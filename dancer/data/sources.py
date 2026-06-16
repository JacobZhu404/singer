import akshare as ak
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class AKShareSource:
    """AKShare数据源"""

    @staticmethod
    def get_stock_list() -> list[dict]:
        """获取股票列表"""
        try:
            df = ak.stock_info_a_code_name()
            return df.to_dict('records')
        except Exception as e:
            logger.warning(f"获取股票列表失败: {e}")
            return []

    @staticmethod
    def get_kline_daily(code: str, days: int = 250) -> Optional[list[dict]]:
        """获取日K线"""
        try:
            df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq", days=days)
            return df.to_dict('records')
        except Exception as e:
            logger.warning(f"获取{code}失败: {e}")
            return None


class DataSource:
    """数据源管理"""

    def __init__(self):
        self.sources = [AKShareSource()]

    def get_stock_list(self) -> list[dict]:
        """获取股票列表"""
        for source in self.sources:
            try:
                result = source.get_stock_list()
                if result:
                    return result
            except Exception as e:
                logger.warning(f"数据源失败: {e}")
        return []

    def get_kline(self, code: str, days: int = 250) -> Optional[list[dict]]:
        """获取K线"""
        for source in self.sources:
            try:
                result = source.get_kline_daily(code, days)
                if result:
                    return result
            except Exception as e:
                logger.warning(f"获取{code}失败: {e}")
        return None