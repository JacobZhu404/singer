"""
趋势确认策略（中线持仓）

定位：已确立的上升趋势，适合中线持仓
与 golden_cross 的区别：
  - golden_cross: 趋势启动初期（金叉当天）
  - macd_bull: 趋势已确认（金叉后+均线多头）

核心逻辑：
  1. MACD零轴金叉（DIF>0, DEA>0, DIF>DEA）
  2. 四线多头排列（MA5>MA10>MA20>MA60）
  3. 趋势已确立（收盘价 > MA20 且 MA20 向上）

信号评分：
  - MACD零轴金叉：+30（核心信号）
  - 四线多头排列：+20
  - MA20>MA60 中期多头：+15
  - MACD柱放大（近3日）：+15
  - 价格站上MA20（趋势确认）：+10
  - 价格站上MA5（短期强势）：+5
适用：趋势确认、中线持仓
"""

import pandas as pd
import numpy as np
from typing import List, Optional
import logging

from .base import BaseStrategy, StockSignal, _compute_risk_flags

logger = logging.getLogger(__name__)


class MACDBullStrategy(BaseStrategy):
    """
    趋势确认策略：
    MACD零轴确认 + 四线多头排列 + 趋势已确立
    适合中线持仓，胜率较高
    """
    name = "macd_bull"
    description = "MACD零轴金叉+四线多头+趋势确立，中线持仓首选"
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

        # ── 条件1: MACD 零轴金叉（核心信号）──
        # DIF>0 且 DEA>0 且 DIF>DEA
        if dif.iloc[i] > 0 and dea.iloc[i] > 0 and dif.iloc[i] > dea.iloc[i]:
            signals.append("MACD零轴金叉")
            score += 30
        else:
            # 非核心信号，直接过滤
            return None

        # ── 条件2: 四线多头排列 ──
        ma5 = mas["ma5"].iloc[i]
        ma10 = mas["ma10"].iloc[i]
        ma20 = mas["ma20"].iloc[i]
        ma60 = mas["ma60"].iloc[i]
        
        if not any(pd.isna(x) for x in [ma5, ma10, ma20, ma60]):
            if ma5 > ma10 > ma20 > ma60:
                signals.append("四线多头排列")
                score += 20
            else:
                # 不满足条件，直接过滤
                return None

            # ── 条件3: MA20>MA60 中期趋势 ──
            if ma20 > ma60:
                signals.append("MA20>MA60中期多头")
                score += 15

        # ── 条件4: MACD 柱放大（近3日连续） ──
        if i >= 2 and not pd.isna(macd_bar.iloc[i - 2]):
            if macd_bar.iloc[i] > 0:
                if macd_bar.iloc[i] > macd_bar.iloc[i - 1] > macd_bar.iloc[i - 2]:
                    signals.append("MACD柱放大")
                    score += 15

        # ── 条件5: 价格站上MA20（趋势确立）──
        if close.iloc[i] > ma20:
            signals.append("价格站上MA20")
            score += 10

        # ── 条件6: 价格站上MA5（短期强势）──
        if close.iloc[i] > ma5:
            signals.append("价格站上MA5")
            score += 5

        # 阈值检查
        if score < 75:
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
