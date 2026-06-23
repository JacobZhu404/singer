"""
RSI 超卖策略（优化版）

优化（2026-05-14）：
  1. 加入趋势过滤（MA20<MA60时降低评分）
  2. 加入RSI背离检测（价格新低但RSI没有新低=看涨背离）
  3. 提高阈值45→55

条件：
  1. RSI < 30 超卖区域
  2. RSI 从底部回升（从 <30 到 ≥30）
  3. RSI < 40 低位区域
  4. 价格 < 20日均线（超跌辅助）
  5. RSI背离检测（看涨背离=底部信号）
  6. 趋势过滤（避免下跌趋势中抄底）
适用：震荡市、抄底信号
"""

import pandas as pd
import numpy as np
import logging

from .base import BaseStrategy, StockSignal, _compute_risk_flags
from ..utils.indicators import calc_rsi, calc_volume_ratio

logger = logging.getLogger(__name__)


class RSIOversoldStrategy(BaseStrategy):
    """RSI 超卖策略（优化版）"""
    name = "rsi_oversold"
    description = "RSI < 30 超卖+RSI背离检测+趋势过滤，震荡市抄底"
    base_win_rate = 0.58  # 优化：提高胜率预估

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date):
        indicators = scanner.get_indicators(code, days=120)
        if not indicators or len(indicators["kline"]) < 30:
            raise self._SkipStock()

        kline = indicators["kline"]
        close = kline["close"]
        rsi = indicators["rsi"]
        ma20 = indicators["ma"]["ma20"]
        ma60 = indicators["ma"].get("ma60")
        vol_ratio_series = indicators["vol_ratio"]

        rsi_val = float(rsi.iloc[-1])
        rsi_prev = float(rsi.iloc[-2]) if len(rsi) >= 2 else rsi_val

        if pd.isna(rsi_val):
            raise self._SkipStock()
        if rsi_val > 60:  # 优化：降低过滤阈值（原70）
            return None

        signals = []
        score = 0

        # ── 条件1: RSI < 30 超卖区域 ──
        if rsi_val < 30:
            signals.append(f"RSI({rsi_val:.0f})<30超卖")
            score += 50
        elif rsi_prev < 30 <= rsi_val < 40:
            signals.append("RSI底部回升")
            score += 25
        elif 30 <= rsi_val < 40:
            signals.append(f"RSI({rsi_val:.0f})低位")
            score += 15

        # ── 条件2: 价格 < 20日均线（超跌辅助）──
        if not pd.isna(ma20.iloc[-1]) and close.iloc[-1] < ma20.iloc[-1]:
            signals.append("价格<20日均线超跌")
            score += 10

        # ── 优化2: 加入RSI背离检测 ──
        # 看涨背离：价格在近10日新低，但RSI没有新低
        if len(close) >= 10:
            price_now = close.iloc[-1]
            price_low_10d = close.iloc[-10:].min()

            # 价格在近10日新低（或接近新低）
            if price_now <= price_low_10d * 1.02:  # 2%以内视为新低
                rsi_now = rsi_val
                rsi_low_10d = rsi.iloc[-10:].min()

                # RSI没有新低（背离）
                if rsi_now > rsi_low_10d:
                    signals.append("RSI看涨背离")
                    score += 30  # 强信号

        # ── 优化1: 加入趋势过滤 ──
        # 如果在下跌趋势中（MA20 < MA60），降低评分
        trend_penalty = 0
        if ma60 is not None and not pd.isna(ma60.iloc[-1]) and not pd.isna(ma20.iloc[-1]):
            if ma20.iloc[-1] < ma60.iloc[-1]:
                signals.append("下跌趋势中(MA20<MA60)")
                trend_penalty = 15
                score -= trend_penalty

        # 阈值收紧：55→60，确保超卖信号质量
        if score < 60:
            return None

        quote = self._get_quote(scanner, code, float(close.iloc[-1]))
        vol_ratio_val = float(vol_ratio_series.iloc[-1]) if not pd.isna(vol_ratio_series.iloc[-1]) else 1.0

        return StockSignal(
            ts_code=code,
            name=name_map.get(code, code),
            strategy=self.name,
            score=min(score, 100),
            win_rate=None,
            signals=signals,
            latest_price=float(quote.get("最新价", close.iloc[-1])),
            pct_chg=float(quote.get("涨跌幅", 0.0)),
            volume_ratio=vol_ratio_val,
            risk_flags=_compute_risk_flags(kline),
            trade_date=trade_date,
            extra={
                "rsi": round(rsi_val, 1),
                "trend_penalty": trend_penalty,
            },
        )
