"""
策略3: 神奇九转 (TD Sequential)
原理：
  - 买入九转：连续9天收盘价 < 4天前收盘价，第9根完美Bar确认后发出买入信号
  - 完美Bar：第9根收盘价 < 第8根最低（否则视为等待，不触发）
  - 序列切换时计数归零重计，不直接跳转到反向序列
  - 本策略只关注买入九转（用于选股）
"""

import pandas as pd
import numpy as np
import logging

from .base import BaseStrategy, StockSignal, ScreenResult, _compute_risk_flags
from ..data.fetcher import market_scanner, get_latest_trade_date

logger = logging.getLogger(__name__)


class TDSequentialStrategy(BaseStrategy):
    name = "td_sequential"
    description = "神奇九转 - TD Sequential 买入九转信号（9计数完成+确认）"
    base_win_rate = 0.60

    def screen(self, stock_list: pd.DataFrame, scanner=None) -> ScreenResult:
        if scanner is None:
            scanner = market_scanner
        trade_date = get_latest_trade_date()
        scanner.load()
        name_map = self._get_name_map(stock_list)

        candidates = []
        scanned = 0
        codes = self._get_codes(stock_list)

        for code in codes:
            try:
                indicators = scanner.get_indicators(code, days=120)
                if not indicators or len(indicators["kline"]) < 20:
                    continue

                scanned += 1
                self._report_progress("executing", scanned, len(codes))
                df = indicators["kline"]
                close = df["close"]
                high = df["high"]
                low = df["low"]
                td_count = indicators["td_count"]
                dif, dea, _ = indicators["macd"]
                mas = indicators["ma"]
                vol_ratio_series = indicators["vol_ratio"]
                i = len(df) - 1

                current_count = int(td_count.iloc[i])
                # 当前策略只关注买入九转（正数）；卖出序列（负数）跳过
                if current_count <= 0:
                    continue
                if current_count == 9:
                    signals, score = ["买入九转完成(count=9)"], 50
                # 标准TD Sequential仅在 Setup 完成（count=9）时产生信号
                # count=7/8 为"九转进行中"，属于提前押注，胜率低于标注的60%
                # 为避免假信号，仅在 count==9 时进入候选，7/8 仅作观察跳过
                elif current_count in (7, 8):
                    continue
                else:
                    continue

                if i >= 2 and float(close.iloc[i]) > max(float(close.iloc[i-1]), float(close.iloc[i-2])):
                    signals.append("价格上穿确认")
                    score += 20

                if i >= 5 and not pd.isna(vol_ratio_series.iloc[i]) and float(vol_ratio_series.iloc[i]) > 1.2:
                    signals.append("成交量确认放大")
                    score += 15

                if not pd.isna(dif.iloc[i]) and not pd.isna(dea.iloc[i]) and \
                   dif.iloc[i] > dea.iloc[i] and i >= 1 and dif.iloc[i-1] <= dea.iloc[i-1]:
                    signals.append("MACD金叉确认")
                    score += 15

                if not pd.isna(mas["ma5"].iloc[i]) and float(close.iloc[i]) >= float(mas["ma5"].iloc[i]) * 0.99:
                    signals.append("MA5支撑")
                    score += 10

                latest = close.iloc[i]
                quote = self._get_quote(scanner, code, float(latest))
                vol_ratio = round(float(vol_ratio_series.iloc[i]), 2) if not pd.isna(vol_ratio_series.iloc[i]) else 1.0

                candidates.append(StockSignal(
                    ts_code=code,
                    name=name_map.get(code, code),
                    strategy=self.name,
                    score=score,
                    win_rate=self._calc_win_rate(score, signals),
                    signals=signals,
                    latest_price=round(float(quote.get("最新价", latest)), 2),
                    pct_chg=round(float(quote.get("涨跌幅", 0.0)), 2),
                    volume_ratio=vol_ratio,
                    risk_flags=_compute_risk_flags(df),
                    trade_date=trade_date,
                    extra={
                        "td_count": current_count,
                        "dif": round(float(dif.iloc[i]), 4) if not pd.isna(dif.iloc[i]) else None,
                    }
                ))

            except Exception as e:
                logger.debug(f"[九转策略] {code} 计算失败: {e}")

        return self._build_result(candidates, trade_date, scanned)


        return self._build_result(candidates, trade_date, scanned)