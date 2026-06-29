"""
趋势确认策略（中线持仓）

定位：已确立的上升趋势，适合中线持仓
与 golden_cross 的区别：
  - golden_cross: 趋势启动初期（金叉当天）
  - macd_bull: 趋势已确认（金叉后+均线多头）

核心逻辑：
  1. MACD零轴金叉（DIF>0, DEA>0, DIF>DEA）
  2. 四线多头排列（MA5>MA10>MA20>MA60）
  3. 趋势已确立（收盘价 > MA20 且 MA60 向上）

v2改进：
  - 区分金叉阶段（刚突破/确认/延续/成熟）
  - 检测动能衰竭风险（DIF下行、MACD柱缩小）
  - MA20>MA60 改为 MA60斜率向上（避免冗余）

信号评分：
  - MACD零轴金叉：+30（核心信号）
  - 金叉阶段：刚突破+25 / 确认+20 / 延续+15 / 成熟+0
  - 四线多头排列：+20
  - MA60向上：+15（原MA20>MA60，避免冗余）
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
    base_win_rate = 0.46  # 30日实测胜率，2026-06-29 全量回测

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
        extra_risks = []  # 策略自计算的风险标签

        # ── 条件1: MACD 零轴金叉（核心信号）──
        # DIF>0 且 DEA>0 且 DIF>DEA
        if dif.iloc[i] > 0 and dea.iloc[i] > 0 and dif.iloc[i] > dea.iloc[i]:
            signals.append("MACD零轴金叉")
            score += 30
        else:
            # 非核心信号，直接过滤
            return None

        # ── 条件1b: 金叉持续天数（区分阶段）──
        cross_days = 0
        for j in range(i, max(i - 30, -1), -1):
            if dif.iloc[j] > dea.iloc[j]:
                cross_days += 1
            else:
                break

        if cross_days <= 3:
            signals.append("刚突破(金叉1-3天)")
            score += 25
        elif cross_days <= 10:
            signals.append("趋势确认(金叉4-10天)")
            score += 20
        else:
            # 金叉超过10天，不是趋势启动最佳窗口，直接过滤
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

            # ── 条件3: MA60斜率向上 ──
            # 原条件 MA20>MA60 冗余（四线多头已蕴含 MA20>MA60）
            # 改为检测 MA60 是否持续向上，更有区分度
            ma60_prev = mas["ma60"].iloc[max(0, i - 5)]
            if not pd.isna(ma60_prev) and ma60 > ma60_prev:
                signals.append("MA60向上(中期趋势良好)")
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

        # ── 新增：动能衰竭风险检测（不扣分，仅标注风险）──
        if i >= 2:
            # DIF连续下行（虽然仍>DEA，但趋势在减弱）
            if dif.iloc[i] < dif.iloc[i - 1] < dif.iloc[i - 2]:
                extra_risks.append({
                    "type": "trend_weakening",
                    "level": "warn",
                    "label": "DIF走弱",
                    "desc": "DIF连续3日下行，上涨动能减弱",
                })
            # MACD柱连续缩小
            if macd_bar.iloc[i] < macd_bar.iloc[i - 1] < macd_bar.iloc[i - 2]:
                extra_risks.append({
                    "type": "momentum_fade",
                    "level": "warn",
                    "label": "柱缩小",
                    "desc": "MACD柱连续3日缩小，多头动能衰竭",
                })

        # 阈值检查（收紧：75→90，控制命中数在合理范围）
        if score < 90:
            return None

        # ── 构建返回结果 ──
        latest = close.iloc[i]
        quote = self._get_quote(scanner, code, float(latest))
        pct = quote.get("涨跌幅", 0.0) or 0.0
        vr = float(vol_ratio.iloc[i]) if not pd.isna(vol_ratio.iloc[i]) else 1.0

        risk_flags = _compute_risk_flags(df)
        # 合并通用风险标签和策略自计算风险标签
        all_risk_flags = risk_flags + extra_risks

        return StockSignal(
            ts_code=code,
            name=name_map.get(code, code),
            strategy=self.name,
            score=min(score, 100),
            win_rate=None,
            signals=signals,
            latest_price=round(float(latest), 2),
            pct_chg=round(float(pct), 2),
            volume_ratio=round(vr, 2),
            risk_flags=all_risk_flags,
            trade_date=trade_date,
            extra={
                "dif": round(float(dif.iloc[i]), 4),
                "dea": round(float(dea.iloc[i]), 4),
                "macd": round(float(macd_bar.iloc[i]), 4),
                "ma5": round(float(ma5), 2) if not pd.isna(ma5) else None,
                "ma10": round(float(ma10), 2) if not pd.isna(ma10) else None,
                "ma20": round(float(ma20), 2) if not pd.isna(ma20) else None,
                "ma60": round(float(ma60), 2) if not pd.isna(ma60) else None,
                "cross_days": cross_days,
            }
        )
