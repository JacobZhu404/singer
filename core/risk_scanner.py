"""
轻量风险/卖出/买入风险扫描

仅读取已缓存的 K 线数据（不发新网络请求），供 ScreenEngine 在合并阶段调用。
从 engine.py 拆出，便于单测和复用。
"""

import logging
from typing import Optional

import pandas as pd

from ..data.fetcher import market_scanner
from ..utils.indicators import calc_risk_flags
from ..utils.sell_signals import detect_sell_signals, assess_buy_risk
from .constants import (
    RISK_SCAN_MIN_BARS,
    SELL_SCAN_MIN_BARS,
    BUY_RISK_MIN_BARS,
)
from .observability import obs

logger = logging.getLogger(__name__)


def _report_scan_failure(scan_name: str, code: str, e: Exception) -> None:
    """风险扫描类辅助函数共用的失败上报：warning 日志 + obs 事件，不向上抛出。"""
    logger.warning(f"{scan_name} {code} 失败: {type(e).__name__}: {e}")
    try:
        obs.error("risk_scanner", scan_name, f"{type(e).__name__}: {e}",
                  context={"code": code}, exc=e)
    except Exception:
        pass


_EMPTY_SELL = {
    "has_sell_signal": False,
    "sell_signals": [],
    "sell_score": 0,
    "stop_loss_price": None,
    "take_profit_price": None,
    "risk_level": "unknown",
}

_EMPTY_BUY_RISK = {
    "buy_risk": "unknown",
    "risk_reasons": [],
    "adjustment": 0,
}


def quick_risk_scan(code: str, df: Optional[pd.DataFrame] = None):
    """
    轻量风险扫描：根据 K 线快速判断卖出风险。

    Returns: (tag, reasons, score, flags)
      tag ∈ {"safe","watch","conflict","high_risk","unknown"}
    """
    try:
        if df is None:
            df = market_scanner.get_history(code, days=60)
        if df is None or df.empty or len(df) < RISK_SCAN_MIN_BARS:
            return "unknown", [], 0, []

        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        vol = df["vol"].astype(float)
        pct_col = "pct_chg" if "pct_chg" in df.columns else "daily_chg"
        pct_chg = df[pct_col].astype(float) if pct_col in df.columns else pd.Series(0, index=close.index)

        flags = calc_risk_flags(close, high, low, vol, pct_chg)
        score = sum(25 if f["level"] == "danger" else 12 for f in flags)
        score = min(score, 100)
        reasons = [f["desc"] for f in flags]

        if score >= 50:
            tag = "high_risk"
        elif score >= 28:
            tag = "conflict"
        elif score >= 12:
            tag = "watch"
        else:
            tag = "safe"

        return tag, reasons, score, flags

    except Exception as e:
        _report_scan_failure("quick_risk_scan", code, e)
        return "unknown", [], 0, []


def quick_sell_scan(code: str, df: Optional[pd.DataFrame] = None) -> dict:
    """卖出信号快速扫描：返回 detect_sell_signals 结果。"""
    try:
        if df is None:
            df = market_scanner.get_history(code, days=60)
        if df is None or df.empty or len(df) < SELL_SCAN_MIN_BARS:
            return dict(_EMPTY_SELL)
        return detect_sell_signals(df)
    except Exception as e:
        _report_scan_failure("quick_sell_scan", code, e)
        return dict(_EMPTY_SELL)


def quick_buy_risk_assess(code: str, df: Optional[pd.DataFrame] = None) -> dict:
    """买入风险评估：返回 assess_buy_risk 结果。"""
    try:
        if df is None:
            df = market_scanner.get_history(code, days=60)
        if df is None or df.empty or len(df) < BUY_RISK_MIN_BARS:
            return dict(_EMPTY_BUY_RISK)
        return assess_buy_risk(df)
    except Exception as e:
        _report_scan_failure("quick_buy_risk_assess", code, e)
        return dict(_EMPTY_BUY_RISK)
