from __future__ import annotations
from dancer.strategies.base import BaseStrategy
from typing import Dict, Optional


class StrategyRegistry:
    """策略注册表"""

    _strategies: Dict[str, BaseStrategy] = {}

    @classmethod
    def register(cls, strategy: BaseStrategy):
        cls._strategies[strategy.name] = strategy

    @classmethod
    def get(cls, name: str) -> Optional[BaseStrategy]:
        return cls._strategies.get(name)

    @classmethod
    def list_all(cls) -> Dict[str, BaseStrategy]:
        return cls._strategies

    @classmethod
    def load_default(cls):
        """加载默认策略"""
        from dancer.strategies.macd_bull import MACDBullStrategy
        from dancer.strategies.volume_breakout import VolumeBreakoutStrategy
        cls.register(MACDBullStrategy())
        cls.register(VolumeBreakoutStrategy())