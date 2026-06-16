from __future__ import annotations
from pydantic import BaseModel, Field
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
    date: datetime = Field(description="交易日期")
    open: float = Field(description="开盘价")
    high: float = Field(description="最高价")
    low: float = Field(description="最低价")
    close: float = Field(description="收盘价")
    volume: float = Field(description="成交量")
    amount: Optional[float] = Field(default=None, description="成交额")


class StockData(BaseModel):
    """完整股票数据"""
    info: StockInfo
    klines: list[KLine]
    updated_at: datetime