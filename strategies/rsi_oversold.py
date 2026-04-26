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
from typing import List
import logging

from .base import BaseStrategy, StockSignal, ScreenResult, _compute_risk_flags
from ..utils.indicators import calc_rsi
from ..data.fetcher import market_scanner, get_latest_trade_date

logger = logging.getLogger(__name__)


class RSIOversoldStrategy(BaseStrategy):
    """RSI 超卖策略"""
    name = "rsi_oversold"
    description = "RSI < 30 超卖区域，价格反弹概率高。适用于震荡市抄底。"
    base_win_rate = 0.55

    def __init__(self, top_n: int = 10):
        super().__init__(top_n=top_n)
        self._cache: set = set()

    def screen(self, stock_list: pd.DataFrame, scanner=None) -> ScreenResult:
        if scanner is None:
            scanner = market_scanner
        trade_date = get_latest_trade_date()
        scanner.load()
        name_map = self._get_name_map(stock_list)

        candidates: List[StockSignal] = []
        scanned = 0

        for code in self._get_codes(stock_list):
            if code in self._cache:
                continue
            try:
                kline = scanner.get_history(code, days=60)
                if kline is None or len(kline) < 30:
                    continue

                scanned += 1
                close = kline["close"]
                rsi = calc_rsi(close, 14)
                rsi_val = float(rsi.iloc[-1])
                rsi_prev = float(rsi.iloc[-2]) if len(rsi) >= 2 else rsi_val

                if pd.isna(rsi_val) or rsi_val > 60:
                    continue

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

                ma20 = close.rolling(20).mean()
                if not pd.isna(ma20.iloc[-1]) and close.iloc[-1] < ma20.iloc[-1]:
                    signals.append("价格<20日均线超跌")
                    score += 10

                if score < 40:
                    continue

                quote = self._get_quote(scanner, code, float(close.iloc[-1]))
                # 量比基于K线成交量计算（5日均量为基准），不再用换手率换算
                vol_ma5 = vol.rolling(5).mean().iloc[-1]
                vol_ratio_val = float(vol.iloc[-1] / vol_ma5) if (not pd.isna(vol_ma5) and vol_ma5 > 0) else 1.0

                candidates.append(StockSignal(
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
                ))
                self._cache.add(code)

            except Exception as e:
                logger.debug(f"[RSI策略] {code} 计算失败: {e}")

        return self._build_result(candidates, trade_date, scanned)
