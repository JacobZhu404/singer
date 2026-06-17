from stock_screener.backtest.backtest_engine import (
    BacktestResult,
    BacktestTrade,
    ROUND_TRIP_COST_PCT,
    _calc_stats,
)


def test_round_trip_cost_positive():
    assert ROUND_TRIP_COST_PCT > 0


def test_calc_stats_alpha_against_benchmark():
    r = BacktestResult(strategy="t")
    r.trades = [
        BacktestTrade(
            buy_date="20250101", code="000001", name="X", strategy="t",
            buy_price=10.0, score=80.0, signals=[],
            returns={5: 3.0}, exit_prices={5: 10.3}, max_drawdowns={5: 1.0},
        ),
        BacktestTrade(
            buy_date="20250108", code="000002", name="Y", strategy="t",
            buy_price=20.0, score=70.0, signals=[],
            returns={5: 1.0}, exit_prices={5: 20.2}, max_drawdowns={5: 0.5},
        ),
    ]
    benchmark = {5: {"20250101": 1.0, "20250108": 0.0}, 2: {}, 10: {}, 30: {}}
    _calc_stats(r, benchmark)

    st = r.period_stats[5]
    assert st.total == 2
    assert st.wins == 2
    assert abs(st.avg_return - 2.0) < 1e-9
    assert abs(st.benchmark_return - 0.5) < 1e-9
    assert abs(st.alpha - 1.5) < 1e-9


def test_calc_stats_no_benchmark_alignment_zero_alpha():
    r = BacktestResult(strategy="t")
    r.trades = [
        BacktestTrade(
            buy_date="20250101", code="000001", name="X", strategy="t",
            buy_price=10.0, score=80.0, signals=[],
            returns={5: 3.0}, exit_prices={5: 10.3}, max_drawdowns={5: 1.0},
        ),
    ]
    _calc_stats(r, {5: {}, 2: {}, 10: {}, 30: {}})
    st = r.period_stats[5]
    assert st.benchmark_return == 0.0
    assert st.alpha == st.avg_return
