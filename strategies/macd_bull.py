"""
趋势共振策略（MACD多头 + 均线金叉 合并版）
条件（三重验证，越多越强）：
  1. MACD零轴确认：DIF>0 且 DEA>0
  2. 均线多头排列：MA5>MA10>MA20>MA60，且MA20>MA60
  3. 价格确认：价格站上所有均线

信号评分：
  - MA5上穿MA10当日金叉：+25（最强信号，核心信号）
  - MACD零轴金叉（DIF>0且DEA>0且DIF>DEA）：+30（核心信号）
  - MACD零轴下金叉：+15
  - MA5>MA10>MA20>MA60均线多头：+20
  - MA20>MA60中期多头：+10
  - MACD柱放大（近3日连续）：+15
  - 价格站上MA5（短期趋势确认）：+10
适用：趋势确认、中线持仓

优化（2026-05）：
  - 阈值提高至65分（原50分）
  - 必须有核心信号（MA5金叉 或 MACD零轴金叉）
  - 移除DIF零轴以上（非金叉）的弱信号
  - MACD零轴金叉加分提高至30（原25）
  - MA20>MA60加分降低至10（原15）
"""

import pandas as pd
import numpy as np
from typing import List, Optional
import logging

from .base import BaseStrategy, StockSignal, _compute_risk_flags

logger = logging.getLogger(__name__)


class MACDBullStrategy(BaseStrategy):
    """
    趋势共振策略：
    MACD零轴确认 + 均线多头排列 + 价格站上均线
    三重验证确保趋势可靠，避免假信号
    """
    name = "macd_bull"
    description = "MACD零轴+DIF>DEA金叉+均线多头排列+价格站上均线，三重趋势共振"
    base_win_rate = 0.60

    def _evaluate_single_stock(
        self,
        code: str,
        scanner,
        name_map: dict,
        trade_date: str,
    ) -> Optional[StockSignal]:
        """评估单只股票，返回StockSignal或None（并行架构）"""
        indicators = scanner.get_indicators(code, days=120)
        if not indicators or len(indicators["kline"]) < 60:
            raise self._SkipStock()

        df = indicators["kline"]
        close = df["close"]

        dif, dea, macd_bar = indicators["macd"]
        mas = indicators["ma"]
        vol_ratio = indicators["vol_ratio"]

        i = len(df) - 1
        if pd.isna(dif.iloc[i]) or pd.isna(dea.iloc[i]):
            raise self._SkipStock()

        signals = []
        score = 0
        has_core_signal = False  # 必须有核心信号

        # ── 条件1: MA5 上穿 MA10（当日金叉，最强信号）──
        ma5 = mas["ma5"].iloc[i]
        ma10 = mas["ma10"].iloc[i]
        ma20 = mas["ma20"].iloc[i]
        ma60 = mas["ma60"].iloc[i]
        ma5_prev = mas["ma5"].iloc[i - 1]
        ma10_prev = mas["ma10"].iloc[i - 1]

        if not any(pd.isna(x) for x in [ma5, ma10, ma20, ma60, ma5_prev, ma10_prev]):
            if ma5_prev <= ma10_prev and ma5 > ma10:
                signals.append("MA5上穿MA10金叉")
                score += 25
                has_core_signal = True
            elif ma5 > ma10:
                signals.append("MA5>MA10多头")
                score += 15

            # ── 条件2: 均线多头排列 ──
            if ma5 > ma10 > ma20 > ma60:
                signals.append("均线多头排列")
                score += 20

            # ── 条件3: MA20>MA60 中期趋势 ──
            if ma20 > ma60:
                signals.append("MA20>MA60中期多头")
                score += 10  # 优化：15→10

            # ── 条件4: 价格站上MA5（短期趋势确认）──
            if close.iloc[i] > ma5:
                signals.append("价格站上MA5")
                score += 10

        # ── 条件5: MACD 零轴判断 ──
        # 零轴上方金叉（最强）：DIF>0 且 DEA>0 且 DIF>DEA
        if dif.iloc[i] > 0 and dea.iloc[i] > 0 and dif.iloc[i] > dea.iloc[i]:
            signals.append("MACD零轴金叉")
            score += 30  # 优化：25→30
            has_core_signal = True
        # 零轴下方金叉（较弱）：DIF>DEA 但零线以下
        elif dif.iloc[i] > dea.iloc[i]:
            signals.append("MACD金叉（零轴下）")
            score += 15
        # 优化：移除 DIF零轴以上（非金叉）的弱信号

        # ── 条件6: MACD 柱放大（近3日连续） ──
        if i >= 2 and not pd.isna(macd_bar.iloc[i - 2]):
            if macd_bar.iloc[i] > 0:
                if macd_bar.iloc[i] > macd_bar.iloc[i - 1] > macd_bar.iloc[i - 2]:
                    signals.append("MACD柱放大")
                    score += 15

        # 优化：必须有核心信号
        if not has_core_signal:
            return None

        # 优化：阈值提高至65
        if score < 65:
            return None

        # ── 构建返回结果 ──
        latest = close.iloc[i]
        quote = self._get_quote(scanner, code, float(latest))
        pct = quote.get("涨跌幅", 0.0) or 0.0
        vr = float(vol_ratio.iloc[i]) if not pd.isna(vol_ratio.iloc[i]) else 1.0

        win_rate = self._calc_win_rate(score, signals)
        risk_flags = _compute_risk_flags(df)

        return StockSignal(
            ts_code=code,
            name=name_map.get(code, code),
            strategy=self.name,
            score=min(score, 100),
            win_rate=win_rate,
            signals=signals,
            latest_price=round(float(latest), 2),
            pct_chg=round(float(pct), 2),
            volume_ratio=round(vr, 2),
            risk_flags=risk_flags,
            trade_date=trade_date,
            extra={
                "dif": round(float(dif.iloc[i]), 4),
                "dea": round(float(dea.iloc[i]), 4),
                "macd": round(float(macd_bar.iloc[i]), 4),
                "ma5": round(float(ma5), 2) if not pd.isna(ma5) else None,
                "ma10": round(float(ma10), 2) if not pd.isna(ma10) else None,
                "ma20": round(float(ma20), 2) if not pd.isna(ma20) else None,
                "ma60": round(float(ma60), 2) if not pd.isna(ma60) else None,
            }
        )
