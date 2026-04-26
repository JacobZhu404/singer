"""
布林带下轨反弹策略
条件：
  1. 价格触及布林带下轨（≤下轨 + 3%）
  2. 布林下轨反弹（价格开始上行）
  3. 缩量止跌（量能萎缩）
适用：震荡市、低位埋伏
"""

import pandas as pd
import numpy as np
from typing import List
import logging

from .base import BaseStrategy, StockSignal, ScreenResult, _compute_risk_flags
from ..utils.indicators import calc_bollinger, calc_volume_ratio
from ..data.fetcher import market_scanner, get_latest_trade_date

logger = logging.getLogger(__name__)


class BollingerBandsStrategy(BaseStrategy):
    """布林带下轨反弹策略"""
    name = "bollinger_bands"
    description = "价格触及布林带下轨+反弹，暗示均值回归。适用于震荡市低位埋伏。"
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
                kline = scanner.get_history(code, days=80)
                if kline is None or len(kline) < 30:
                    continue

                scanned += 1
                close = kline["close"]
                vol = kline["vol"]

                upper, mid, lower = calc_bollinger(close, 20, 2.0)
                price = close.iloc[-1]
                prev_price = close.iloc[-2]
                lower_band = lower.iloc[-1]
                upper_band = upper.iloc[-1]

                if pd.isna(lower_band) or pd.isna(upper_band) or lower_band == 0:
                    continue

                signals = []
                score = 0
                lower_touch_pct = (price - lower_band) / lower_band * 100

                if lower_touch_pct <= 3:
                    signals.append(f"触及布林下轨({lower_touch_pct:.1f}%)")
                    score += 40
                if prev_price <= lower_band * 1.02 and price > prev_price:
                    signals.append("布林下轨反弹")
                    score += 30

                prev_lower_touch = (close.iloc[-2] - lower.iloc[-2]) / lower.iloc[-2] * 100
                if prev_lower_touch <= 1 and lower_touch_pct > 3:
                    signals.append("昨触布林下轨反弹")
                    score += 20

                vol_ratio_series = calc_volume_ratio(vol, 5)
                vol_ratio_val = float(vol_ratio_series.iloc[-1]) if not pd.isna(vol_ratio_series.iloc[-1]) else 1.0
                if vol_ratio_val < 0.6:
                    signals.append("缩量止跌")
                    score += 10

                if score < 40:
                    continue

                quote = self._get_quote(scanner, code, float(price))

                candidates.append(StockSignal(
                    ts_code=code,
                    name=name_map.get(code, code),
                    strategy=self.name,
                    score=min(score, 100),
                    win_rate=self._calc_win_rate(score, signals),
                    signals=signals,
                    latest_price=float(quote.get("最新价", price)),
                    pct_chg=float(quote.get("涨跌幅", 0.0)),
                    volume_ratio=round(vol_ratio_val, 2),
                    risk_flags=_compute_risk_flags(kline),
                    trade_date=trade_date,
                    extra={
                        "lower_band": round(float(lower_band), 2),
                        "upper_band": round(float(upper_band), 2),
                        "bollinger_width": round(float(upper_band - lower_band), 2),
                        "price": round(float(price), 2),
                    },
                ))
                self._cache.add(code)

            except Exception as e:
                logger.debug(f"[布林策略] {code} 计算失败: {e}")

        return self._build_result(candidates, trade_date, scanned)
