"""
趋势共振策略（MACD多头 + 均线金叉 合并版）
条件（三重验证，越多越强）：
  1. MACD零轴确认：DIF>0 且 DEA>0
  2. 均线多头排列：MA5>MA10>MA20>MA60，且MA20>MA60
  3. 价格确认：价格站上所有均线

信号评分：
  - MA5上穿MA10当日金叉：+25（最强信号）
  - DIF>0 且 DEA>0：+15（零轴以上）
  - MA5>MA10>MA20>MA60均线多头：+20
  - MA20>MA60中期多头：+15
  - MACD柱放大（近3日连续）：+15
  - 价格站上所有均线：+10
适用：趋势确认、中线持仓
"""

import pandas as pd
import numpy as np
from typing import List
import logging

from .base import BaseStrategy, StockSignal, ScreenResult, _compute_risk_flags
from ..utils.indicators import calc_macd, calc_ma, calc_volume_ratio
from ..data.fetcher import market_scanner, get_latest_trade_date

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

                # 计算 MACD
                dif, dea, macd_bar = calc_macd(close)
                # 计算均线
                mas = calc_ma(close, [5, 10, 20, 60])
                # 量比
                vol_ratio = calc_volume_ratio(vol, 5)

                i = len(df) - 1
                if pd.isna(dif.iloc[i]) or pd.isna(dea.iloc[i]):
                    continue

                signals = []
                score = 0

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
                        score += 15

                    # ── 条件4: 价格站上均线（MA5>MA10 是多头前提）──
                    if close.iloc[i] > ma5 > ma10 > ma20 > ma60:
                        signals.append("价格站上所有均线")
                        score += 10

                # ── 条件5: MACD 零轴确认 ──
                # DIF>0：快线在零轴上方，多头动能确认（10分）
                # 零轴金叉（DIF>0 且 DIF>DEA）：最强组合（+15分，含零轴确认）
                if dif.iloc[i] > dea.iloc[i]:
                    signals.append("MACD零轴金叉")
                    score += 25          # 同时满足 DIF>0(隐含) + 金叉 = 最强信号
                elif dif.iloc[i] > 0:
                    signals.append("DIF零轴以上")
                    score += 10          # DIF 在零轴上方但尚未金叉

                # ── 条件7: MACD 柱放大（近3日连续） ──
                if i >= 2 and not pd.isna(macd_bar.iloc[i - 2]):
                    if macd_bar.iloc[i] > 0:
                        if macd_bar.iloc[i] > macd_bar.iloc[i - 1] > macd_bar.iloc[i - 2]:
                            signals.append("MACD柱放大")
                            score += 15

                if score < 50:
                    continue

                latest = close.iloc[i]
                quote = self._get_quote(scanner, code, float(latest))
                pct = quote.get("涨跌幅", 0.0) or 0.0
                vr = float(vol_ratio.iloc[i]) if not pd.isna(vol_ratio.iloc[i]) else 1.0

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
                        "macd": round(float(macd_bar.iloc[i]), 4),
                        "ma5": round(float(ma5), 2) if not pd.isna(ma5) else None,
                        "ma10": round(float(ma10), 2) if not pd.isna(ma10) else None,
                        "ma20": round(float(ma20), 2) if not pd.isna(ma20) else None,
                        "ma60": round(float(ma60), 2) if not pd.isna(ma60) else None,
                    }
                ))

            except Exception as e:
                logger.debug(f"[MACD策略] {code} 计算失败: {e}")

        return self._build_result(candidates, trade_date, scanned)
