"""
布林带·收口突破

波动率突破：布林带 10 日收口后放量突破中轨 / 逼近上轨，捕捉大行情起步。

触发门槛：`lower_touch_pct > 5%`，与 `bollinger_lower_bounce` 互斥分工——下轨区域的票
留给后者。

评分：
  - 布林带收口（≈10日最低宽 ×1.05 内） +25
  - 放量（vol_ratio>1.5/+1.2） +20/+10
  - 突破中轨 +15
  - 接近上轨（≥0.98×upper） +30
"""

import pandas as pd

from .base import BaseStrategy, StockSignal, _compute_risk_flags


class BollingerBreakoutStrategy(BaseStrategy):
    name = "bollinger_breakout"
    description = "布林带 10 日收口 + 放量突破中轨 / 逼近上轨（波动率扩张）"
    base_win_rate = 0.48  # 30日实测胜率，2026-06-29 全量回测

    LOWER_BAND_ZONE_PCT = 5.0  # 与下轨反弹互斥的同一门槛

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date):
        indicators = scanner.get_indicators(code, days=120)
        if not indicators or len(indicators["kline"]) < 30:
            raise self._SkipStock()

        kline = indicators["kline"]
        close = kline["close"]
        bb = indicators["bollinger"]
        upper = bb["upper"]
        lower = bb["lower"]
        price = float(close.iloc[-1])
        lower_band = float(lower.iloc[-1])
        upper_band = float(upper.iloc[-1])
        mid_band = float(bb["mid"].iloc[-1])

        if pd.isna(lower_band) or pd.isna(upper_band) or lower_band == 0:
            raise self._SkipStock()

        ma60 = indicators["ma"].get("ma60")
        if ma60 is not None and not pd.isna(ma60.iloc[-1]) and price < ma60.iloc[-1] * 0.90:
            return None

        lower_touch_pct = (price - lower_band) / lower_band * 100
        if lower_touch_pct <= self.LOWER_BAND_ZONE_PCT:
            return None

        upper_touch_pct = (price - upper_band) / upper_band * 100
        vol_ratio_series = indicators["vol_ratio"]
        vol_ratio_val = float(vol_ratio_series.iloc[-1]) if not pd.isna(vol_ratio_series.iloc[-1]) else 1.0

        signals = []
        score = 0

        bb_width = upper_band - lower_band
        bb_width_series = upper - lower
        if len(bb_width_series) >= 10:
            min_width_10d = float(bb_width_series.iloc[-10:].min())
            if bb_width < min_width_10d * 1.05:
                signals.append("布林带收口")
                score += 25

        if vol_ratio_val > 1.5:
            signals.append(f"放量({vol_ratio_val:.1f}倍)")
            score += 20
        elif vol_ratio_val > 1.2:
            signals.append(f"量能温和({vol_ratio_val:.1f}倍)")
            score += 10

        if price > mid_band:
            signals.append("突破布林中轨")
            score += 15

        if upper_band > 0 and upper_touch_pct >= -2:
            signals.append("接近布林上轨")
            score += 30

        if score < 60:
            return None

        quote = self._get_quote(scanner, code, float(price))
        return StockSignal(
            ts_code=code,
            name=name_map.get(code, code),
            strategy=self.name,
            score=min(score, 100),
            win_rate=None,
            signals=signals,
            latest_price=float(quote.get("最新价", price)),
            pct_chg=float(quote.get("涨跌幅", 0.0)),
            volume_ratio=round(vol_ratio_val, 2),
            risk_flags=_compute_risk_flags(kline),
            trade_date=trade_date,
            extra={
                "lower_band": round(lower_band, 2),
                "upper_band": round(upper_band, 2),
                "mid_band": round(mid_band, 2),
                "bollinger_width": round(upper_band - lower_band, 2),
                "price": round(float(price), 2),
            },
        )
