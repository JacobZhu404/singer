"""high_tight_flag 入场条件修复测试

旧 bug：
1. `near_high` 分支接受 "5% 内任一票" → 选到一堆"做完旗杆开始跌"的股票
2. `high >= flag_high` 用盘中最高（插针即触发） → T+1 实际买不到
3. 不校验 flag_high 是否在 pole_top 之上 → 旗面下滑也认旗形

修复：只接 close-based 突破 + flag_high >= pole_top * 0.98
"""

import threading

import numpy as np
import pandas as pd
import pytest

from stock_screener.strategies.high_tight_flag import HighTightFlagStrategy


def _mk_kline(rows):
    """rows = [(open, high, low, close, vol), ...]"""
    arr = np.array(rows, dtype=float)
    return pd.DataFrame({
        "open":  arr[:, 0],
        "high":  arr[:, 1],
        "low":   arr[:, 2],
        "close": arr[:, 3],
        "vol":   arr[:, 4],
    })


class FakeScanner:
    def __init__(self):
        self._lock = threading.Lock()
        self._cache = {}
        self._vr_today = 2.0

    def load(self):
        return True

    def get_indicators(self, code, days=120, pure=False):
        df = self._cache.get(code)
        if df is None:
            return {}
        vr = pd.Series(np.full(len(df), 1.0), index=df.index)
        vr.iloc[-1] = self._vr_today  # 当日量比可控
        return {"kline": df, "vol_ratio": vr}

    def get_realtime(self, code):
        df = self._cache.get(code)
        last = float(df["close"].iloc[-1]) if df is not None and len(df) else 0.0
        return {"涨跌幅": 0.0, "最新价": last, "换手率": 0.0}


@pytest.fixture
def strat():
    return HighTightFlagStrategy(top_n=10)


def _pole_flag(flag_pattern):
    """构造典型的 30 日旗杆 (10 → 18, +80%) + N 日旗面 flag_pattern。

    flag_pattern: 旗面那几天的 (open, high, low, close, vol) 列表
    返回完整 kline (35+ 行)。
    """
    base = 8.0
    # 旗杆 25 天，从 8 涨到 17.5（+118%，远超 80% 门槛）。
    # 旗杆顶 < 旗面 17.5-18.05，满足 flag_high >= pole_top * 0.98。
    pole = []
    for k in range(25):
        p = base + (17.5 - base) * k / 24
        pole.append((p * 0.99, p * 1.02, p * 0.98, p, 3e6))
    return _mk_kline(pole + list(flag_pattern))


# ── 真旗形 + close 突破：通过 ────────────────────────────────────

def test_close_breakout_with_volume_passes(strat):
    """旗杆 +80% → 旗面在 17.5-18 之间整理 → 今日 close 突破 18 + 放量"""
    flag = [
        (17.8, 18.0, 17.5, 17.7, 1.5e6),
        (17.7, 17.9, 17.5, 17.8, 1.5e6),
        (17.8, 18.0, 17.6, 17.9, 1.4e6),
        (17.9, 18.0, 17.7, 17.95, 1.6e6),
        (17.95, 18.0, 17.8, 17.9, 1.4e6),
        (17.9, 18.0, 17.7, 17.85, 1.5e6),
        (17.85, 18.05, 17.75, 17.95, 1.5e6),
        (17.95, 18.05, 17.8, 17.9, 1.5e6),
        (17.9, 18.0, 17.8, 17.85, 1.5e6),
        # 今日：close 18.5 > flag_high 18.05，放量 (vr=2.0)
        (17.85, 18.6, 17.85, 18.5, 5e6),
    ]
    sc = FakeScanner()
    sc._cache["600000"] = _pole_flag(flag)
    sig = strat._evaluate_single_stock("600000", sc, {"600000": "X"}, "20260101")
    assert sig is not None, "真旗形 + close 突破应该通过"
    assert sig.extra["pole_gain"] >= 70


# ── 旧 bug 1: "贴近高点"不再认 ──────────────────────────────────

def test_near_high_without_breakout_rejected(strat):
    """贴近旗面高点但当日 close 没破前高 → 旧版会过，新版必拒"""
    flag = [
        (17.8, 18.0, 17.5, 17.7, 1.5e6),
        (17.7, 17.9, 17.5, 17.8, 1.5e6),
        (17.8, 18.0, 17.6, 17.9, 1.4e6),
        (17.9, 18.0, 17.7, 17.95, 1.6e6),
        (17.95, 18.0, 17.8, 17.9, 1.4e6),
        (17.9, 18.0, 17.7, 17.85, 1.5e6),
        (17.85, 18.05, 17.75, 17.95, 1.5e6),
        (17.95, 18.05, 17.8, 17.9, 1.5e6),
        (17.9, 18.0, 17.8, 17.85, 1.5e6),
        # 今日 close 17.9 < flag_high 18.05，只是"贴近"
        (17.85, 18.0, 17.8, 17.9, 1.5e6),
    ]
    sc = FakeScanner()
    sc._cache["600000"] = _pole_flag(flag)
    sig = strat._evaluate_single_stock("600000", sc, {"600000": "X"}, "20260101")
    assert sig is None, "无突破不应入场"


