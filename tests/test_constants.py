from stock_screener.core import constants as C


def test_concurrency_values_sane():
    assert C.MAX_WORKERS_STRATEGY > 0
    assert C.MAX_WORKERS_PREFETCH > 0
    assert C.MAX_WORKERS_PREFETCH >= C.MAX_WORKERS_STRATEGY


def test_kline_days_sane():
    assert C.DEFAULT_KLINE_DAYS > 0
    assert C.PREFETCH_KLINE_DAYS >= C.DEFAULT_KLINE_DAYS


def test_score_thresholds_in_range():
    assert 0 <= C.MIN_SINGLE_SCORE <= 100
    assert 0 <= C.MIN_WEIGHTED_SCORE <= 100
