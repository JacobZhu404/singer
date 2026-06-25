"""涨停基因 v2：真封板 + 回踩甜区 + 量价拐头"""

import threading

import numpy as np
import pandas as pd
import pytest

from stock_screener.strategies.limit_up_gene import LimitUpGeneStrategy


def _mk_kline(rows, n_pad=30):
    """rows = [(open, high, low, close), ...]；前补 n_pad 根稳定价。
    pad 段全部用 rows[0] 的 close 平铺，作为"基准底"。"""
    base = rows[0][3]
    pad = [(base, base * 1.005, base * 0.995, base)] * n_pad
    full = pad + list(rows)
    arr = np.array(full, dtype=float)
    return pd.DataFrame({
        "open": arr[:, 0],
        "high": arr[:, 1],
        "low":  arr[:, 2],
        "close": arr[:, 3],
        "vol": np.full(len(arr), 5e6),
    })


class FakeScanner:
    def __init__(self):
        self._lock = threading.Lock()
        self._cache = {}

    def load(self):
        return True

    def get_indicators(self, code, days=60, pure=False):
        df = self._cache.get(code)
        if df is None:
            return {}
        vr = pd.Series(np.full(len(df), 1.3), index=df.index)
        # 简化 MACD：DIF 全 0（零轴附近）
        dif = pd.Series(np.zeros(len(df)), index=df.index)
        dea = pd.Series(np.zeros(len(df)), index=df.index)
        bar = pd.Series(np.zeros(len(df)), index=df.index)
        return {"kline": df, "vol_ratio": vr, "macd": (dif, dea, bar)}

    def get_realtime(self, code):
        return {"涨跌幅": None, "最新价": None, "换手率": 0.0}


@pytest.fixture
def strat():
    return LimitUpGeneStrategy(top_n=10)


# ── 真封板检测 ────────────────────────────────────────────

def test_real_limit_up_close_at_high_passes(strat):
    """涨幅触板且 close==high → 视为真封板"""
    # 前一日 close=10，当日 close=11 high=11（封板）
    df = pd.DataFrame({
        "open":  [10, 10.0],
        "high":  [10, 11.0],
        "low":   [10, 10.0],
        "close": [10, 11.0],  # +10% 且 close==high
        "vol":   [1e6, 2e6],
    })
    assert strat._is_real_limit_up(df, 1, limit_pct=10.0) is True


def test_real_limit_up_rejects_intraday_touch_then_fall(strat):
    """盘中触板但收回 → 不算真封板"""
    df = pd.DataFrame({
        "open":  [10, 10.0],
        "high":  [10, 11.0],   # 盘中触板
        "low":   [10, 10.0],
        "close": [10, 10.5],   # 但收盘只涨 5%，且 close < high → 不算
        "vol":   [1e6, 2e6],
    })
    assert strat._is_real_limit_up(df, 1, limit_pct=10.0) is False


def test_real_limit_up_chinext_20pct(strat):
    """创业板 +20% close==high 才算"""
    df = pd.DataFrame({
        "open":  [10, 10.0],
        "high":  [10, 12.0],
        "low":   [10, 10.0],
        "close": [10, 12.0],
        "vol":   [1e6, 2e6],
    })
    assert strat._is_real_limit_up(df, 1, limit_pct=20.0) is True
    # +10% 在创业板不算封板
    df2 = df.copy(); df2.loc[1, ["close", "high"]] = 11.0
    assert strat._is_real_limit_up(df2, 1, limit_pct=20.0) is False


# ── 形态命中 / 不命中 ─────────────────────────────────────

def _gold_pattern():
    """构造典型形态：第 X 日封板 → 回踩 ~10% → 当日阳线企稳"""
    return [
        # (open, high, low, close)
        (10, 10, 10, 10),       # 基线
        (10, 11, 10, 11),       # 真封板 +10%
        (11, 11.2, 10.8, 10.8), # 次日小回
        (10.8, 10.9, 10.3, 10.4), # 继续回
        (10.4, 10.5, 9.9, 10.0),  # 继续回
        (10.0, 10.1, 9.6, 9.8),   # 跌到位
        (9.8, 9.9, 9.5, 9.7),     # 横盘
        (9.7, 9.95, 9.6, 9.9),    # 当日阳线，收回 9.9（距 peak 11 回撤 10%）
    ]


def test_gene_pattern_hits(strat):
    sc = FakeScanner()
    sc._cache["600000"] = _mk_kline(_gold_pattern())
    sig = strat._evaluate_single_stock("600000", sc, {"600000": "X"}, "20260101")
    assert sig is not None
    assert sig.score >= strat.score_threshold
    assert sig.extra["n_real_limits"] == 1
    assert 5.0 <= sig.extra["pullback_pct"] <= 18.0


def test_no_recent_limit_returns_none(strat):
    """无封板基因 → None"""
    sc = FakeScanner()
    flat = [(10, 10.1, 9.9, 10)] * 10
    sc._cache["600000"] = _mk_kline(flat)
    sig = strat._evaluate_single_stock("600000", sc, {"600000": "X"}, "20260101")
    assert sig is None


def test_today_is_limit_up_skipped(strat):
    """当日仍封板 → 不可成交，SkipStock"""
    sc = FakeScanner()
    rows = _gold_pattern()
    # 把最后一根改成今日封板（前日 close=9.7 → 今日 close=10.67 ≈ +10%）
    rows[-1] = (9.7, 10.67, 9.7, 10.67)
    sc._cache["600000"] = _mk_kline(rows)
    with pytest.raises(strat._SkipStock):
        strat._evaluate_single_stock("600000", sc, {"600000": "X"}, "20260101")


def test_too_deep_pullback_rejected(strat):
    """回撤超 18% → 已破位，不是回踩"""
    sc = FakeScanner()
    rows = [
        (10, 10, 10, 10),
        (10, 11, 10, 11),       # 封板
        (11, 11, 10, 10.5),
        (10.5, 10.5, 9, 9),
        (9, 9, 8.5, 8.6),       # 跌穿
        (8.6, 8.7, 8.3, 8.5),
        (8.5, 8.7, 8.4, 8.7),   # 阳线但距 peak 11 已回撤 20%+
    ]
    sc._cache["600000"] = _mk_kline(rows)
    sig = strat._evaluate_single_stock("600000", sc, {"600000": "X"}, "20260101")
    assert sig is None


def test_no_pullback_rejected(strat):
    """封板后没有回撤（持续上涨）→ 不是"回踩+拐头"形态，应拒绝"""
    sc = FakeScanner()
    rows = [
        (10, 10, 10, 10),
        (10, 11, 10, 11),       # 封板
        (11, 11.5, 10.9, 11.4),
        (11.4, 11.8, 11.3, 11.7),
        (11.7, 12.0, 11.6, 11.9),  # 当日，距 peak 12 只回 0.8%
    ]
    sc._cache["600000"] = _mk_kline(rows)
    sig = strat._evaluate_single_stock("600000", sc, {"600000": "X"}, "20260101")
    assert sig is None


def test_negative_today_rejected(strat):
    """当日继续阴跌 → 没有拐头，应拒绝"""
    sc = FakeScanner()
    rows = _gold_pattern()
    # 改最后一根：阴线（close < open）
    rows[-1] = (9.9, 9.95, 9.5, 9.6)
    sc._cache["600000"] = _mk_kline(rows)
    sig = strat._evaluate_single_stock("600000", sc, {"600000": "X"}, "20260101")
    assert sig is None
