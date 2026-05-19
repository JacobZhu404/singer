"""
策略2: 强势股选股（优化版）
条件（综合评分，满足即可）：
  1. 放量上涨（量比 > 1.5）
  2. 红肥绿瘦：涨时成交量大、跌时成交量小（近10日）
  3. 五连小阳 / 跳空缺口（互斥，二选一加分）
     - 五连小阳：连续5日小阳线
     - 跳空缺口：今日最低 > 昨日最高
  4. MACD 零轴以上：DIF > 0

v2变更：
- "五连小阳"与"跳空缺口"改为 elif 互斥（两者同时出现概率极低）
- 入选门槛从40降至30（满足放量+红肥绿瘦+MACD即40分刚好及格）
"""

import pandas as pd
import numpy as np
import logging

from .base import BaseStrategy, StockSignal, _compute_risk_flags
from ..utils.indicators import (
    calc_macd, calc_volume_ratio,
    is_red_candle, detect_gap_up,
    is_near_52w_high, calc_relative_strength
)

logger = logging.getLogger(__name__)


class StrongStockStrategy(BaseStrategy):
    name = "strong_stock"
    description = "强势股 - 放量红肥绿瘦+小阳/缺口(互斥)+MACD零轴(v2优化)"
    base_win_rate = 0.62

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date):
        indicators = scanner.get_indicators(code, days=120)
        if not indicators or len(indicators["kline"]) < 10:
            raise self._SkipStock()

        df = indicators["kline"]
        close = df["close"]
        open_ = df["open"]
        high = df["high"]
        low = df["low"]
        vol = df["vol"]
        pct_chg = close.pct_change() * 100

        signals = []
        score = 0
        extra = {}  # 初始化extra字典

        vol_ratio = indicators["vol_ratio"]
        red = is_red_candle(open_, close)
        dif, dea, macd_bar = indicators["macd"]
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
            if red.iloc[j]:
                up_vol += v
            else:
                down_vol += v
        if down_vol > 0 and up_vol / (down_vol + 1e-8) > 2.0:  # 从 1.5 提高到 2.0
            signals.append(f"红肥绿瘦(涨缩量比{up_vol/down_vol:.1f})")
            score += 20

        if i >= 4 and all(red.iloc[i - k] for k in range(5)) and \
           all(not pd.isna(pct_chg.iloc[i - k]) and
               abs(float(pct_chg.iloc[i - k])) <= 3.0
               for k in range(5)):
            # 增加累计涨幅限制（防止追高）
            cumulative_gain = (close.iloc[i] / close.iloc[i-4] - 1) * 100
            if cumulative_gain < 12.0:  # 累计涨幅 < 12%
                signals.append("五连小阳")
                score += 20
        elif gap_up.iloc[i]:
            # 与五连小阳互斥：跳空高开会破坏小阳线节奏
            signals.append("跳空缺口(今低>昨高)")
            score += 20

        if not pd.isna(dif.iloc[i]) and dif.iloc[i] > 0:
            signals.append("MACD零轴以上")
            score += 20

        # 优化1：52周新高回踩确认
        if len(df) >= 250:
            near_52w_high = is_near_52w_high(high, close, window=250, threshold=0.95)
            if near_52w_high:
                # 回踩确认：当前价格 >= 250日最高价 * 0.95（在新高附近）
                highest_250 = high.iloc[-250:].max()
                if close.iloc[i] >= highest_250 * 0.95:
                    signals.append("52周新高回踩确认")
                    score += 25
                    extra["near_52w_high"] = True
        
        # 优化2：相对强度（20日涨跌幅）
        if len(df) >= 20:
            rel_strength = calc_relative_strength(close, window=20)
            extra["rel_strength_20d"] = round(rel_strength, 2)
            if rel_strength > 0:
                signals.append(f"相对强度正(+{rel_strength:.1f}%)")
                score += 15
            elif rel_strength > -5:
                signals.append(f"相对强度弱(+{rel_strength:.1f}%)")
                score += 5

        if score < 50:  # 从 30 提高到 50（根据回测结果优化）
            return None

        latest = close.iloc[i]
        vr = float(vol_ratio.iloc[i]) if not pd.isna(vol_ratio.iloc[i]) else 1.0
        quote = self._get_quote(scanner, code, float(latest))
        
        # 合并extra字典
        extra["gap_up"] = bool(gap_up.iloc[i])
        extra["dif"] = round(float(dif.iloc[i]), 4) if not pd.isna(dif.iloc[i]) else None
        extra["up_down_vol_ratio"] = round(up_vol / (down_vol + 1e-8), 2)

        return StockSignal(
            ts_code=code,
            name=name_map.get(code, code),
            strategy=self.name,
            score=score,
            win_rate=None,
            signals=signals,
            latest_price=round(float(quote.get("最新价", latest)), 2),
            pct_chg=round(float(quote.get("涨跌幅", 0.0)), 2),
            volume_ratio=round(vr, 2),
            risk_flags=_compute_risk_flags(df),
            trade_date=trade_date,
            extra=extra
        )
