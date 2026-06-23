"""
RPS 相对强度突破策略（欧奈尔 William O'Neil）

核心思想（CANSLIM 中的 "L" — Leader）：
  真正的领涨股在突破前已具备远强于大盘的相对价格强度（RPS）。
  本策略用「多周期加权涨幅」近似 RPS（越近的窗口权重越大），再叠加
  「创阶段新高 + 放量」的突破确认。

说明：
  标准 RPS 是全市场横截面百分位排名（0-99），无法在单只股票评估里直接算出。
  这里在 _evaluate_single_stock 内算出每只股票的「强度原始分」，命中后由
  base._build_result 的排名百分位机制做横向相对排名，效果上等价于「强中选强」。

条件：
  1. 多周期加权涨幅为正且较高（趋势确立、强于自身历史）
  2. 价格创 N 日新高（突破阶段平台）
  3. 突破当日/近日放量（量比 > 1.5）
  4. 价格站上 MA50（中期趋势向上）
适用：主升浪初期的领涨股捕捉
"""

import pandas as pd
import numpy as np
from typing import Optional
import logging

from .base import BaseStrategy, StockSignal, _compute_risk_flags

logger = logging.getLogger(__name__)


class RpsBreakoutStrategy(BaseStrategy):
    """RPS 相对强度突破（欧奈尔领涨股）"""
    name = "rps_breakout"
    description = "欧奈尔RPS：多周期加权强度+创阶段新高+放量突破，捕捉领涨股"
    base_win_rate = 0.58

    def __init__(self, top_n: int = 10):
        super().__init__(top_n=top_n)

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date) -> Optional[StockSignal]:
        # RPS 需要较长历史，尽量取 250 日（约一年），不足则降级用现有数据
        indicators = scanner.get_indicators(code, days=250)
        if not indicators or len(indicators["kline"]) < 60:
            raise self._SkipStock()

        df = indicators["kline"]
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        n = len(close)
        i = n - 1

        price_now = float(close.iloc[i])
        if price_now < 0.1 or price_now > 1000:
            raise self._SkipStock()

        # ── 多周期加权强度（欧奈尔 RPS 思路：近端权重更大）──
        # 取可用的窗口；窗口超过现有数据长度则跳过该窗口
        windows = [(20, 0.40), (60, 0.30), (120, 0.20), (240, 0.10)]
        strength = 0.0
        weight_used = 0.0
        ret_detail = {}
        for w, wt in windows:
            if i - w < 0:
                continue
            past = float(close.iloc[i - w])
            if past <= 0:
                continue
            ret = (price_now - past) / past * 100
            strength += ret * wt
            weight_used += wt
            ret_detail[f"r{w}"] = round(ret, 1)

        if weight_used <= 0:
            raise self._SkipStock()
        # 归一化（数据不足导致部分窗口缺失时，按已用权重还原量纲）
        strength = strength / weight_used

        signals = []
        score = 0

        # ── 强度评分（强度越高分越高，封顶 45）──
        if strength <= 0:
            return None
        if strength >= 60:
            score += 45
            signals.append(f"超强相对强度({strength:.0f})")
        elif strength >= 30:
            score += 35
            signals.append(f"强相对强度({strength:.0f})")
        elif strength >= 15:
            score += 22
            signals.append(f"中等相对强度({strength:.0f})")
        else:
            score += 10
            signals.append(f"弱相对强度({strength:.0f})")

        # ── 突破确认：创阶段新高 ──
        # 用过去 60 日（不含今日）最高价作为平台高点
        lookback = min(60, i)
        platform_high = float(high.iloc[i - lookback:i].max()) if lookback >= 1 else price_now
        today_high = float(high.iloc[i])
        has_breakout = False
        if today_high >= platform_high:
            score += 25
            has_breakout = True
            signals.append(f"创{lookback}日新高({platform_high:.2f})")
            # 更长周期新高加成
            if i >= 120:
                high_120 = float(high.iloc[i - 120:i].max())
                if today_high >= high_120:
                    score += 10
                    signals.append("创120日新高")

        # ── 放量确认 ──
        vol_ratio = indicators.get("vol_ratio")
        vr = 1.0
        if vol_ratio is not None and not pd.isna(vol_ratio.iloc[i]):
            vr = float(vol_ratio.iloc[i])
        if vr >= 2.0:
            score += 15
            signals.append(f"放量突破({vr:.1f}倍)")
        elif vr >= 1.5:
            score += 10
            signals.append(f"温和放量({vr:.1f}倍)")

        # ── 中期趋势：站上 MA50 ──
        if i >= 50:
            ma50 = float(close.iloc[i - 49:i + 1].mean())
            if price_now > ma50:
                score += 10
                signals.append("价格站上MA50")
            else:
                score -= 10
                signals.append("价格在MA50下方")

        # 必须同时满足：正强度 + 突破创新高
        if not has_breakout:
            return None

        if score < 75:
            return None

        quote = self._get_quote(scanner, code, price_now)
        pct = quote.get("涨跌幅", 0.0) or 0.0
        return StockSignal(
            ts_code=code,
            name=name_map.get(code, code),
            strategy=self.name,
            score=min(int(score), 100),
            win_rate=None,
            signals=signals,
            latest_price=round(float(quote.get("最新价", price_now)), 2),
            pct_chg=round(float(pct), 2),
            volume_ratio=round(vr, 2),
            risk_flags=_compute_risk_flags(df),
            trade_date=trade_date,
            extra={
                "strength": round(strength, 1),
                "platform_high": round(platform_high, 2),
                **ret_detail,
            },
        )