# ── 旧 bug 2: 盘中插针不算 ──────────────────────────────────────

def test_intraday_wick_above_high_rejected(strat):
    """high 盘中破前高但收盘没站上 → 旧版会过 (用 high)，新版必拒 (用 close)"""
    flag = [
        (17.8, 18.0, 17.5, 17.7, 1.5e6),
        (17.7, 17.9, 17.5, 17.8, 1.5e6),
        (17.8, 18.0, 17.6, 17.9, 1.4e6),
        (17.9, 18.0, 17.7, 17.95, 1.6e6),
        (17.95, 18.0, 17.8, 17.9, 1.4e6),
        (17.9, 18.0, 17.7, 17.85, 1.5e6),
        (17.85, 18.05, 17.75, 17.95, 1.5e6),
        (17.95, 18.05, 17.8, 17.9, 1.5e6),
        (17.9, 18.0, 17.8, 17.85, 1.5e6),
        # 今日：盘中冲到 18.5（破前高 18.05）但 close 回落到 17.8
        (17.85, 18.5, 17.8, 17.8, 5e6),
    ]
    sc = FakeScanner()
    sc._cache["600000"] = _pole_flag(flag)
    sig = strat._evaluate_single_stock("600000", sc, {"600000": "X"}, "20260101")
    assert sig is None, "插针不算突破"


# ── 旧 bug 3: 旗面下滑（已破位）不再认 ──────────────────────────

def test_flag_drifting_down_rejected(strat):
    """旗杆 +80% 后旗面整体下滑（flag_high 远低于 pole_top）→ 新版应拒"""
    # 旗杆顶 ≈ 18.0，但旗面整体在 16 附近（已下滑 ~11%）
    flag = [
        (17.0, 17.0, 16.5, 16.8, 1.5e6),
        (16.8, 17.0, 16.4, 16.5, 1.5e6),
        (16.5, 16.8, 16.2, 16.4, 1.4e6),
        (16.4, 16.6, 16.0, 16.2, 1.6e6),
        (16.2, 16.5, 16.0, 16.3, 1.4e6),
        (16.3, 16.5, 16.1, 16.2, 1.5e6),
        (16.2, 16.4, 16.0, 16.1, 1.5e6),
        (16.1, 16.4, 16.0, 16.3, 1.5e6),
        # 今日 close 16.5 > flag_high 16.5 触发 close-breakout，
        # 但 flag_high << pole_top * 0.98 (18*0.98=17.64) → 应拒
        (16.3, 16.6, 16.2, 16.5, 5e6),
    ]
    sc = FakeScanner()
    sc._cache["600000"] = _pole_flag(flag)
    sig = strat._evaluate_single_stock("600000", sc, {"600000": "X"}, "20260101")
    assert sig is None, "旗面已下滑则不是高位旗形"


# ── 量能不足：不算突破 ──────────────────────────────────────────

def test_close_breakout_without_volume_rejected(strat):
    """close 突破但量比不到 1.5 → 不算放量突破"""
    flag = [
        (17.8, 18.0, 17.5, 17.7, 1.5e6),
        (17.7, 17.9, 17.5, 17.8, 1.5e6),
        (17.8, 18.0, 17.6, 17.9, 1.4e6),
        (17.9, 18.0, 17.7, 17.95, 1.6e6),
        (17.95, 18.0, 17.8, 17.9, 1.4e6),
        (17.9, 18.0, 17.7, 17.85, 1.5e6),
        (17.85, 18.05, 17.75, 17.95, 1.5e6),
        (17.95, 18.05, 17.8, 17.9, 1.5e6),
        (17.9, 18.0, 17.8, 17.85, 1.5e6),
        (17.85, 18.6, 17.85, 18.5, 1.5e6),  # close 破前高但量没放
    ]
    sc = FakeScanner()
    sc._cache["600000"] = _pole_flag(flag)
    sc._vr_today = 1.2  # 量比不够
    sig = strat._evaluate_single_stock("600000", sc, {"600000": "X"}, "20260101")
    assert sig is None, "无放量不应入场"


# ── 旗杆涨幅不够：不算旗形 ──────────────────────────────────────

def test_weak_pole_gain_rejected(strat):
    """旗杆只涨 50% (< 80%) → 不是高紧旗形"""
    # 自构造一个 25 天 +50% 的弱旗杆
    base = 10.0
    pole = []
    for k in range(25):
        p = base + (15.0 - base) * (k + 1) / 25  # 10 → 15，+50%
        pole.append((p * 0.99, p * 1.02, p * 0.98, p, 3e6))
    flag = [
        (14.8, 15.0, 14.5, 14.7, 1.5e6),
    ] * 8 + [(14.7, 15.5, 14.7, 15.3, 5e6)]
    sc = FakeScanner()
    sc._cache["600000"] = _mk_kline(pole + flag)
    sig = strat._evaluate_single_stock("600000", sc, {"600000": "X"}, "20260101")
    assert sig is None, "旗杆不够暴涨不算旗形"
