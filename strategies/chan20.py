"""
缠20策略：MACD零轴下二次金叉 + SKDJ共振

核心逻辑：
  1. MACD在零轴下方（DIF<0, DEA<0）出现两次金叉
  2. SKDJ在低位（SK<30）形成金叉共振
  3. 底部形态确认，反弹概率高

评分规则：
  - MACD零轴下二次金叉：+40（核心信号）
  - SKDJ低位金叉（SK<30）：+30（共振确认）
  - SKDJ低位（20以下）：+15（超卖）
  - 价格站上5日均线：+10
  - 温和放量：+5

适用：底部反转、短线抄底
"""

import pandas as pd
import numpy as np
from typing import List
import logging

from .base import BaseStrategy, StockSignal, ScreenResult, _compute_risk_flags
from ..utils.indicators import calc_macd, calc_skdj, calc_ma, calc_volume_ratio
from ..data.fetcher import market_scanner, get_latest_trade_date

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

    def screen(self, stock_list: pd.DataFrame, scanner=None) -> ScreenResult:
        if scanner is None:
            scanner = market_scanner
        trade_date = get_latest_trade_date()
        scanner.load()
        name_map = self._get_name_map(stock_list)
        codes = self._get_codes(stock_list)

        candidates: List[StockSignal] = []
        scanned = 0

        for code in codes:
            try:
                df = scanner.get_history(code, days=120)
                if df is None or len(df) < 60:
                    continue

                scanned += 1
                close = df["close"]
                high = df["high"]
                low = df["low"]
                vol = df["vol"]

                i = len(df) - 1

                # 计算指标
                dif, dea, macd_bar = calc_macd(close)
                sk, sd = calc_skdj(close, high, low)
                mas = calc_ma(close, [5, 10, 20])
                vol_ratio = calc_volume_ratio(vol, 5)

                if pd.isna(dif.iloc[i]) or pd.isna(dea.iloc[i]) or pd.isna(sk.iloc[i]) or pd.isna(sd.iloc[i]):
                    continue

                signals = []
                score = 0

                # ── 核心条件1: MACD零轴下二次金叉 ──
                zero_crosses = _detect_macd_crosses_below_zero(dif, dea)
                # 需要至少两次金叉，且最近一次金叉在5个交易日内
                if len(zero_crosses) >= 2 and (i - zero_crosses[-1]) <= 5:
                    signals.append("MACD零轴下二次金叉")
                    score += 40
                elif len(zero_crosses) >= 1 and (i - zero_crosses[-1]) <= 3:
                    signals.append("MACD零轴下金叉")
                    score += 25
                else:
                    continue  # 不满足核心条件，跳过

                # ── 核心条件2: SKDJ共振 ──
                sk_val = sk.iloc[i]
                sd_val = sd.iloc[i]
                sk_prev = sk.iloc[i - 1]
                sd_prev = sd.iloc[i - 1]

                # SKDJ金叉
                if sk_prev <= sd_prev and sk_val > sd_val:
                    if sk_val < 30:
                        signals.append("SKDJ低位金叉")
                        score += 30
                    else:
                        signals.append("SKDJ金叉")
                        score += 15

                # SKDJ超卖区
                if sk_val < 20:
                    signals.append("SKDJ超卖")
                    score += 15
                elif sk_val < 30:
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

                # 阈值过滤（核心策略需要至少满足一个核心条件）
                if score < 45:
                    continue

                latest = close.iloc[i]
                quote = self._get_quote(scanner, code, float(latest))
                pct = quote.get("涨跌幅", 0.0) or 0.0

                win_rate = self._calc_win_rate(score, signals)
                risk_flags = _compute_risk_flags(df)
                candidates.append(StockSignal(
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
                ))

            except Exception as e:
                logger.debug(f"[缠20策略] {code} 计算失败: {e}")

        return self._build_result(candidates, trade_date, scanned)
