import pandas as pd

from stock_screener.backtest.backtest_engine import (
    BacktestResult,
    BacktestTrade,
    COMMISSION_PCT,
    ROUND_TRIP_COST_PCT,
    SLIPPAGE_PCT,
    TRANSFER_PCT,
    STAMP_DUTY_PCT,
    _calc_future_returns,
    _calc_stats,
    _is_limit_down_close,
    _is_limit_up_open,
)


def test_round_trip_cost_positive():
    assert ROUND_TRIP_COST_PCT > 0


def test_round_trip_cost_includes_stamp_duty():
    """印花税仅卖出单边收，必须计入双边成本"""
    expected = ((SLIPPAGE_PCT + COMMISSION_PCT + TRANSFER_PCT) * 2 + STAMP_DUTY_PCT) * 100
    assert abs(ROUND_TRIP_COST_PCT - expected) < 1e-9
    # 比单纯滑点+佣金+过户费双倍大，差值正好是 stamp duty
    without_stamp = (SLIPPAGE_PCT + COMMISSION_PCT + TRANSFER_PCT) * 2 * 100
    assert abs((ROUND_TRIP_COST_PCT - without_stamp) - STAMP_DUTY_PCT * 100) < 1e-9


# ── 卖出侧跌停封板：退出顺延 ─────────────────────────────

def _mk_df(closes):
    return pd.DataFrame({"close": closes, "open": closes, "high": closes, "low": closes})

def test_limit_down_exit_rolls_to_next_bar():
    """退出日封死跌停，应顺延到下一根能成交的 close"""
    # entry=10 @idx=0；HOLD_PERIODS[0]=2 → 退出 idx=2 是 -10%（封板），idx=3 才解封
    # 构造：[10, 10, 9.0, 9.3]，其中 9.0 相对前收 10 跌 10% 触主板封板
    df = _mk_df([10.0, 10.0, 9.0, 9.3])
    rets, exits, _ = _calc_future_returns(df, entry_idx=0, code="600000", name="X")
    # period=2 的原退出 idx=2 是跌停 → 顺延到 idx=3，exit=9.3
    assert 2 in exits
    assert abs(exits[2] - 9.3) < 1e-9


def test_limit_down_detection_main_board():
    df = _mk_df([10.0, 9.0])  # -10%
    assert _is_limit_down_close(df, 1, limit_pct=10.0) is True
    assert _is_limit_down_close(df, 1, limit_pct=20.0) is False  # 创业板 -10% 不算封板


def test_exit_dropped_when_entirely_stuck_in_limit_down():
    """连续多日一字跌停，HOLD 期内根本卖不出 → 该 period 应不计入 returns"""
    # entry=10；之后全部 -10%（连环一字板），max_roll=5 后仍封死 → period 应丢弃
    closes = [10.0] + [10.0 * (0.9 ** i) for i in range(1, 32)]
    df = _mk_df(closes)
    rets, exits, _ = _calc_future_returns(df, entry_idx=0, code="600000", name="X")
    # 短持有期（2/5）会因为连续封板而无可成交退出价
    assert 2 not in rets
    assert 5 not in rets


# ── T+1 开盘价入场 ─────────────────────────────────────────

def _mk_ohlc(rows):
    """rows = [(open, close), ...]，high/low 由 open/close 派生。"""
    o = [r[0] for r in rows]
    c = [r[1] for r in rows]
    return pd.DataFrame({
        "open":  o,
        "close": c,
        "high":  [max(a, b) for a, b in zip(o, c)],
        "low":   [min(a, b) for a, b in zip(o, c)],
    })


def test_entry_uses_next_day_open():
    """入场价应是 T+1 开盘价 open[signal+1]，不是信号日 close[signal]"""
    # T(idx0) close=10；T+1(idx1) open=10.5（入场价）；T+2(idx2) close=12
    df = _mk_ohlc([(9.9, 10.0), (10.5, 11.0), (11.0, 12.0)])
    rets, exits, _ = _calc_future_returns(df, entry_idx=0, code="600000", name="X")
    # period=2 → 持有至 close[idx2]=12，入场 open[idx1]=10.5
    assert 2 in rets
    gross = (12.0 - 10.5) / 10.5 * 100
    assert abs(rets[2] - (gross - ROUND_TRIP_COST_PCT)) < 1e-6
    assert abs(exits[2] - 12.0) < 1e-9


def test_no_next_day_returns_empty():
    """信号日是最后一根 K 线 → 无 T+1 开盘，不产生任何收益"""
    df = _mk_ohlc([(9.9, 10.0)])
    rets, exits, dds = _calc_future_returns(df, entry_idx=0, code="600000", name="X")
    assert rets == {} and exits == {} and dds == {}


def test_limit_up_open_blocks_entry():
    """T+1 一字涨停开盘（+10%）→ 买不进，过滤"""
    df = _mk_ohlc([(9.9, 10.0), (11.0, 11.0)])  # T close=10, T+1 open=11
    assert _is_limit_up_open(df, 0, "600000", "浦发银行") is True


def test_normal_open_allows_entry():
    df = _mk_ohlc([(9.9, 10.0), (10.3, 10.5)])  # T+1 open +3%
    assert _is_limit_up_open(df, 0, "600000", "浦发银行") is False


def test_limit_up_open_chinext_20pct():
    """创业板 ±20%：T+1 开盘 +10% 不算封板，+20% 才算"""
    df_10 = _mk_ohlc([(9.9, 10.0), (11.0, 11.5)])  # T+1 open +10%
    df_20 = _mk_ohlc([(9.9, 10.0), (12.0, 12.0)])  # T+1 open +20%
    assert _is_limit_up_open(df_10, 0, "300001", "特锐德") is False
    assert _is_limit_up_open(df_20, 0, "300001", "特锐德") is True


def test_limit_up_open_st_5pct():
    """ST ±5%：T+1 开盘 +5% 即封板"""
    df = _mk_ohlc([(9.9, 10.0), (10.5, 10.6)])  # T+1 open +5%
    assert _is_limit_up_open(df, 0, "600000", "*ST 浦发") is True


def test_no_next_day_blocks_entry():
    """信号日是最后一根 → 无法在 T+1 成交"""
    df = _mk_ohlc([(9.9, 10.0)])
    assert _is_limit_up_open(df, 0, "600000", "X") is True


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
