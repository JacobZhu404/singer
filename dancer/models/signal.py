from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional


class FactorValue(BaseModel):
    """因子值"""
    name: str
    value: float
    description: Optional[str] = None


class StockSignal(BaseModel):
    """股票信号"""
    code: str = Field(description="股票代码")
    name: str = Field(description="股票名称")
    score: float = Field(ge=0, le=100, description="综合评分 0-100")
    factors: list[FactorValue] = Field(default_factory=list, description="因子列表")
    signals: list[str] = Field(default_factory=list, description="触发信号")
    reason: Optional[str] = Field(default=None, description="推荐理由")


class ScreenResult(BaseModel):
    """筛选结果"""
    stocks: list[StockSignal]
    total: int
    timestamp: str