"""
缠20策略：MACD零轴下二次金叉 + SKDJ共振

核心逻辑：
  1. MACD在零轴下方（DIF<0, DEA<0）出现两次金叉
  2. SKDJ在低位（SK<25）形成金叉共振
  3. 底部形态确认，反弹概率高

评分规则（已收紧）：
  - MACD零轴下二次金叉（3日内）：+40（核心信号）
  - SKDJ低位金叉（SK<25）：+30（共振确认）
  - SKDJ低位（20以下）：+15（超卖）
  - 价格站上5日均线：+10
  - 温和放量：+5

适用：底部反转、短线抄底
"""

import pandas as pd
import numpy as np
from typing import List
import logging

from .base import BaseStrategy, StockSignal, _compute_risk_flags
from ..utils.indicators import calc_macd, calc_skdj, calc_ma

logger = logging.getLogger(__name__)


def _detect_macd_crosses_below_zero(dif: pd.Series, dea: pd.Series) -> List[int]:
    """
    检测MACD在零轴下方的所有金叉位置（DIF上穿DEA）
    返回金叉发生的索引列表
    """
    crosses = []
    for i in range(1, len(dif)):
        if pd.isna(dif.iloc[i]) or pd.isna(dea.iloc[i]):
            continue
        prev_dif = dif.iloc[i - 1]
        prev_dea = dea.iloc[i - 1]
        curr_dif = dif.iloc[i]
        curr_dea = dea.iloc[i]
        # 金叉：前一日DIF<=DEA，当日DIF>DEA，且都在零轴下方
        if prev_dif <= prev_dea and curr_dif > curr_dea:
            if curr_dif < 0 and curr_dea < 0:
                crosses.append(i)
    return crosses


class Chan20Strategy(BaseStrategy):
    """
    缠20策略：MACD零轴下二次金叉 + SKDJ低位共振
    用于捕捉底部反转机会
    """
    name = "chan20"
    description = "MACD零轴下二次金叉+SKDJ低位共振，底部反转信号"
    base_win_rate = 0.55

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date):
        indicators = scanner.get_indicators(code, days=120)
        if not indicators or len(indicators["kline"]) < 60:
            raise self._SkipStock()

        df = indicators["kline"]
        close = df["close"]
        high = df["high"]
        low = df["low"]

        i = len(df) - 1

        # 查表获取预计算指标
        dif, dea, macd_bar = indicators["macd"]
        sk, sd = indicators["skdj"]
        mas = indicators["ma"]
        vol_ratio = indicators["vol_ratio"]

        if pd.isna(dif.iloc[i]) or pd.isna(dea.iloc[i]) or pd.isna(sk.iloc[i]) or pd.isna(sd.iloc[i]):
            raise self._SkipStock()

        signals = []
        score = 0

        # ── 核心条件1: MACD零轴下二次金叉 ──
        zero_crosses = _detect_macd_crosses_below_zero(dif, dea)
        # tightened: 二次金叉要求最近一次在3日内，单次金叉在2日内
        if len(zero_crosses) >= 2 and (i - zero_crosses[-1]) <= 3:
            signals.append("MACD零轴下二次金叉")
            score += 40
        elif len(zero_crosses) >= 1 and (i - zero_crosses[-1]) <= 2:
            signals.append("MACD零轴下金叉")
            score += 25
        else:
            return None  # 不满足核心条件，跳过

        # ── 核心条件2: SKDJ共振 ──
        sk_val = sk.iloc[i]
        sd_val = sd.iloc[i]
        sk_prev = sk.iloc[i - 1]
        sd_prev = sd.iloc[i - 1]

        # SKDJ金叉（tightened: SK<25）
        if sk_prev <= sd_prev and sk_val > sd_val:
            if sk_val < 25:
                signals.append("SKDJ低位金叉")
                score += 30
            else:
                signals.append("SKDJ金叉")
                score += 15

        # SKDJ超卖区（tightened: 低位阈值<25）
        if sk_val < 20:
            signals.append("SKDJ超卖")
            score += 15
        elif sk_val < 25:
            signals.append("SKDJ低位")
            score += 10

        # ── 辅助条件: 价格站上5日均线 ──
        ma5 = mas["ma5"].iloc[i]
        ma10 = mas["ma10"].iloc[i]
        if not pd.isna(ma5) and close.iloc[i] > ma5:
            signals.append("站上5日线")
            score += 10
            if not pd.isna(ma10) and ma5 > ma10:
                signals.append("MA5>MA10")
                score += 5

        # ── 辅助条件: 温和放量 ──
        vr = float(vol_ratio.iloc[i]) if not pd.isna(vol_ratio.iloc[i]) else 1.0
        if vr > 1.2:
            signals.append("温和放量")
            score += 5

        # 阈值过滤（tightened: 最低分从45提高到55）
        if score < 55:
            return None

        latest = close.iloc[i]
        quote = self._get_quote(scanner, code, float(latest))
        pct = quote.get("涨跌幅", 0.0) or 0.0

        win_rate = self._calc_win_rate(score, signals)
        risk_flags = _compute_risk_flags(df)
        return StockSignal(
            ts_code=code,
            name=name_map.get(code, code),
            strategy=self.name,
            score=score,
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
                "sk": round(float(sk_val), 2),
                "sd": round(float(sd_val), 2),
                "ma5": round(float(ma5), 2) if not pd.isna(ma5) else None,
                "macd_crosses": len(zero_crosses),
            }
        )
