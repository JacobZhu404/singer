"""
策略4: 右侧交易（优化版）

原理：等待股票从底部启动，突破关键阻力位后介入
优化（2026-05-14）：
  1. 调整评分权重（60日突破加分更多：30分）
  2. 加入突破有效性验证（突破幅度>1%）
  3. 加入突破幅度过滤（1%~8%为有效突破）

条件：
  1. 突破近20日高点（或60日高点）
  2. 突破时放量（量比 > 1.5）
  3. 突破前有明显调整（非追高）
  4. MA5 上穿 MA20（均线金叉）
  5. 股价站上 MA60（长期趋势向上）
  6. RSI 在 50~70 区间（强势但未超买）
  7. 突破有效性验证（突破幅度 > 1%）
  8. 突破幅度过滤（1%~8%为有效突破）
适用：趋势确认、中线持仓
"""

import pandas as pd
import numpy as np
import logging

from .base import BaseStrategy, StockSignal, _compute_risk_flags
from ..utils.indicators import (
    calc_macd, calc_ma, calc_volume_ratio, calc_rsi
)

logger = logging.getLogger(__name__)


class RightSideTradingStrategy(BaseStrategy):
    name = "right_side"
    description = "右侧交易(优化) - 突破关键阻力位+放量+均线金叉+突破有效性验证"
    base_win_rate = 0.58  # 优化：提高胜率预估

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date):
        indicators = scanner.get_indicators(code, days=120)
        if not indicators or len(indicators["kline"]) < 60:
            raise self._SkipStock()

        df = indicators["kline"]
        close = df["close"]
        high = df["high"]
        vol = df["vol"]
        i = len(df) - 1

        mas = indicators["ma"]
        vol_ratio = indicators["vol_ratio"]
        rsi = indicators["rsi"]
        dif, dea, _ = indicators["macd"]

        signals = []
        score = 0
        c = float(close.iloc[i])
        vr = float(vol_ratio.iloc[i]) if not pd.isna(vol_ratio.iloc[i]) else 1.0

        # ── 条件1&2: 突破20日/60日新高 ──
        # 合并计算避免重复，60日突破已隐含20日突破，只取更高分
        breakout_20_pct = 0.0
        breakout_60_pct = 0.0
        has_breakout_60 = False
        high_60 = None
        if i >= 60:
            high_60 = float(high.iloc[i-60:i].max())
            has_breakout_60 = c > high_60
            breakout_60_pct = (c - high_60) / high_60 * 100

        if i >= 20:
            high_20 = float(high.iloc[i-20:i].max())
            breakout_20_pct = (c - high_20) / high_20 * 100

            # 只突破20日但未突破60日才单独加分
            if c > high_20 and not has_breakout_60:
                signals.append(f"突破20日新高({high_20:.2f})")
                score += 20

                # 优化2：突破有效性验证（突破幅度>1%）
                if breakout_20_pct > 1.0:
                    signals.append(f"20日突破有效({breakout_20_pct:.1f}%)")
                    score += 10

                # 优化3：突破幅度过滤（1%~8%为有效突破）
                if 1.0 <= breakout_20_pct <= 8.0:
                    signals.append(f"20日突破幅度合理({breakout_20_pct:.1f}%)")
                    score += 10
                elif breakout_20_pct > 8.0:
                    signals.append(f"20日突破幅度过大({breakout_20_pct:.1f}%)")
                    score -= 10  # 可能已过热的，降低评分

        # 60日高点突破（核心条件）
        if has_breakout_60:
            signals.append(f"突破60日新高({high_60:.2f})")
            score += 30  # 优化：10→30（60日突破更强）

            # 优化2：突破有效性验证（突破幅度>1%）
            if breakout_60_pct > 1.0:
                signals.append(f"60日突破有效({breakout_60_pct:.1f}%)")
                score += 15

            # 优化3：突破幅度过滤（1%~8%为有效突破）
            if 1.0 <= breakout_60_pct <= 8.0:
                signals.append(f"60日突破幅度合理({breakout_60_pct:.1f}%)")
                score += 10
            elif breakout_60_pct > 8.0:
                signals.append(f"60日突破幅度过大({breakout_60_pct:.1f}%)")
                score -= 15  # 可能已过热的，大幅降低评分
        elif not (c > high_20 and breakout_20_pct > 1.0):
            # 没有有效突破（20日或60日），不是右侧交易信号，直接过滤
            return None

        # ── 条件3: 突破时放量 ──
        if vr > 2.0:
            signals.append(f"突破放量(量比{vr:.1f}x)")
            score += 25  # 优化：20→25（放量更重要）
        elif vr > 1.5:
            signals.append(f"突破温和放量(量比{vr:.1f}x)")
            score += 15

        # ── 条件4: 突破前缩量调整（过滤假突破）──
        if i >= 21:
            vol_ma5_before = float(vol.iloc[i-6:i-1].mean())
            vol_ma20_before = float(vol.iloc[i-21:i-1].mean())
            if vol_ma5_before < vol_ma20_before * 0.9:
                signals.append("突破前缩量调整")
                score += 10

        # ── 条件5: MA5 上穿 MA20（均线金叉）──
        ma5 = mas["ma5"].iloc[i]
        ma20 = mas["ma20"].iloc[i]
        ma5_prev = mas["ma5"].iloc[i-1] if i >= 1 else None
        ma20_prev = mas["ma20"].iloc[i-1] if i >= 1 else None
        
        if (ma5_prev is not None and ma20_prev is not None and
                not pd.isna(ma5_prev) and not pd.isna(ma20_prev) and
                float(ma5) > float(ma20) and float(ma5_prev) <= float(ma20_prev)):
            signals.append("MA5上穿MA20金叉")
            score += 20

        # ── 条件6: 股价站上 MA60（长期趋势向上）──
        ma60 = mas["ma60"].iloc[i]
        if not pd.isna(ma60) and c > float(ma60):
            signals.append("股价站上MA60")
            score += 15

        # ── 条件7: RSI 在 50~70 区间（强势但未超买）──
        r = float(rsi.iloc[i]) if not pd.isna(rsi.iloc[i]) else 50
        if 50 <= r <= 70:
            signals.append(f"RSI强势区间({r:.0f})")
            score += 10
        elif r > 70:
            signals.append(f"RSI超买({r:.0f})")
            score -= 10

        # ── 条件8: MACD 零轴以上（趋势确认）──
        if not pd.isna(dif.iloc[i]) and dif.iloc[i] > 0:
            signals.append("MACD零轴以上")
            score += 10

        # ── 阈值过滤 ──
        if score < 90:  # 收紧：60→90，控制命中数
            return None

        quote = self._get_quote(scanner, code, c)
        return StockSignal(
            ts_code=code,
            name=name_map.get(code, code),
            strategy=self.name,
            score=min(score, 100),
            win_rate=None,
            signals=signals,
            latest_price=round(float(quote.get("最新价", c)), 2),
            pct_chg=round(float(quote.get("涨跌幅", 0.0)), 2),
            volume_ratio=round(vr, 2),
            risk_flags=_compute_risk_flags(df),
            trade_date=trade_date,
            extra={
                "ma5": round(float(ma5), 2),
                "ma20": round(float(ma20), 2),
                "ma60": round(float(ma60), 2) if not pd.isna(ma60) else None,
                "rsi14": round(r, 1),
                "breakout_20_pct": round(breakout_20_pct, 2) if i >= 20 else None,
                "breakout_60_pct": round(breakout_60_pct, 2) if i >= 60 else None,
            }
        )
