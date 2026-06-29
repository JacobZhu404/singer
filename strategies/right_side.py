"""
策略4: 右侧交易（优化版）

原理：等待股票从底部启动，突破关键阻力位后介入
重构（2026-06-03）：
  1. 改为加分制而非层层过滤
  2. 放宽阈值到60分，与其他策略一致
  3. 支持多维度加分

条件（加分项）：
  1. 突破近20日/60日新高 (+20/+30)
  2. 突破有效性验证（>1%）(+10/+15)
  3. 突破幅度合理（1~8%）(+10)
  4. 放量（量比>1.5）(+/+25)
  5. MA5上穿MA20金叉 (+20)
  6. 股价站上MA60 (+15)
  7. RSI在50~70区间 (+10)
  8. MACD零轴以上 (+10)
适用：趋势确认、中线持仓
"""

import pandas as pd
import numpy as np
import logging

from .base import BaseStrategy, StockSignal, _compute_risk_flags, last_vol_ratio
from ..utils.indicators import (
    calc_macd, calc_ma, calc_volume_ratio, calc_rsi
)

logger = logging.getLogger(__name__)


class RightSideTradingStrategy(BaseStrategy):
    name = "right_side"
    description = "右侧交易 - 突破关键阻力位+放量+均线金叉"
    base_win_rate = 0.49  # 30日实测胜率，2026-06-29 全量回测

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
        vr = last_vol_ratio(vol_ratio, i)

        # ── 突破计算 ──
        breakout_20_pct = 0.0
        breakout_60_pct = 0.0
        has_breakout_20 = False
        has_breakout_60 = False
        high_20 = high_60 = None

        if i >= 20:
            high_20 = float(high.iloc[i-20:i].max())
            breakout_20_pct = (c - high_20) / high_20 * 100 if high_20 > 0 else 0
            has_breakout_20 = c > high_20

        if i >= 60:
            high_60 = float(high.iloc[i-60:i].max())
            breakout_60_pct = (c - high_60) / high_60 * 100 if high_60 > 0 else 0
            has_breakout_60 = c > high_60

        # 突破是右侧交易的核心条件——未突破直接淘汰，避免在加分制下被弱信号顶上 top_n
        # 阶段 B 数据：旧实现给"未突破"+5 基础分，导致命中 300（被 top_n 截），与
        # volume_breakout 嵌套率达 93%（vol 几乎完全是 right 的子集）
        if not has_breakout_20 and not has_breakout_60:
            return None

        # 条件1：突破20日/60日新高
        if has_breakout_60:
            signals.append(f"突破60日新高({high_60:.2f})")
            score += 30
            # 突破有效性验证
            if breakout_60_pct > 1.0:
                signals.append(f"60日突破有效({breakout_60_pct:.1f}%)")
                score += 15
            # 突破幅度合理
            if 1.0 <= breakout_60_pct <= 8.0:
                signals.append(f"幅度合理({breakout_60_pct:.1f}%)")
                score += 10
            elif breakout_60_pct > 8.0:
                signals.append(f"幅度过大({breakout_60_pct:.1f}%)")
                score -= 10
        elif has_breakout_20:
            # 没突破60日但突破了20日
            signals.append(f"突破20日新高({high_20:.2f})")
            score += 20
            if breakout_20_pct > 1.0:
                signals.append(f"20日突破有效({breakout_20_pct:.1f}%)")
                score += 10
            if 1.0 <= breakout_20_pct <= 8.0:
                signals.append(f"20日幅度合理({breakout_20_pct:.1f}%)")
                score += 10
            elif breakout_20_pct > 8.0:
                signals.append(f"幅度过大({breakout_20_pct:.1f}%)")
                score -= 5

        # 条件2：放量
        if vr > 2.0:
            signals.append(f"放量(量比{vr:.1f}x)")
            score += 25
        elif vr > 1.5:
            signals.append(f"温和放量(量比{vr:.1f}x)")
            score += 15

        # 条件3：突破前缩量调整
        if i >= 21:
            vol_ma5_before = float(vol.iloc[i-6:i-1].mean())
            vol_ma20_before = float(vol.iloc[i-21:i-1].mean())
            if vol_ma5_before < vol_ma20_before * 0.9:
                signals.append("突破前缩量")
                score += 10

        # 条件4：MA5上穿MA20金叉
        ma5 = mas["ma5"].iloc[i]
        ma20 = mas["ma20"].iloc[i]
        ma5_prev = mas["ma5"].iloc[i-1] if i >= 1 else None
        ma20_prev = mas["ma20"].iloc[i-1] if i >= 1 else None
        
        if (ma5_prev is not None and ma20_prev is not None and
                not pd.isna(ma5_prev) and not pd.isna(ma20_prev) and
                float(ma5) > float(ma20) and float(ma5_prev) <= float(ma20_prev)):
            signals.append("MA5上穿MA20金叉")
            score += 20

        # 条件5：股价站上MA60
        ma60 = mas["ma60"].iloc[i]
        if not pd.isna(ma60) and c > float(ma60):
            signals.append("站上MA60")
            score += 15

        # 条件6：RSI在50~70区间
        r = float(rsi.iloc[i]) if not pd.isna(rsi.iloc[i]) else 50
        if 50 <= r <= 70:
            signals.append(f"RSI强势({r:.0f})")
            score += 10
        elif r > 70:
            signals.append(f"RSI超买({r:.0f})")
            score -= 5

        # 条件7：MACD零轴以上
        if not pd.isna(dif.iloc[i]) and dif.iloc[i] > 0:
            signals.append("MACD零轴上")
            score += 10

        # 阈值收紧 60→80：纯突破至少要叠加放量/金叉等 2 项才能入选，过滤"勉强达标"
        if score < 80:
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
