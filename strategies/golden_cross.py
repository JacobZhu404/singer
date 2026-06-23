"""
均线金叉策略（趋势启动初期）

定位：趋势启动初期，适合短线波段
与 macd_bull 的区别：
  - golden_cross: 趋势启动（金叉当天，最早信号）
  - macd_bull: 趋势确认（金叉后+均线多头，已确立）

核心逻辑：
  1. MA5 上穿 MA10（金叉当天，核心信号）
  2. MA5 > MA10 > MA20（三线多头排列）
  3. 成交量确认（量比 > 1.5，避免假突破）
  4. RSI 未超买（45~65，趋势确认但未过热）

信号评分：
  - MA5上穿MA10金叉：+40（核心信号）
  - 三线多头排列：+25
  - 成交量放大（量比>1.5）：+15
  - 价格站上MA5：+10
  - RSI趋势确认（45~65）：+10
适用：趋势启动初期、短线波段（比 macd_bull 更早入场）
"""

import pandas as pd
import numpy as np
from typing import List
import logging

from .base import BaseStrategy, StockSignal, _compute_risk_flags

logger = logging.getLogger(__name__)


class GoldenCrossStrategy(BaseStrategy):
    """均线金叉策略（趋势启动初期：金叉+量能确认）"""
    name = "golden_cross"
    description = "MA5上穿MA10金叉+三线多头+量能确认，趋势启动信号"
    base_win_rate = 0.52  # 比 macd_bull 低（更早入场，假信号更多）

    def __init__(self, top_n: int = 10):
        super().__init__(top_n=top_n)

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date):
        indicators = scanner.get_indicators(code, days=120)
        if not indicators or len(indicators["kline"]) < 30:
            raise self._SkipStock()

        df = indicators["kline"]
        close = df["close"]
        i = len(df) - 1

        mas = indicators["ma"]
        ma5 = mas["ma5"]
        ma10 = mas["ma10"]
        ma20 = mas["ma20"]
        rsi = indicators["rsi"]
        vol_ratio = indicators["vol_ratio"]

        m5 = float(ma5.iloc[i])
        m10 = float(ma10.iloc[i])
        m20 = float(ma20.iloc[i])
        m5_prev = float(ma5.iloc[i-1]) if i >= 1 else m5
        m10_prev = float(ma10.iloc[i-1]) if i >= 1 else m10

        if any(np.isnan(x) for x in [m5, m10, m20]):
            raise self._SkipStock()

        signals = []
        score = 0

        # ── 核心条件1：MA5 上穿 MA10（金叉当天）──
        if m5 > m10 and m5_prev <= m10_prev:
            signals.append("MA5上穿MA10金叉")
            score += 40
        else:
            # 非金叉当天，不是"趋势启动"，直接过滤
            return None

        # ── 条件2：三线多头排列（MA5 > MA10 > MA20）──
        if m5 > m10 > m20:
            signals.append("三线多头排列")
            score += 25

        # ── 条件3：成交量确认（量比 > 1.5）──
        vr = float(vol_ratio.iloc[i]) if not np.isnan(vol_ratio.iloc[i]) else 1.0
        if vr > 1.5:
            signals.append(f"放量({vr:.1f}倍)")
            score += 15
        else:
            # 量能不足，可能是假突破
            signals.append(f"量能不足({vr:.1f}倍)")
            score -= 10

        # ── 条件4：价格站上MA5 ──
        c = float(close.iloc[i])
        if c > m5:
            signals.append("价格站上MA5")
            score += 10

        # ── 条件5：RSI 确认（未超买）──
        r = float(rsi.iloc[i]) if not np.isnan(rsi.iloc[i]) else 50
        if 45 <= r <= 65:
            signals.append(f"RSI趋势确认({r:.0f})")
            score += 10
        elif r > 70:
            signals.append(f"RSI超买({r:.0f})")
            score -= 15
        elif r < 45:
            signals.append(f"RSI偏弱({r:.0f})")
            score -= 10

        # 阈值检查（收紧：60→65，控制命中数）
        if score < 65:
            return None

        quote = self._get_quote(scanner, code, c)

        return StockSignal(
            ts_code=code,
            name=name_map.get(code, code),
            strategy=self.name,
            score=min(max(score, 0), 100),
            win_rate=None,
            signals=signals,
            latest_price=float(quote.get("最新价", c)),
            pct_chg=float(quote.get("涨跌幅", 0.0)),
            volume_ratio=round(vr, 2),
            risk_flags=_compute_risk_flags(df),
            trade_date=trade_date,
            extra={
                "ma5": round(m5, 2),
                "ma10": round(m10, 2),
                "ma20": round(m20, 2),
                "rsi14": round(r, 1),
                "volume_ratio": round(vr, 2),
            },
        )
