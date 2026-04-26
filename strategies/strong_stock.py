"""
策略2: 强势股选股
条件（综合评分，需满足大部分）：
  1. 放量上涨（量比 > 1.5）
  2. 红肥绿瘦：涨时成交量大、跌时成交量小（近10日）
  3. 五连小阳：连续5日小阳线
  4. 跳空缺口：今日最低 > 昨日最高
  5. MACD 零轴以上：DIF > 0
"""

import pandas as pd
import numpy as np
import logging

from .base import BaseStrategy, StockSignal, ScreenResult, _compute_risk_flags
from ..utils.indicators import (
    calc_macd, calc_volume_ratio,
    is_red_candle, detect_gap_up
)
from ..data.fetcher import market_scanner, get_latest_trade_date

logger = logging.getLogger(__name__)


class StrongStockStrategy(BaseStrategy):
    name = "strong_stock"
    description = "强势股 - 放量红肥绿瘦+五连小阳+跳空缺口+MACD零轴以上"
    base_win_rate = 0.62

    def screen(self, stock_list: pd.DataFrame, scanner=None) -> ScreenResult:
        if scanner is None:
            scanner = market_scanner
        trade_date = get_latest_trade_date()
        scanner.load()
        name_map = self._get_name_map(stock_list)

        candidates = []
        scanned = 0

        for code in self._get_codes(stock_list):
            try:
                df = scanner.get_history(code, days=30)
                if df is None or len(df) < 10:
                    continue

                scanned += 1
                close = df["close"]
                open_ = df["open"]
                high = df["high"]
                low = df["low"]
                vol = df["vol"]
                pct_chg = close.pct_change() * 100

                signals = []
                score = 0

                vol_ratio = calc_volume_ratio(vol, 5)
                red = is_red_candle(open_, close)
                dif, dea, macd_bar = calc_macd(close)
                gap_up = detect_gap_up(high, low, open_, close)
                i = len(df) - 1

                if not pd.isna(vol_ratio.iloc[i]) and vol_ratio.iloc[i] > 1.5 and red.iloc[i]:
                    signals.append(f"放量上涨(量比{vol_ratio.iloc[i]:.1f}x)")
                    score += 20

                n = min(10, i + 1)
                up_vol = down_vol = 0.0
                for j in range(max(0, i - n + 1), i + 1):
                    if pd.isna(red.iloc[j]):
                        continue
                    v = float(vol.iloc[j])
                    (up_vol, down_vol)[not red.iloc[j]] += v
                if down_vol > 0 and up_vol / (down_vol + 1e-8) > 1.5:
                    signals.append(f"红肥绿瘦(涨缩量比{up_vol/down_vol:.1f})")
                    score += 20

                if i >= 4 and all(red.iloc[i - k] for k in range(5)) and \
                   all(not pd.isna(pct_chg.iloc[i - k]) and
                       abs(float(pct_chg.iloc[i - k])) <= 3.0
                       for k in range(5)):
                    signals.append("五连小阳")
                    score += 20

                if gap_up.iloc[i]:
                    signals.append("跳空缺口(今低>昨高)")
                    score += 20

                if not pd.isna(dif.iloc[i]) and dif.iloc[i] > 0:
                    signals.append("MACD零轴以上")
                    score += 20

                if score < 40:
                    continue

                latest = close.iloc[i]
                vr = float(vol_ratio.iloc[i]) if not pd.isna(vol_ratio.iloc[i]) else 1.0
                quote = self._get_quote(scanner, code, float(latest))

                candidates.append(StockSignal(
                    ts_code=code,
                    name=name_map.get(code, code),
                    strategy=self.name,
                    score=score,
                    win_rate=self._calc_win_rate(score, signals),
                    signals=signals,
                    latest_price=round(float(quote.get("最新价", latest)), 2),
                    pct_chg=round(float(quote.get("涨跌幅", 0.0)), 2),
                    volume_ratio=round(vr, 2),
                    risk_flags=_compute_risk_flags(df),
                    trade_date=trade_date,
                    extra={
                        "gap_up": bool(gap_up.iloc[i]),
                        "dif": round(float(dif.iloc[i]), 4) if not pd.isna(dif.iloc[i]) else None,
                        "up_down_vol_ratio": round(up_vol / (down_vol + 1e-8), 2),
                    }
                ))

            except Exception as e:
                logger.debug(f"[强势股策略] {code} 计算失败: {e}")

        return self._build_result(candidates, trade_date, scanned)
