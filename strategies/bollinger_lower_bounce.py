"""
布林带·下轨反弹

均值回归：价格触及布林带下轨区域后反弹。

触发门槛：`lower_touch_pct ≤ 5%`，与 `bollinger_breakout` 互斥分工——上方区域的票留给后者。

评分：
  - 触及下轨（≤+3%） +40
  - 当根反弹（昨贴下轨、今上行） +30
  - 昨触下轨今脱离（V 型起步） +20
  - 缩量止跌（vol_ratio<0.6） +10
"""

import pandas as pd

from .base import BaseStrategy, StockSignal, _compute_risk_flags


class BollingerLowerBounceStrategy(BaseStrategy):
    name = "bollinger_lower_bounce"
    description = "价格触及布林下轨后反弹（均值回归）"
    base_win_rate = 0.48  # 30日实测胜率，2026-06-29 全量回测

    LOWER_BAND_ZONE_PCT = 5.0  # 价位在下轨上方 ≤5% 才进入该策略

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date):
        indicators = scanner.get_indicators(code, days=120)
        if not indicators or len(indicators["kline"]) < 30:
            raise self._SkipStock()

        kline = indicators["kline"]
        close = kline["close"]
        bb = indicators["bollinger"]
        lower = bb["lower"]
        price = float(close.iloc[-1])
        prev_price = float(close.iloc[-2])
        lower_band = float(lower.iloc[-1])
        upper_band = float(bb["upper"].iloc[-1])
        mid_band = float(bb["mid"].iloc[-1])

        if pd.isna(lower_band) or pd.isna(upper_band) or lower_band == 0:
            raise self._SkipStock()

        ma60 = indicators["ma"].get("ma60")
        if ma60 is not None and not pd.isna(ma60.iloc[-1]) and price < ma60.iloc[-1] * 0.90:
            return None

        lower_touch_pct = (price - lower_band) / lower_band * 100
        if lower_touch_pct > self.LOWER_BAND_ZONE_PCT:
            return None

        vol_ratio_series = indicators["vol_ratio"]
        vol_ratio_val = float(vol_ratio_series.iloc[-1]) if not pd.isna(vol_ratio_series.iloc[-1]) else 1.0

        signals = []
        score = 0

        if lower_touch_pct <= 3:
            signals.append(f"触及布林下轨({lower_touch_pct:.1f}%)")
            score += 40

        if prev_price <= lower_band * 1.02 and price > prev_price:
            signals.append("布林下轨反弹")
            score += 30

        prev_lower = float(lower.iloc[-2]) if not pd.isna(lower.iloc[-2]) else lower_band
        if prev_lower > 0:
            prev_lower_touch = (float(close.iloc[-2]) - prev_lower) / prev_lower * 100
            if prev_lower_touch <= 1 and lower_touch_pct > 3:
                signals.append("昨触布林下轨反弹")
                score += 20

        if vol_ratio_val < 0.6:
            signals.append("缩量止跌")
            score += 10

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
