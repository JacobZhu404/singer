import numpy as np
import pandas as pd

from stock_screener.utils.indicators import calc_macd, calc_rsi, ema, sma


def test_ema_matches_pandas_ewm():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    expected = s.ewm(span=3, adjust=False).mean()
    pd.testing.assert_series_equal(ema(s, 3), expected)


def test_sma_basic():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    out = sma(s, 3)
    assert np.isnan(out.iloc[0])
    assert out.iloc[2] == 2.0
    assert out.iloc[4] == 4.0


def test_macd_shapes_and_relations(sample_kline):
    dif, dea, bar = calc_macd(sample_kline["close"])
    assert len(dif) == len(sample_kline)
    np.testing.assert_allclose(bar.values, ((dif - dea) * 2).values, atol=1e-12)


def test_rsi_in_range(sample_kline):
    rsi = calc_rsi(sample_kline["close"])
    valid = rsi.dropna()
    assert valid.min() >= 0
    assert valid.max() <= 100
