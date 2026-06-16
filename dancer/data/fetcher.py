from dancer.data.sources import DataSource
from dancer.models.stock import StockInfo, KLine, StockData
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class DataFetcher:
    """数据获取器"""

    def __init__(self):
        self.source = DataSource()
        self._stock_list_cache = None

    def get_stock_list(self) -> list[dict]:
        """获取股票列表（带缓存）"""
        if self._stock_list_cache is None:
            self._stock_list_cache = self.source.get_stock_list()
        return self._stock_list_cache

    def get_stock_data(self, code: str, days: int = 250) -> StockData | None:
        """获取单只股票数据"""
        klines = self.source.get_kline(code, days)
        if not klines:
            return None

        info = self._find_stock_info(code)
        if not info:
            return None

        return StockData(
            info=StockInfo(**info),
            klines=[KLine(**k) for k in klines],
            updated_at=datetime.now()
        )

    def _find_stock_info(self, code: str) -> dict | None:
        """从股票列表中查找信息"""
        stocks = self.get_stock_list()
        for s in stocks:
            if s.get('code') == code:
                return s
        return None