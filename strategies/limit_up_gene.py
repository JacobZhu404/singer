"""
策略5: 涨停基因
原理：
  具有"涨停基因"的股票容易反复涨停
  条件：
  1. 近30日内有过涨停记录（至少1次）
  2. 涨停后未大幅回落（最大回撤 < 15%）
  3. 当前处于连板梯队（或近期再次启动）
  4. 所在题材/板块仍在活跃中
  5. 当日量比 > 1.5（热点关注）
"""

import pandas as pd
import numpy as np
import logging
from typing import Optional, List
from datetime import datetime, timedelta

from .base import BaseStrategy, StockSignal, _compute_risk_flags
from ..utils.indicators import calc_macd, calc_ma

logger = logging.getLogger(__name__)


class LimitUpGeneStrategy(BaseStrategy):
    name = "limit_up_gene"
    description = "涨停基因 - 近期有涨停记录+题材活跃+再次启动"
    base_win_rate = 0.65

    def _get_codes(self, stock_list: pd.DataFrame) -> List[str]:
        from ..data.fetcher import get_limit_list, get_latest_trade_date
        trade_date = get_latest_trade_date()
        limit_df = get_limit_list(trade_date)
        if limit_df.empty:
            return []
        code_col = next((c for c in ["symbol", "code", "代码", "ts_code"] if c in limit_df.columns), None)
        if code_col:
            limit_df = limit_df.copy()
            limit_df["ts_code"] = limit_df[code_col].astype(str).str.zfill(6)
            return limit_df["ts_code"].tolist()[:200]
        return []

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date):
        indicators = scanner.get_indicators(code, days=120)
        if not indicators or len(indicators["kline"]) < 10:
            raise self._SkipStock()

        df = indicators["kline"]
        close = df["close"]
        high = df["high"]
        vol = df["vol"]
        pct_chg_series = close.pct_change() * 100
        i = len(df) - 1
        signals = []
        score = 0

        today_pct = float(pct_chg_series.iloc[i]) if i >= 0 else 0
        if today_pct >= 9.5:
            signals.append(f"今日涨停({today_pct:.1f}%)")
            score += 40
        elif today_pct >= 8:
            signals.append(f"接近涨停({today_pct:.1f}%)")
            score += 25

        limit_days = sum(1 for j in range(max(0, i - 30), i + 1) if pct_chg_series.iloc[j] >= 9.5)
        if limit_days >= 1:
            signals.append(f"近30日涨停{limit_days}次")
            score += min(limit_days * 10, 20)

        consecutive = 0
        if i >= 2:
            consecutive = sum(1 for j in range(i - 2, i + 1) if pct_chg_series.iloc[j] >= 5)
            if consecutive >= 3:
                signals.append(f"三连阳({consecutive}天)")
                score += 15

        if i >= 1:
            recent_high = float(high.iloc[max(0, i-5):i+1].max())
            current_close = float(close.iloc[i])
            if recent_high > 0:
                drawdown = (recent_high - current_close) / recent_high * 100
                if drawdown < 15:
                    signals.append(f"涨停后回撤小({drawdown:.1f}%)")
                    score += 10

        vol_ratio = indicators["vol_ratio"]
        vr = float(vol_ratio.iloc[i]) if not pd.isna(vol_ratio.iloc[i]) else 1.0
        if vr > 1.5:
            signals.append(f"当日放量(量比{vr:.1f}x)")
            score += 10

        dif, dea, _ = indicators["macd"]
        if not pd.isna(dif.iloc[i]) and dif.iloc[i] > 0:
            signals.append("MACD零轴以上")
            score += 10

        if score < 40:
            return None

        latest = float(close.iloc[i])
        quote = self._get_quote(scanner, code, latest)

        return StockSignal(
            ts_code=code,
            name=name_map.get(code, code),
            strategy=self.name,
            score=score,
            win_rate=self._calc_win_rate(score, signals),
            signals=signals,
            latest_price=round(float(quote.get("最新价", latest)), 2),
            pct_chg=round(float(quote.get("涨跌幅", 0.0)), 2),
            volume_ratio=round(vr, 2),
            risk_flags=_compute_risk_flags(df),
            trade_date=trade_date,
            extra={
                "limit_count": limit_days,
                "consecutive_days": consecutive,
            }
        )
