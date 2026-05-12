"""
均线金叉策略（宽松模式）
注意：本策略是 macd_bull 的宽松子集，门槛更低，选股范围更大。
      macd_bull 要求 MA5>MA10>MA20>MA60 四线多头 + MACD柱放大 + DIF>DEA金叉，
      本策略仅要求 MA5>MA10>MA20 三线 + RSI 50~65 + DIF>0。
      满足 macd_bull 的股票几乎必然满足 golden_cross，反之不成立。

原理：MA5 从下穿越 MA10，形成黄金交叉，视为短期趋势转多信号。
条件：
  1. MA5 上穿 MA10（金叉当天）
  2. MA5 > MA10 > MA20（多头排列，不含MA60）
  3. 股价站上 MA5（顺势确认）
  4. RSI 在 50~65（趋势确认但未超买）
适用：趋势启动初期、短线波段（比 macd_bull 更早入场，但假信号更多）
"""

import pandas as pd
import numpy as np
from typing import List
import logging

from .base import BaseStrategy, StockSignal, ScreenResult, _compute_risk_flags
from ..data.fetcher import market_scanner, get_latest_trade_date

logger = logging.getLogger(__name__)


class GoldenCrossStrategy(BaseStrategy):
    """均线金叉策略（macd_bull宽松模式：少MA60/MACD柱放大/RSI硬过滤）"""
    name = "golden_cross"
    description = "均线金叉(macd_bull宽松版) - 仅3线多头+RSI确认，适合趋势初期"
    base_win_rate = 0.55

    def __init__(self, top_n: int = 10):
        super().__init__(top_n=top_n)
        self._cache: set = set()

    def screen(self, stock_list: pd.DataFrame, scanner=None) -> ScreenResult:
        if scanner is None:
            scanner = market_scanner
        trade_date = get_latest_trade_date()
        scanner.load()
        name_map = self._get_name_map(stock_list)

        candidates: List[StockSignal] = []
        scanned = 0

        for code in self._get_codes(stock_list):
            if code in self._cache:
                continue
            try:
                indicators = scanner.get_indicators(code, days=60)
                if not indicators or len(indicators["kline"]) < 30:
                    continue

                scanned += 1
                self._report_progress("executing", scanned, len(self._get_codes(stock_list)))
                df = indicators["kline"]
                close = df["close"]
                i = len(df) - 1

                mas = indicators["ma"]
                ma5 = mas["ma5"]
                ma10 = mas["ma10"]
                ma20 = mas["ma20"]
                rsi = indicators["rsi"]
                dif, dea, _ = indicators["macd"]
                vol_ratio = indicators["vol_ratio"]

                m5 = float(ma5.iloc[i])
                m10 = float(ma10.iloc[i])
                m20 = float(ma20.iloc[i])
                m5_prev = float(ma5.iloc[i-1]) if i >= 1 else m5
                m10_prev = float(ma10.iloc[i-1]) if i >= 1 else m10

                if any(np.isnan(x) for x in [m5, m10, m20]):
                    continue

                signals = []
                score = 0

                # 条件1：MA5 上穿 MA10（金叉当天）
                if m5 > m10 and m5_prev <= m10_prev:
                    signals.append("MA5上穿MA10金叉")
                    score += 40
                elif m5 > m10:
                    signals.append("MA5在MA10上方")
                    score += 20

                # 条件2：多头排列（MA5 > MA10 > MA20）
                if m5 > m10 > m20:
                    signals.append("均线多头排列")
                    score += 25

                # 条件3：股价站上 MA5
                c = float(close.iloc[i])
                if c > m5:
                    signals.append("股价站上MA5")
                    score += 15

                # 条件4：RSI 确认
                r = float(rsi.iloc[i]) if not np.isnan(rsi.iloc[i]) else 50
                if 50 <= r <= 65:
                    signals.append(f"RSI趋势确认({r:.0f})")
                    score += 10
                elif r < 50:
                    signals.append(f"RSI偏弱({r:.0f})")
                    score -= 10

                # MACD 在零轴上方加分
                d = float(dif.iloc[i]) if not np.isnan(dif.iloc[i]) else 0
                if d > 0:
                    signals.append("MACD零轴上方")
                    score += 10

                if score < 70:
                    continue

                quote = self._get_quote(scanner, code, c)
                vr = float(vol_ratio.iloc[i]) if not np.isnan(vol_ratio.iloc[i]) else 1.0

                candidates.append(StockSignal(
                    ts_code=code,
                    name=name_map.get(code, code),
                    strategy=self.name,
                    score=min(max(score, 0), 100),
                    win_rate=self._calc_win_rate(score, signals),
                    signals=signals,
                    latest_price=float(quote.get("最新价", c)),
                    pct_chg=float(quote.get("涨跌幅", 0.0)),
                    volume_ratio=round(vr, 2),
                    risk_flags=_compute_risk_flags(df),
                    trade_date=trade_date,
                    extra={
                        "ma5": round(m5, 2),
                        "ma10": round(m10, 2),
                        "ma20": round(m20, 2),
                        "rsi14": round(r, 1),
                    },
                ))
                self._cache.add(code)

            except Exception as e:
                logger.debug(f"[金叉策略] {code} 计算失败: {e}")

        return self._build_result(candidates, trade_date, scanned)
