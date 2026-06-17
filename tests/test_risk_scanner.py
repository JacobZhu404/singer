"""risk_scanner 模块单测：验证拆分后行为一致性"""

import pandas as pd
import pytest

from stock_screener.core import risk_scanner


def test_quick_risk_scan_short_returns_unknown(sample_kline):
    short = sample_kline.head(5).reset_index().rename(columns={"index": "date"})
    short["pct_chg"] = 0.0
    tag, reasons, score, flags = risk_scanner.quick_risk_scan("000001", short)
    assert tag == "unknown"
    assert score == 0
    assert flags == []


def test_quick_risk_scan_full_returns_tag(sample_kline):
    df = sample_kline.reset_index().rename(columns={"index": "date"})
    df["pct_chg"] = df["close"].pct_change().fillna(0) * 100
    tag, reasons, score, flags = risk_scanner.quick_risk_scan("000001", df)
    assert tag in {"safe", "watch", "conflict", "high_risk"}
    assert isinstance(reasons, list)
    assert 0 <= score <= 100
    assert isinstance(flags, list)


def test_quick_sell_scan_short_returns_default(sample_kline):
    short = sample_kline.head(5).reset_index().rename(columns={"index": "date"})
    out = risk_scanner.quick_sell_scan("000001", short)
    assert out["has_sell_signal"] is False
    assert out["risk_level"] == "unknown"


def test_quick_buy_risk_short_returns_default(sample_kline):
    short = sample_kline.head(5).reset_index().rename(columns={"index": "date"})
    out = risk_scanner.quick_buy_risk_assess("000001", short)
    assert out["buy_risk"] == "unknown"
    assert out["adjustment"] == 0


def test_quick_risk_scan_handles_bad_input():
    # df 缺列时不应抛，应吞异常返回 unknown
    bad = pd.DataFrame({"close": [1, 2, 3] * 20})  # 缺 high/low/vol
    tag, reasons, score, flags = risk_scanner.quick_risk_scan("000001", bad)
    assert tag == "unknown"
