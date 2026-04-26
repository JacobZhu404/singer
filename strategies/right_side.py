"""
策略4: 右侧交易
原理：等待股票从底部启动，突破关键阻力位后介入
条件：
  1. 突破近20日高点（或60日高点）
  2. 突破时放量（量比 > 1.5）
  3. 突破前有明显调整（非追高）
  4. MA5 上穿 MA20（均线金叉）
  5. 股价站上 MA60（长期趋势向上）
  6. RSI 在 50~70 区间（强势但未超买）
"""

import pandas as pd
import numpy as np
import logging

from .base import BaseStrategy, StockSignal, ScreenResult, _compute_risk_flags
from ..utils.indicators import (
    calc_macd, calc_ma, calc_volume_ratio, calc_rsi
)
from ..data.fetcher import market_scanner, get_latest_trade_date

logger = logging.getLogger(__name__)


class RightSideTradingStrategy(BaseStrategy):
    name = "right_side"
    description = "右侧交易 - 突破关键阻力位+放量+均线金叉"
    base_win_rate = 0.56

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
                df = scanner.get_history(code, days=80)
                if df is None or len(df) < 30:
                    continue

                scanned += 1
                close = df["close"]
                high = df["high"]
                vol = df["vol"]
                i = len(df) - 1

                mas = calc_ma(close, [5, 10, 20, 60])
                vol_ratio = calc_volume_ratio(vol, 5)
                rsi = calc_rsi(close, 14)
                dif, dea, _ = calc_macd(close)

                signals = []
                score = 0
                ma5 = mas["ma5"].iloc[i]
                ma20 = mas["ma20"].iloc[i]
                ma60 = mas["ma60"].iloc[i]
                ma5_prev = mas["ma5"].iloc[i-1] if i >= 1 else None
                ma20_prev = mas["ma20"].iloc[i-1] if i >= 1 else None

                if any(pd.isna(x) for x in [ma5, ma20, ma60]):
                    continue

                c = float(close.iloc[i])
                vr = float(vol_ratio.iloc[i]) if not pd.isna(vol_ratio.iloc[i]) else 1.0

                if i >= 20:
                    high_20 = float(high.iloc[i-20:i].max())
                    if c > high_20:
                        signals.append(f"突破20日新高({high_20:.2f})")
                        score += 25

                if vr > 1.5:
                    signals.append(f"突破放量(量比{vr:.1f}x)")
                    score += 20

                if (ma5_prev is not None and ma20_prev is not None and
                        not pd.isna(ma5_prev) and not pd.isna(ma20_prev) and
                        float(ma5) > float(ma20) and float(ma5_prev) <= float(ma20_prev)):
                    signals.append("MA5上穿MA20金叉")
                    score += 20

                if c > float(ma60):
                    signals.append("股价站上MA60")
                    score += 15

                r = float(rsi.iloc[i]) if not pd.isna(rsi.iloc[i]) else 50
                if 50 <= r <= 70:
                    signals.append(f"RSI强势区间({r:.0f})")
                    score += 10

                if not pd.isna(dif.iloc[i]) and dif.iloc[i] > 0:
                    signals.append("MACD零轴以上")
                    score += 10

                if i >= 5:
                    gain_5d = (c - float(close.iloc[i-5])) / float(close.iloc[i-5]) * 100
                    if gain_5d < 10:
                        signals.append("启动未过热")
                        score += 10

                if score < 40:
                    continue

                quote = self._get_quote(scanner, code, c)
                candidates.append(StockSignal(
                    ts_code=code,
                    name=name_map.get(code, code),
                    strategy=self.name,
                    score=score,
                    win_rate=self._calc_win_rate(score, signals),
                    signals=signals,
                    latest_price=round(float(quote.get("最新价", c)), 2),
                    pct_chg=round(float(quote.get("涨跌幅", 0.0)), 2),
                    volume_ratio=round(vr, 2),
                    risk_flags=_compute_risk_flags(df),
                    trade_date=trade_date,
                    extra={
                        "ma5": round(float(ma5), 2),
                        "ma20": round(float(ma20), 2),
                        "ma60": round(float(ma60), 2),
                        "rsi14": round(r, 1),
                    }
                ))

            except Exception as e:
                logger.debug(f"[右侧交易策略] {code} 计算失败: {e}")

        return self._build_result(candidates, trade_date, scanned)
