from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional
import pandas as pd
from dancer.models.signal import StockSignal, FactorValue


class BaseStrategy(ABC):
    """策略基类"""

    name: str = "base"
    description: str = "基础策略"
    weight: float = 1.0  # 权重

    @abstractmethod
    def evaluate(self, code: str, df: pd.DataFrame) -> Optional[StockSignal]:
        """评估单只股票"""
        pass

    def calculate_factors(self, df: pd.DataFrame) -> list[FactorValue]:
        """计算因子（子类可重写）"""
        from dancer.factors.talib import FactorCalculator
        calc = FactorCalculator()
        factors_data = calc.calculate_all(df)
        return [FactorValue(name=k, value=v) for k, v in factors_data.items() if v is not None]