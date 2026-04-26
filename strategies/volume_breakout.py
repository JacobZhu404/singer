"""
量价突破策略
条件：
  1. 量比 > 2倍（明显放量）
  2. 价格突破30日高点
  3. 阳线（今日收涨）
  4. 放量上涨共振
适用：短线启动、题材炒作
"""

import pandas as pd
import numpy as np
from typing import List
import logging

from .base import BaseStrategy, StockSignal, ScreenResult, _compute_risk_flags
from ..data.fetcher import market_scanner, get_latest_trade_date

logger = logging.getLogger(__name__)


class VolumeBreakoutStrategy(BaseStrategy):
    """量价突破策略"""
    name = "volume_breakout"
    description = "量比>2倍 + 价格突破近期高点，视为有效突破信号。"
    base_win_rate = 0.56

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
                high = kline["high"]
                vol = kline["vol"]
                # 用今日最高价（而非收盘价）判断是否突破
                today_high = float(high.iloc[-1])
                price = float(close.iloc[-1])       # 收盘价用于收阳判断
                prev_price = float(close.iloc[-2])

                vol_ma20 = vol.rolling(20).mean().iloc[-1]
                if pd.isna(vol_ma20) or vol_ma20 <= 0:
                    continue

                vol_ratio = vol.iloc[-1] / vol_ma20
                vol_ratio_prev = vol.iloc[-2] / vol.iloc[-7:-2].mean() if len(vol) >= 7 else 1.0
                # 突破判断用最高价，而非收盘价
                high_30 = high.iloc[-31:-1].max() if len(high) >= 31 else high.iloc[:-1].max()
                high_5 = high.iloc[-6:-1].max() if len(high) >= 6 else high.iloc[:-1].max()

                signals = []
                score = 0

                if vol_ratio >= 2.0:
                    signals.append(f"量比{vol_ratio:.1f}倍放量")
                    score += 35
                elif vol_ratio >= 1.5:
                    signals.append(f"温和放量{vol_ratio:.1f}倍")
                    score += 20

                if today_high > high_30:
                    signals.append(f"突破30日高点({round(high_30, 2)})")
                    score += 30
                    if today_high > high_5:
                        signals.append("突破近期新高")
                        score += 15

                if price > prev_price:
                    signals.append("今日收阳")
                    score += 10

                price_pct_chg = (price - prev_price) / prev_price * 100
                if vol_ratio >= 1.5 and price_pct_chg > 2:
                    signals.append("放量上涨共振")
                    score += 15
                if vol_ratio >= 1.5 and vol_ratio_prev >= 1.3:
                    signals.append("量能持续放大")
                    score += 10

                if score < 40:
                    continue

                quote = self._get_quote(scanner, code, price)
                candidates.append(StockSignal(
                    ts_code=code,
                    name=name_map.get(code, code),
                    strategy=self.name,
                    score=min(score, 100),
                    win_rate=self._calc_win_rate(score, signals),
                    signals=signals,
                    latest_price=float(quote.get("最新价", price)),
                    pct_chg=float(quote.get("涨跌幅", 0.0)),
                    volume_ratio=float(vol_ratio),
                    risk_flags=_compute_risk_flags(kline),
                    trade_date=trade_date,
                    extra={
                        "vol_ratio": round(float(vol_ratio), 2),
                        "vol_ma20": round(float(vol_ma20), 0),
                        "high_30": round(float(high_30), 2),
                    },
                ))
                self._cache.add(code)

            except Exception as e:
                logger.debug(f"[量价突破] {code} 计算失败: {e}")

        return self._build_result(candidates, trade_date, scanned)
