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
from ..utils.indicators import td_sequential_count, calc_macd, calc_ma
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

        for code in self._get_codes(stock_list):
            try:
                df = scanner.get_history(code, days=60)
                if df is None or len(df) < 20:
                    continue

                scanned += 1
                close = df["close"]
                high = df["high"]
                vol = df["vol"]

                # 传入 high 以启用卖出九转的完美Bar校验
                td_count = td_sequential_count(close, high=high)
                dif, dea, _ = calc_macd(close)
                mas = calc_ma(close, [5, 20])
                i = len(df) - 1

                current_count = int(td_count.iloc[i])
                # 当前策略只关注买入九转（正数）；卖出序列（负数）跳过
                if current_count <= 0:
                    continue
                if current_count == 9:
                    signals, score = ["买入九转完成(count=9)"], 50
                elif current_count in (7, 8):
                    signals, score = [f"九转进行中(count={current_count})"], 25
                else:
                    continue

                if i >= 2 and float(close.iloc[i]) > max(float(close.iloc[i-1]), float(close.iloc[i-2])):
                    signals.append("价格上穿确认")
                    score += 20

                if i >= 5:
                    avg_vol = float(vol.iloc[i-5:i].mean())
                    if avg_vol > 0 and float(vol.iloc[i]) > avg_vol * 1.2:
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
                avg_vol = float(vol.iloc[i-5:i].mean()) if i >= 5 else 0
                vol_ratio = round(float(vol.iloc[i]) / avg_vol, 2) if avg_vol > 0 else 1.0

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
