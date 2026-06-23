"""realtime_merge 合并语义测试：杜绝 §C2 缓存污染回归。"""

import pandas as pd
import pytest

from stock_screener.data.realtime_merge import (
    merge_realtime_into_history,
    normalize_quote,
)


def _make_history(rows: int = 10, last_date: str = "2026-06-17") -> pd.DataFrame:
    dates = pd.date_range(end=last_date, periods=rows, freq="D")
    return pd.DataFrame({
        "date": dates,
        "open": [10.0] * rows,
        "close": [10.0] * rows,
        "high": [10.5] * rows,
        "low": [9.5] * rows,
        "vol": [1000000.0] * rows,
    })


def test_empty_history_returns_empty():
    """空历史不该被实时一根线"复活"为 1 行（否则就是 C2 污染）。"""
    df = merge_realtime_into_history(pd.DataFrame(), {"price": 12.5})
    assert df.empty, "空历史必须返回空，不能伪造历史"


def test_full_history_keeps_history():
    """合并后必须保留全部历史 + 加一行今日（不能只剩 1 行）。"""
    history = _make_history(rows=20)
    quote = {"最新价": 11.0, "成交量": 5000, "今开": 10.8, "最高价": 11.2, "最低价": 10.7, "涨跌幅": 1.5}
    merged = merge_realtime_into_history(history, quote, today_str="2026-06-18", volume_unit_is_lots=True)
    assert len(merged) == 21, f"应 20 历史 + 1 今日 = 21 行，实际 {len(merged)}"
    assert merged["close"].iloc[-1] == 11.0
    assert merged["close"].iloc[-2] == 10.0  # 历史末日未被覆盖


def test_already_has_today_returns_unchanged():
    history = _make_history(rows=10, last_date="2026-06-18")
    merged = merge_realtime_into_history(history, {"price": 99.0}, today_str="2026-06-18")
    assert len(merged) == 10
    assert merged["close"].iloc[-1] != 99.0, "已含今日不该被覆盖"


def test_empty_quote_returns_history_unchanged():
    history = _make_history(rows=15)
    merged = merge_realtime_into_history(history, {}, today_str="2026-06-18")
    assert len(merged) == 15


def test_zero_price_quote_returns_history_unchanged():
    history = _make_history(rows=15)
    merged = merge_realtime_into_history(
        history, {"price": 0, "volume": 100}, today_str="2026-06-18"
    )
    assert len(merged) == 15


def test_chinese_keys_normalized():
    q = normalize_quote({"最新价": 12.3, "今开": 12.0, "最高价": 12.5, "最低价": 11.8, "成交量": 500, "涨跌幅": 2.5})
    assert q["price"] == 12.3
    assert q["open"] == 12.0
    assert q["pct"] == 2.5


def test_english_keys_normalized():
    q = normalize_quote({"price": 8.0, "open": 7.9, "high": 8.1, "low": 7.8, "volume": 200})
    assert q["price"] == 8.0
    assert q["open"] == 7.9
    assert q["vol"] == 200


def test_lots_to_shares_conversion():
    history = _make_history(rows=10)
    # 实时返回 100 手；转股 = 10000
    quote = {"最新价": 10.5, "成交量": 100, "涨跌幅": 1.0}
    merged = merge_realtime_into_history(history, quote, today_str="2026-06-18", volume_unit_is_lots=True)
    # 估算后的 vol 至少等于换算的 10000（盘外不估算时 == 10000）
    assert merged["vol"].iloc[-1] >= 10000
