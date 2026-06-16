from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class StockInfo(BaseModel):
    """股票基本信息"""
    code: str           # 股票代码 (e.g., "000001")
    name: str         # 股票名称
    market: str       # 市场 (主板/创业板/科创板)
    industry: Optional[str] = None  # 所属行业


class KLine(BaseModel):
    """K线数据"""
    code: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: Optional[float] = None


class StockData(BaseModel):
    """完整股票数据"""
    info: StockInfo
    klines: list[KLine]
    updated_at: datetime