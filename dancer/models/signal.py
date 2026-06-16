from pydantic import BaseModel
from typing import Optional


class FactorValue(BaseModel):
    """因子值"""
    name: str
    value: float
    description: Optional[str] = None


class StockSignal(BaseModel):
    """股票信号"""
    code: str
    name: str
    score: float           # 综合评分 0-100
    factors: list[FactorValue] = []
    signals: list[str] = []  # 触发信号 ["MACD金叉", "RSI超卖"]
    reason: Optional[str] = None


class ScreenResult(BaseModel):
    """筛选结果"""
    stocks: list[StockSignal]
    total: int
    timestamp: str