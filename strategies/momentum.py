"""
动量策略（风险调整动量）

基于 Barroso & Santa-Clara 2015 "Momentum Has Its Moments" 核心洞察：
用已实现波动率标准化动量信号，奖励平滑趋势、惩罚噪声暴涨。

核心逻辑：
  1. 高波动股直接过滤（年化vol > 80% = 崩盘候选）
  2. 风险调整动量 = N日涨幅 / N日期望波动（z-score）
  3. 趋势一致性加分（阳线占比高 = 平滑上涨）
  4. 保留量能确认/RSI/MA20 正交信号

信号评分：
  - 5日风险调整动量：max 35
  - 10日风险调整动量：max 25
  - 20日风险调整动量：max 20
  - 趋势一致性（20日阳线占比）：max 15
  - 成交量放大（量比>1.5）：+15
  - RSI未超买（<70）：+10
  - 价格在MA20上方：+10
"""

import pandas as pd
import numpy as np
from typing import Optional
import logging

from .base import BaseStrategy, StockSignal, _compute_risk_flags, last_vol_ratio

logger = logging.getLogger(__name__)

VOL_CEILING = 0.80


class MomentumStrategy(BaseStrategy):
    """
    风险调整动量策略：
    用 return / expected_range (z-score) 替代原始涨幅，识别高信噪比趋势股。
    """
    name = "momentum"
    description = "风险调整动量（波动率标准化）+趋势一致性+量能确认"
    base_win_rate = 0.42

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
        indicators = scanner.get_indicators(code, days=120)
        if not indicators or len(indicators["kline"]) < self.lookback_days:
            raise self._SkipStock()

        df = indicators["kline"]
        close = df["close"].astype(float)

        i = len(df) - 1
        if i < self.lookback_days:
            raise self._SkipStock()

        price_now = close.iloc[i]

        # ── 20日已实现波动率（年化），用于过滤和标准化 ──
        daily_returns = close.pct_change()
        ret_20d = daily_returns.iloc[i - 19:i + 1]
        daily_std = ret_20d.std()
        vol_20d_ann = daily_std * np.sqrt(252)

        if vol_20d_ann > VOL_CEILING:
            return None

        if daily_std <= 0:
            raise self._SkipStock()

        # ── 各窗口涨幅 ──
        price_5d = close.iloc[i - 5] if i >= 5 else close.iloc[0]
        price_10d = close.iloc[i - 10] if i >= 10 else close.iloc[0]
        price_20d = close.iloc[i - self.lookback_days]

        change_5d = (price_now - price_5d) / price_5d
        change_10d = (price_now - price_10d) / price_10d
        change_20d = (price_now - price_20d) / price_20d

        # ── 风险调整动量 = 涨幅 / 期望N日波动（z-score） ──
        adj_5d = change_5d / (daily_std * np.sqrt(5))
        adj_10d = change_10d / (daily_std * np.sqrt(10))
        adj_20d = change_20d / (daily_std * np.sqrt(20))

        # ── 入场门槛：5日信噪比 > 0.8σ ──
        if adj_5d < 0.8:
            return None

        # ── 趋势一致性（20日阳线占比） ──
        pos_days_ratio = float((ret_20d > 0).sum()) / len(ret_20d)

        # ── 打分 ──
        momentum_score = 0
        signals = []

        # 5日风险调整动量（max 35）
        momentum_score += min(adj_5d * 15, 35)
        signals.append(f"5日动量z={adj_5d:.2f}(涨{change_5d*100:.1f}%)")

        # 10日风险调整动量（max 25）
        if adj_10d > 0:
            momentum_score += min(adj_10d * 12, 25)
            signals.append(f"10日z={adj_10d:.2f}")

        # 20日风险调整动量（max 20）
        if adj_20d > 0:
            momentum_score += min(adj_20d * 10, 20)
            signals.append(f"20日z={adj_20d:.2f}")

        # 趋势一致性（max 15 / penalty -10）
        if pos_days_ratio > 0.6:
            trend_bonus = min((pos_days_ratio - 0.5) * 30, 15)
            momentum_score += trend_bonus
            signals.append(f"趋势平滑({pos_days_ratio:.0%}阳线)")
        elif pos_days_ratio < 0.4:
            momentum_score -= 10
            signals.append(f"趋势混乱({pos_days_ratio:.0%}阳线)")

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
        if momentum_score < 70:
            return None

        # ── 构建返回结果 ──
        quote = self._get_quote(scanner, code, float(price_now))
        pct = quote.get("涨跌幅", 0.0) or 0.0
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
                "change_5d": round(change_5d * 100, 2),
                "change_10d": round(change_10d * 100, 2),
                "change_20d": round(change_20d * 100, 2),
                "realized_vol_20d": round(vol_20d_ann * 100, 1),
                "adj_momentum_5d": round(adj_5d, 2),
                "adj_momentum_10d": round(adj_10d, 2),
                "adj_momentum_20d": round(adj_20d, 2),
                "pos_days_ratio": round(pos_days_ratio, 2),
                "rsi14": round(r, 1),
                "volume_ratio": round(vr, 2),
            },
        )
