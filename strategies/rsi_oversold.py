"""
RSI 超卖策略
条件：
  1. RSI < 30 超卖区域
  2. RSI 从底部回升（从 <30 到 ≥30）
  3. RSI < 40 低位区域
  4. 价格 < 20日均线（超跌辅助）
适用：震荡市、抄底信号
"""

import pandas as pd
import numpy as np
import logging

from .base import BaseStrategy, StockSignal, _compute_risk_flags
from ..utils.indicators import calc_rsi, calc_volume_ratio

logger = logging.getLogger(__name__)


class RSIOversoldStrategy(BaseStrategy):
    """RSI 超卖策略"""
    name = "rsi_oversold"
    description = "RSI < 30 超卖区域，价格反弹概率高。适用于震荡市抄底。"
    base_win_rate = 0.55

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date):
        try:
            indicators = scanner.get_indicators(code, days=120)
            if not indicators or len(indicators["kline"]) < 30:
                raise self._SkipStock()

            kline = indicators["kline"]
            close = kline["close"]
            rsi = indicators["rsi"]
            ma20 = indicators["ma"]["ma20"]
            vol_ratio_series = indicators["vol_ratio"]
            rsi_val = float(rsi.iloc[-1])
            rsi_prev = float(rsi.iloc[-2]) if len(rsi) >= 2 else rsi_val

            if pd.isna(rsi_val):
                raise self._SkipStock()
            if rsi_val > 60:
                return None

            signals = []
            score = 0

            if rsi_val < 30:
                signals.append(f"RSI({rsi_val:.0f})<30超卖")
                score += 50
            elif rsi_prev < 30 <= rsi_val < 40:
                signals.append("RSI底部回升")
                score += 25
            elif 30 <= rsi_val < 40:
                signals.append(f"RSI({rsi_val:.0f})低位")
                score += 15

            if not pd.isna(ma20.iloc[-1]) and close.iloc[-1] < ma20.iloc[-1]:
                signals.append("价格<20日均线超跌")
                score += 10

            if score < 45:
                return None

            quote = self._get_quote(scanner, code, float(close.iloc[-1]))
            vol_ratio_val = float(vol_ratio_series.iloc[-1]) if not pd.isna(vol_ratio_series.iloc[-1]) else 1.0

            return StockSignal(
                ts_code=code,
                name=name_map.get(code, code),
                strategy=self.name,
                score=min(score, 100),
                win_rate=self._calc_win_rate(score, signals),
                signals=signals,
                latest_price=float(quote.get("最新价", close.iloc[-1])),
                pct_chg=float(quote.get("涨跌幅", 0.0)),
                volume_ratio=vol_ratio_val,
                risk_flags=_compute_risk_flags(kline),
                trade_date=trade_date,
                extra={"rsi": round(rsi_val, 1)},
            )

        except Exception as e:
            logger.debug(f"[RSI策略] {code} 计算失败: {e}")