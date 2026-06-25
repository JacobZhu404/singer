"""factor_portfolio_backtest 纯函数单测：分位选股 / 涨停剔除 / 等权收益 / 年化。"""

import math
import numpy as np
import pandas as pd

from stock_screener.tools.factor_portfolio_backtest import (
    select_top_quantile,
    tradable_codes,
    equal_weight_return,
    annualize,
    ROUND_TRIP,
)


def test_select_top_quantile_picks_highest():
    row = pd.Series({"a": 0.1, "b": 0.5, "c": -0.2, "d": 0.9, "e": np.nan})
    sel = select_top_quantile(row, q=0.5)  # 4 有效 → ceil(4*0.5)=2
    assert list(sel) == ["d", "b"]


def test_select_top_quantile_drops_nan_and_inf():
    row = pd.Series({"a": np.inf, "b": np.nan, "c": 0.3})
    sel = select_top_quantile(row, q=1.0)
    assert list(sel) == ["c"]


def test_select_top_quantile_empty():
    row = pd.Series({"a": np.nan})
    assert len(select_top_quantile(row, q=0.5)) == 0


def test_tradable_excludes_limit_up_open():
    codes = ["600000", "600001"]
    prev_close = pd.Series({"600000": 10.0, "600001": 10.0})
    # 600000 开盘 +9.8%（接近主板 10% 涨停 → 买不进）；600001 +3%（可买）
    buy_open = pd.Series({"600000": 10.98, "600001": 10.30})
    limits = {"600000": 10.0, "600001": 10.0}
    sel = tradable_codes(codes, prev_close, buy_open, limits)
    assert list(sel) == ["600001"]


def test_tradable_chinext_20pct_threshold():
    codes = ["300001"]
    prev_close = pd.Series({"300001": 10.0})
    buy_open = pd.Series({"300001": 11.5})  # +15%，创业板 20% 涨停内 → 可买
    limits = {"300001": 20.0}
    assert list(tradable_codes(codes, prev_close, buy_open, limits)) == ["300001"]


def test_tradable_drops_missing_price():
    codes = ["600000"]
    prev_close = pd.Series({"600000": np.nan})
    buy_open = pd.Series({"600000": 10.0})
    assert len(tradable_codes(codes, prev_close, buy_open, {"600000": 10.0})) == 0


def test_equal_weight_return():
    buy = pd.Series({"a": 10.0, "b": 20.0})
    sell = pd.Series({"a": 11.0, "b": 21.0})  # +10%, +5%
    r = equal_weight_return(buy, sell, ["a", "b"])
    assert abs(r - 0.075) < 1e-9


def test_equal_weight_return_empty_is_nan():
    assert math.isnan(equal_weight_return(pd.Series(dtype=float), pd.Series(dtype=float), []))


def test_annualize_basic():
    # 10 期，每期 +1%，持有 5 日
    s = annualize([0.01] * 10, hold_days=5)
    assert s["periods"] == 10
    assert abs(s["mean_per"] - 0.01) < 1e-12
    assert s["win_rate"] == 1.0
    assert s["sharpe"] == 0.0  # std=0
    assert abs(s["cum"] - (1.01 ** 10 - 1)) < 1e-9


def test_annualize_empty():
    assert annualize([], hold_days=5)["periods"] == 0


def test_round_trip_matches_engine_cost():
    assert abs(ROUND_TRIP - ((0.0010 + 0.0003) * 2 + 0.0005)) < 1e-12
