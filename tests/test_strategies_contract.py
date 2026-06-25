"""策略契约测试 — 一次性覆盖所有注册策略

每个策略都通过：
1. 实例化不抛
2. `_evaluate_single_stock` 在「合成 K 线 + 完整指标包」上不崩
3. 命中时 StockSignal 各字段在合理范围（score∈[0,100], win_rate∈[0,1], 必填字段）
4. 不命中时返回 None / 抛 _SkipStock 也算正常完成

这套契约捕获最常见的回归：导入失败、指标字段重命名、score 计算 bug
让 score 超出 [0,100]、忘记 set name/strategy 等。

不验证「特定形态是否命中」——那种验证应放到针对性测试里
（如 tests/test_high_tight_flag.py、tests/test_limit_up_gene.py）。
"""

import threading
import numpy as np
import pandas as pd
import pytest

from stock_screener.strategies.base import BaseStrategy, StockSignal
from stock_screener.strategies.registry import STRATEGY_REGISTRY
from stock_screener.utils.indicators import compute_indicator_bundle


def _make_kline(seed: int, n: int = 250) -> pd.DataFrame:
    """生成形态各异的合成 K 线，用 seed 控制多样性以提升命中概率。"""
    rng = np.random.default_rng(seed)
    base = float(rng.uniform(5, 30))
    drift = float(rng.normal(0.0008, 0.0005))
    vol_scale = float(rng.uniform(0.015, 0.03))
    returns = rng.normal(drift, vol_scale, size=n)
    close = base * np.exp(np.cumsum(returns))
    high = close * (1 + rng.uniform(0.001, 0.025, size=n))
    low = close * (1 - rng.uniform(0.001, 0.025, size=n))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.005, size=n))
    vol = rng.uniform(1e6, 1e7, size=n)
    pct = pd.Series(close).pct_change().fillna(0).values * 100
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "vol": vol,
            "volume": vol,
            "pct_chg": pct,
        },
        index=dates,
    )


class FakeScanner:
    """模拟 MarketScanner，提供策略所需的最小接口集。"""

    def __init__(self, kline_by_code):
        self._lock = threading.Lock()
        self._cache = dict(kline_by_code)
        self._ind_cache = {}
        # 横截面策略（reversal）需读 ranked_returns，挂一个空 dict 当占位
        self.reversal_returns = {}
        self._cross_section_cache = {}

    def load(self):
        return True

    def get_history(self, code, days=120, pure=False):
        df = self._cache.get(code)
        if df is None or len(df) < days:
            return df if df is not None else pd.DataFrame()
        return df.tail(days).reset_index(drop=True)

    def get_indicators(self, code, days=120, pure=False):
        key = (code, days, pure)
        if key in self._ind_cache:
            return self._ind_cache[key]
        df = self.get_history(code, days=days, pure=pure)
        if df is None or df.empty:
            result = {}
        else:
            result = compute_indicator_bundle(df)
        self._ind_cache[key] = result
        return result

    def get_realtime(self, code):
        df = self._cache.get(code)
        if df is None or df.empty:
            return {"涨跌幅": 0.0, "最新价": 0.0, "换手率": 0.0}
        last = float(df["close"].iloc[-1])
        prev = float(df["close"].iloc[-2]) if len(df) > 1 else last
        pct = (last / prev - 1) * 100 if prev else 0.0
        return {"涨跌幅": pct, "最新价": last, "换手率": 1.0}


_CODES = [f"60000{i}" for i in range(10)]
_NAME_MAP = {c: f"TST{i}" for i, c in enumerate(_CODES)}


@pytest.fixture(scope="module")
def fake_scanner():
    klines = {c: _make_kline(seed=i + 1) for i, c in enumerate(_CODES)}
    return FakeScanner(klines)


def _check_signal_invariants(sig: StockSignal, strat_name: str):
    assert sig.ts_code, f"[{strat_name}] ts_code empty"
    assert sig.strategy == strat_name, f"[{strat_name}] strategy mismatch: {sig.strategy}"
    assert 0 <= sig.score <= 100, f"[{strat_name}] score out of range: {sig.score}"
    # win_rate: 4 个策略 (macd_bull/strong_stock/chanlun_strict/rps_breakout) 当前留 None，
    # 由 engine 合并阶段兜底 base_win_rate。这里允许 None 以反映现状；如要收紧应统一在
    # _evaluate_single_stock 里赋 self.base_win_rate（待重构）。
    assert sig.win_rate is None or 0 <= sig.win_rate <= 1, (
        f"[{strat_name}] win_rate out of range: {sig.win_rate}"
    )
    assert isinstance(sig.signals, list), f"[{strat_name}] signals not list"


@pytest.mark.parametrize("strat_key", list(STRATEGY_REGISTRY.keys()))
def test_strategy_evaluates_without_exception(strat_key, fake_scanner):
    """每个注册策略在合成数据上单只评估都不应抛非预期异常。"""
    meta = STRATEGY_REGISTRY[strat_key]
    strat = meta["cls"](top_n=5)
    assert strat.name == strat_key, f"strategy name mismatch: {strat.name} != {strat_key}"

    # 横截面策略需要 prepare_for_date 先扫一遍
    try:
        strat.prepare_for_date(fake_scanner, _CODES, "20260101")
    except BaseStrategy._SkipStock:
        pass  # 横截面准备阶段允许跳过

    hits = 0
    for code in _CODES:
        try:
            sig = strat._evaluate_single_stock(code, fake_scanner, _NAME_MAP, "20260101")
        except BaseStrategy._SkipStock:
            continue
        if sig is not None:
            _check_signal_invariants(sig, strat_key)
            hits += 1
    # 不强制每个策略必须命中——某些形态在随机合成数据上罕见


def test_all_strategies_instantiate():
    """注册表里每个 cls 都能 top_n=20 实例化（捕获 __init__ 签名漂移）。"""
    for key, meta in STRATEGY_REGISTRY.items():
        inst = meta["cls"](top_n=20)
        assert inst.name == key, f"{key}: name '{inst.name}' != registry key"
        assert isinstance(inst.description, str)
        assert 0 < inst.base_win_rate < 1
