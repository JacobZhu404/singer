"""
布林带策略（下轨反弹 + 波动率突破）

定位：两种模式
  1. 下轨反弹：价格触及布林带下轨后反弹（均值回归）
  2. 波动率突破：布林带收口后扩张（捕捉大行情）

与 volume_breakout 的区别：
  - volume_breakout: 基于成交量突破
  - bollinger_bands: 基于波动率突破

核心逻辑（模式1 - 下轨反弹）：
  1. 价格触及布林带下轨（≤下轨 + 3%）
  2. 布林下轨反弹（价格开始上行）
  3. 缩量止跌（量能萎缩）

核心逻辑（模式2 - 波动率突破）：
  1. 布林带宽度<10日最低（波动率收缩）
  2. 成交量放大（量比>1.5）
  3. 价格突破布林中轨或上轨

信号评分：
  - 模式1：触及下轨+40，下轨反弹+30，缩量止跌+10
  - 模式2：布林带收口+25，成交量放大+20，突破中轨/上轨+30
适用：震荡市（模式1）、突破行情（模式2）
"""

import pandas as pd
import numpy as np
import logging

from .base import BaseStrategy, StockSignal, _compute_risk_flags
from ..utils.indicators import calc_bollinger, calc_volume_ratio

logger = logging.getLogger(__name__)


class BollingerBandsStrategy(BaseStrategy):
    """布林带策略（下轨反弹 + 波动率突破）"""
    name = "bollinger_bands"
    description = "下轨反弹+波动率突破，均值回归或捕捉大行情"
    base_win_rate = 0.55

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date):
        try:
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
            price = close.iloc[-1]
            prev_price = close.iloc[-2]
            lower_band = lower.iloc[-1]
            upper_band = upper.iloc[-1]
            mid_band = mid.iloc[-1]

            if pd.isna(lower_band) or pd.isna(upper_band) or lower_band == 0:
                raise self._SkipStock()

            # 趋势过滤：避免在极端下跌趋势中抄底
            # 用MA60判断大趋势，而非MA20（MA20即布林中轨，触下轨时价格必然远低于MA20）
            ma60 = indicators["ma"].get("ma60")
            if ma60 is not None and not pd.isna(ma60.iloc[-1]) and price < ma60.iloc[-1] * 0.90:
                return None

            signals = []
            score = 0
            lower_touch_pct = (price - lower_band) / lower_band * 100

            # ── 模式1：下轨反弹（原有逻辑）──
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

            vol_ratio_val = float(vol_ratio_series.iloc[-1]) if not pd.isna(vol_ratio_series.iloc[-1]) else 1.0
            if vol_ratio_val < 0.6:
                signals.append("缩量止跌")
                score += 10

            # ── 模式2：波动率突破（新增逻辑）──
            # 计算布林带宽度
            bb_width = upper_band - lower_band
            bb_width_series = upper - lower
            
            # 布林带宽度<10日最低（波动率收缩）
            if len(bb_width_series) >= 10:
                min_width_10d = bb_width_series.iloc[-10:].min()
                if bb_width < min_width_10d * 1.05:  # 接近10日最低
                    signals.append("布林带收口")
                    score += 25
            
            # 成交量放大（波动率突破需要量能配合）
            if vol_ratio_val > 1.5:
                signals.append(f"放量({vol_ratio_val:.1f}倍)")
                score += 20
            elif vol_ratio_val > 1.2:
                signals.append(f"量能温和({vol_ratio_val:.1f}倍)")
                score += 10
            
            # 价格突破布林中轨或上轨
            if price > mid_band:
                signals.append("突破布林中轨")
                score += 15
            if price > upper_band * 0.98:  # 接近上轨
                signals.append("接近布林上轨")
                score += 30

            if score < 45:
                return None

            quote = self._get_quote(scanner, code, float(price))

            return StockSignal(
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
                    "mid_band": round(float(mid_band), 2),
                    "bollinger_width": round(float(bb_width), 2),
                    "price": round(float(price), 2),
                },
            )

        except Exception as e:
            logger.debug(f"[布林策略] {code} 计算失败: {e}")