"""
动量策略（相对强度）

定位：识别强势股，捕捉趋势延续
与 macd_bull/golden_cross 的区别：
  - macd_bull/golden_cross: 基于技术指标（MACD/MA金叉）
  - momentum: 基于价格动量（过去N日涨幅）

核心逻辑：
  1. 过去N日涨幅较高（趋势向上）
  2. 涨幅 > 0（上涨趋势）
  3. 成交量配合（量比 > 1.2）
  4. RSI未超买（< 70）

信号评分：
  - 过去5日涨幅高：+40（涨幅越大分越高，封顶40）
  - 过去10日涨幅高：+30（封顶30）
  - 过去20日涨幅高：+20（封顶20）
  - 成交量放大（量比>1.5）：+15
  - RSI未超买（<70）：+10
  - 价格在MA20上方：+10
适用：趋势延续、强势股回调后继续上涨
"""

import pandas as pd
import numpy as np
from typing import Optional, List
import logging

from .base import BaseStrategy, StockSignal, _compute_risk_flags, last_vol_ratio

logger = logging.getLogger(__name__)


class MomentumStrategy(BaseStrategy):
    """
    动量策略：
    基于价格动量（过去N日涨幅强度），识别强势股
    适合趋势延续行情
    """
    name = "momentum"
    description = "价格动量强度+量能确认，捕捉趋势延续"
    base_win_rate = 0.58

    def __init__(self, top_n: int = 10, lookback_days: int = 20):
        super().__init__(top_n=top_n)
        self.lookback_days = lookback_days

    def _evaluate_single_stock(
        self,
        code: str,
        scanner,
        name_map: dict,
        trade_date: str,
    ) -> Optional[StockSignal]:
        """评估单只股票，返回StockSignal或None（并行架构）"""
        indicators = scanner.get_indicators(code, days=120)
        if not indicators or len(indicators["kline"]) < self.lookback_days:
            raise self._SkipStock()

        df = indicators["kline"]
        close = df["close"].astype(float)
        vol = df["vol"].astype(float)

        i = len(df) - 1

        # ── 计算过去N日涨幅 ──
        if i < self.lookback_days:
            raise self._SkipStock()

        price_now = close.iloc[i]
        price_5d = close.iloc[i - 5] if i >= 5 else close.iloc[0]
        price_10d = close.iloc[i - 10] if i >= 10 else close.iloc[0]
        price_20d = close.iloc[i - self.lookback_days]

        change_5d = (price_now - price_5d) / price_5d * 100
        change_10d = (price_now - price_10d) / price_10d * 100
        change_20d = (price_now - price_20d) / price_20d * 100

        # ── 计算相对强度排名（需要外部调用者传入排名信息）──
        # 注意：这里只能计算单只股票的涨幅，排名需要在 merge_results 时计算
        # 我们这里先计算涨幅，排名由引擎层处理
        momentum_score = 0
        signals = []

        # 过去5日涨幅 > 5%（收紧：从>0提高到>5%，过滤弱动量）
        if change_5d > 5:
            momentum_score += min(change_5d * 2, 40)  # 最高40分
            signals.append(f"5日涨幅{change_5d:.1f}%")
        else:
            return None

        # 过去10日涨幅 > 0
        if change_10d > 0:
            momentum_score += min(change_10d * 1.5, 30)  # 最高30分
            signals.append(f"10日涨幅{change_10d:.1f}%")

        # 过去20日涨幅 > 0
        if change_20d > 0:
            momentum_score += min(change_20d, 20)  # 最高20分
            signals.append(f"20日涨幅{change_20d:.1f}%")

        # ── 成交量确认 ──
        vr = last_vol_ratio(indicators["vol_ratio"], i)

        if vr > 1.5:
            signals.append(f"放量({vr:.1f}倍)")
            momentum_score += 15
        elif vr > 1.2:
            signals.append(f"量能温和({vr:.1f}倍)")
            momentum_score += 10

        # ── RSI 未超买 ──
        rsi = indicators["rsi"]
        r = float(rsi.iloc[i]) if not pd.isna(rsi.iloc[i]) else 50

        if r < 70:
            signals.append(f"RSI未超买({r:.0f})")
            momentum_score += 10
        else:
            signals.append(f"RSI超买({r:.0f})")
            momentum_score -= 15

        # ── 价格在MA20上方 ──
        ma20 = close.rolling(20).mean().iloc[i]
        if not pd.isna(ma20) and price_now > ma20:
            signals.append("价格在MA20上方")
            momentum_score += 10

        # ── 阈值过滤 ──
        if momentum_score < 85:  # 收紧：50→85，控制命中数
            return None

        # ── 构建返回结果 ──
        quote = self._get_quote(scanner, code, float(price_now))
        pct = quote.get("涨跌幅", 0.0) or 0.0

        # 风险标记
        risk_flags = _compute_risk_flags(df)

        return StockSignal(
            ts_code=code,
            name=name_map.get(code, code),
            strategy=self.name,
            score=min(int(momentum_score), 100),
            win_rate=None,
            signals=signals,
            latest_price=round(float(price_now), 2),
            pct_chg=round(float(pct), 2),
            volume_ratio=round(vr, 2),
            risk_flags=risk_flags,
            trade_date=trade_date,
            extra={
                "change_5d": round(change_5d, 2),
                "change_10d": round(change_10d, 2),
                "change_20d": round(change_20d, 2),
                "rsi14": round(r, 1),
                "volume_ratio": round(vr, 2),
            },
        )

