import pytest

from stock_screener.strategies.base import BaseStrategy
from stock_screener.strategies.registry import (
    STRATEGY_REGISTRY,
    get_strategy,
    list_strategies,
)


def test_registry_non_empty():
    assert len(STRATEGY_REGISTRY) >= 5


def test_every_registered_class_extends_base():
    for key, meta in STRATEGY_REGISTRY.items():
        assert issubclass(meta["cls"], BaseStrategy), f"{key} 不继承 BaseStrategy"


def test_registry_metadata_complete():
    required = {"cls", "name", "description", "tags", "icon", "weight"}
    for key, meta in STRATEGY_REGISTRY.items():
        missing = required - set(meta)
        assert not missing, f"{key} 缺少字段: {missing}"


def test_get_strategy_returns_instance_with_top_n():
    s = get_strategy("macd_bull", top_n=5)
    assert s.top_n == 5
    assert s.name == "macd_bull"


def test_get_strategy_unknown_raises():
    with pytest.raises(ValueError):
        get_strategy("not_a_real_strategy")


def test_list_strategies_keys_match_registry():
    listed = {row["id"] for row in list_strategies()}
    assert listed == set(STRATEGY_REGISTRY.keys())
