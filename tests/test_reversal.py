"""横截面反转因子 + 反转策略测试"""

import threading

import numpy as np
import pandas as pd
import pytest

from stock_screener.utils.cross_section import compute_reversal_scores
from stock_screener.strategies.reversal import ReversalStrategy


def _make_kline(closes, opens=None, n_pad=25) -> pd.DataFrame:
    """构造一段 K 线：前面用首个收盘价补足 n_pad 根，末尾接入 closes。"""
    closes = list(closes)
    full_close = [closes[0]] * n_pad + closes
    if opens is None:
        full_open = [c for c in full_close]
    else:
        full_open = [full_close[0]] * (len(full_close) - len(opens)) + list(opens)
    arr = np.array(full_close, dtype=float)
    df = pd.DataFrame({
        "open": np.array(full_open, dtype=float),
        "high": arr * 1.01,
        "low": arr * 0.99,
        "close": arr,
        "vol": np.full(len(arr), 5e6),
    })
    return df


class FakeScanner:
    def __init__(self):
        self._lock = threading.Lock()
        self._kline_cache = {}
        self._reversal_scores = {}

    def load(self):
        return True

    def get_indicators(self, code, days=60, pure=False):
        df = self._kline_cache.get(code)
        if df is None:
            return {}
        vr = pd.Series(np.full(len(df), 1.3), index=df.index)
        return {"kline": df, "vol_ratio": vr}

    def get_realtime(self, code):
        return {"涨跌幅": None, "最新价": None, "换手率": 0.0}


# ── 横截面排名 ────────────────────────────────────────────────

def test_compute_reversal_ranks_losers_highest():
    sc = FakeScanner()
    # 三只：大跌 / 持平 / 大涨
    sc._kline_cache["000001"] = _make_kline([11, 10.5, 10.2, 9.8, 9.6, 10.0])  # ret5<0
    sc._kline_cache["000002"] = _make_kline([10, 10, 10, 10, 10, 10])           # ret5≈0
    sc._kline_cache["000003"] = _make_kline([10, 10.5, 11, 11.5, 12, 13])       # ret5>0
    out = compute_reversal_scores(sc, ["000001", "000002", "000003"], lookback=5)

    assert set(out) == {"000001", "000002", "000003"}
    # 跌得最多 → 反转分最高
    assert out["000001"]["score"] > out["000002"]["score"] > out["000003"]["score"]
    assert out["000001"]["ret"] < 0 < out["000003"]["ret"]
    # 结果挂到 scanner 上
    assert sc._reversal_scores is out


def test_compute_reversal_skips_short_history():
    sc = FakeScanner()
    sc._kline_cache["000001"] = _make_kline([11, 10, 9.5], n_pad=2)  # 太短 (<min_history)
    out = compute_reversal_scores(sc, ["000001"], lookback=5, min_history=20)
    assert out == {}


# ── 策略护栏（直接驱动 _evaluate_single_stock）────────────────

@pytest.fixture
def strat():
    return ReversalStrategy(top_n=10)


def _run(strat, sc, code, ret, rev_score, name="测试股"):
    sc._reversal_scores = {code: {"ret": ret, "score": rev_score, "rank_pct": 0.05}}
    name_map = {code: name}
    return strat._evaluate_single_stock(code, sc, name_map, "2026-06-23")


def test_oversold_rebound_passes(strat):
    sc = FakeScanner()
    # 近期下跌后当日回升：close[-2]=9.6 → close[-1]=10.0
    sc._kline_cache["000001"] = _make_kline([11, 10.5, 10.2, 9.8, 9.6, 10.0])
    sig = _run(strat, sc, "000001", ret=-9.1, rev_score=90.0)
    assert sig is not None
    assert sig.strategy == "reversal"
    assert sig.score > 0
    assert sig.extra["reversal_score"] == 90.0


def test_gainer_rejected(strat):
    sc = FakeScanner()
    sc._kline_cache["000002"] = _make_kline([10, 10.5, 11, 11.5, 12, 13])
    assert _run(strat, sc, "000002", ret=30.0, rev_score=90.0) is None


def test_below_threshold_rejected(strat):
    sc = FakeScanner()
    sc._kline_cache["000001"] = _make_kline([11, 10.5, 10.2, 9.8, 9.6, 10.0])
    assert _run(strat, sc, "000001", ret=-9.1, rev_score=50.0) is None


def test_limit_down_skipped(strat):
    sc = FakeScanner()
    # 当日跌停：close[-2]=10.0 → close[-1]=9.0（主板 -10%）
    sc._kline_cache["000001"] = _make_kline([11, 10.6, 10.3, 10.1, 10.0, 9.0])
    with pytest.raises(ReversalStrategy._SkipStock):
        _run(strat, sc, "000001", ret=-18.0, rev_score=95.0)


def test_falling_knife_rejected(strat):
    sc = FakeScanner()
    # 当日继续下杀且收阴：close[-2]=9.7 → close[-1]=9.5，open[-1]=9.8
    sc._kline_cache["000001"] = _make_kline(
        [11, 10.5, 10.1, 9.9, 9.7, 9.5], opens=[9.8]
    )
    assert _run(strat, sc, "000001", ret=-13.6, rev_score=90.0) is None


def test_deep_crash_rejected(strat):
    sc = FakeScanner()
    sc._kline_cache["000001"] = _make_kline([20, 18, 16, 15, 14.5, 15.0])
    # ret5 < -max_drawdown(25%) → 排除接飞刀
    assert _run(strat, sc, "000001", ret=-30.0, rev_score=95.0) is None


def test_st_not_in_name_map_skipped(strat):
    sc = FakeScanner()
    sc._kline_cache["000001"] = _make_kline([11, 10.5, 10.2, 9.8, 9.6, 10.0])
    sc._reversal_scores = {"000001": {"ret": -9.1, "score": 90.0, "rank_pct": 0.05}}
    with pytest.raises(ReversalStrategy._SkipStock):
        strat._evaluate_single_stock("000001", sc, {}, "2026-06-23")


def test_missing_score_skipped(strat):
    sc = FakeScanner()
    sc._kline_cache["000001"] = _make_kline([11, 10.5, 10.2, 9.8, 9.6, 10.0])
    sc._reversal_scores = {}
    with pytest.raises(ReversalStrategy._SkipStock):
        strat._evaluate_single_stock("000001", sc, {"000001": "测试股"}, "2026-06-23")
