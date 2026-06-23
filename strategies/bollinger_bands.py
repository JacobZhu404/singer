"""
布林带策略（下轨反弹 + 波动率突破，互斥双模式）

定位：两种模式按价格位置互斥触发
  1. 下轨反弹：价格在布林带下轨区域（≤下轨+5%），均值回归
  2. 波动率突破：布林带收口（10日最低宽）+ 放量 + 突破中轨，捕捉大行情

与 volume_breakout 的区别：
  - volume_breakout: 基于历史价格突破
  - bollinger_bands: 基于波动率/位置

模式 1（下轨反弹）评分：
  - 触及下轨（≤+3%）+40
  - 当根反弹（昨贴下轨、今上行）+30
  - 昨触下轨今脱离 +20（独立项）
  - 缩量止跌（vol_ratio<0.6）+10

模式 2（波动率突破）评分：
  - 布林带收口（≈10日最低宽）+25
  - 放量（vol_ratio>1.5/+1.2）+20/+10
  - 突破中轨 +15
  - 接近上轨（≥0.98×upper）+30

阶段 C 重构：原版同时评 mode1+mode2，逻辑矛盾；改为按价格位置互斥
"""

import pandas as pd
import numpy as np
import logging

from .base import BaseStrategy, StockSignal, _compute_risk_flags

logger = logging.getLogger(__name__)


class BollingerBandsStrategy(BaseStrategy):
    """布林带策略（下轨反弹 + 波动率突破，互斥双模式）"""
    name = "bollinger_bands"
    description = "下轨反弹/波动率突破（互斥），均值回归或捕捉大行情"
    base_win_rate = 0.55

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date):
        indicators = scanner.get_indicators(code, days=120)
        if not indicators or len(indicators["kline"]) < 30:
            raise self._SkipStock()

        kline = indicators["kline"]
        close = kline["close"]
        bb = indicators["bollinger"]
        upper = bb["upper"]
        mid = bb["mid"]
        lower = bb["lower"]
        vol_ratio_series = indicators["vol_ratio"]
        price = float(close.iloc[-1])
        prev_price = float(close.iloc[-2])
        lower_band = float(lower.iloc[-1])
        upper_band = float(upper.iloc[-1])
        mid_band = float(mid.iloc[-1])

        if pd.isna(lower_band) or pd.isna(upper_band) or lower_band == 0:
            raise self._SkipStock()

        # 趋势过滤：用 MA60 兜底极端下跌（MA20 即布林中轨，触下轨时必远低于 MA20，无法过滤）
        ma60 = indicators["ma"].get("ma60")
        if ma60 is not None and not pd.isna(ma60.iloc[-1]) and price < ma60.iloc[-1] * 0.90:
            return None

        vol_ratio_val = float(vol_ratio_series.iloc[-1]) if not pd.isna(vol_ratio_series.iloc[-1]) else 1.0
        lower_touch_pct = (price - lower_band) / lower_band * 100  # 距下轨百分比，越小越靠下
        upper_touch_pct = (price - upper_band) / upper_band * 100  # 距上轨百分比，越大越靠上

        # ── 模式判定（互斥）：靠近下轨走 mode1，远离下轨且收口/接近上轨走 mode2 ──
        # 临界值 5%：下轨上方 5% 内视为下轨区域
        if lower_touch_pct <= 5:
            return self._eval_mode1_lower_bounce(
                code, name_map, trade_date,
                kline, close, lower, lower_band, price, prev_price,
                lower_touch_pct, vol_ratio_val, upper_band, mid_band,
                scanner,
            )
        else:
            return self._eval_mode2_volatility_breakout(
                code, name_map, trade_date,
                kline, close, upper, lower, mid_band, upper_band, lower_band, price,
                vol_ratio_val, upper_touch_pct,
                scanner,
            )

    def _eval_mode1_lower_bounce(
        self, code, name_map, trade_date,
        kline, close, lower, lower_band, price, prev_price,
        lower_touch_pct, vol_ratio_val, upper_band, mid_band,
        scanner,
    ):
        signals = ["模式1:下轨反弹"]
        score = 0

        if lower_touch_pct <= 3:
            signals.append(f"触及布林下轨({lower_touch_pct:.1f}%)")
            score += 40

        # 当根反弹：昨贴下轨、今上行
        if prev_price <= lower_band * 1.02 and price > prev_price:
            signals.append("布林下轨反弹")
            score += 30

        # 昨触下轨今脱离（V 型起步）
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

        return self._build_signal(
            code, name_map, trade_date, kline, scanner, price,
            score, signals, vol_ratio_val,
            lower_band=lower_band, upper_band=upper_band, mid_band=mid_band,
            mode="lower_bounce",
        )

    def _eval_mode2_volatility_breakout(
        self, code, name_map, trade_date,
        kline, close, upper, lower, mid_band, upper_band, lower_band, price,
        vol_ratio_val, upper_touch_pct,
        scanner,
    ):
        signals = ["模式2:波动率突破"]
        score = 0

        bb_width = upper_band - lower_band
        bb_width_series = upper - lower

        # 布林带收口（10 日最低宽附近）
        if len(bb_width_series) >= 10:
            min_width_10d = float(bb_width_series.iloc[-10:].min())
            if bb_width < min_width_10d * 1.05:
                signals.append("布林带收口")
                score += 25

        # 量能配合
        if vol_ratio_val > 1.5:
            signals.append(f"放量({vol_ratio_val:.1f}倍)")
            score += 20
        elif vol_ratio_val > 1.2:
            signals.append(f"量能温和({vol_ratio_val:.1f}倍)")
            score += 10

        # 突破中轨
        if price > mid_band:
            signals.append("突破布林中轨")
            score += 15

        # 接近上轨
        if upper_band > 0 and upper_touch_pct >= -2:  # ≥0.98×upper
            signals.append("接近布林上轨")
            score += 30

        if score < 60:
            return None

        return self._build_signal(
            code, name_map, trade_date, kline, scanner, price,
            score, signals, vol_ratio_val,
            lower_band=lower_band, upper_band=upper_band, mid_band=mid_band,
            mode="volatility_breakout",
        )

    def _build_signal(
        self, code, name_map, trade_date, kline, scanner, price,
        score, signals, vol_ratio_val,
        lower_band, upper_band, mid_band, mode,
    ):
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
                "mode": mode,
                "lower_band": round(lower_band, 2),
                "upper_band": round(upper_band, 2),
                "mid_band": round(mid_band, 2),
                "bollinger_width": round(upper_band - lower_band, 2),
                "price": round(float(price), 2),
            },
        )
