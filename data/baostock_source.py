"""
baostock 数据源：收盘数据，后复权
"""

import logging
from datetime import datetime, date, timedelta
from typing import Optional

import baostock as bs
import pandas as pd

logger = logging.getLogger(__name__)

# 延迟初始化
_bs_conn = None


def _ensure_login():
    global _bs_conn
    if _bs_conn is None:
        _bs_conn = bs.login()
        if _bs_conn.error_code != "0":
            logger.error(f"baostock 登录失败: {_bs_conn.error_msg}")
            return False
    return True


def _to_bs_code(code: str) -> str:
    """转换为 baostock 格式：6/9开头 -> sh，其余 -> sz"""
    code = str(code).strip()
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    return f"{prefix}.{code}"


def get_kline(code: str, days: int = 120, start_date: Optional[str] = None) -> pd.DataFrame:
    """
    获取后复权日K线

    Args:
        code: 股票代码（6位数字）
        days: 需要的天数
        start_date: 开始日期（可选，默认days天前）

    Returns:
        DataFrame: date, open, high, low, close, volume, amount
    """
    if not _ensure_login():
        return pd.DataFrame()

    code6 = str(code).strip()
    bs_code = _to_bs_code(code6)

    if not start_date:
        end = date.today()
        start = end - timedelta(days=days)
        start_date = start.strftime("%Y-%m-%d")
    end_date = date.today().strftime("%Y-%m-%d")

    try:
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume,amount",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="1",  # 后复权
        )

        if rs.error_code != "0":
            logger.warning(f"baostock 查询失败 {code6}: {rs.error_msg}")
            return pd.DataFrame()

        data = []
        while rs.next():
            data.append(rs.get_row_data())

        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data, columns=rs.fields)

        # 数值转换
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna(subset=["close"])
        df = df[df["volume"] > 0]

        return df

    except Exception as e:
        logger.warning(f"baostock 获取 {code6} 失败: {e}")
        return pd.DataFrame()


def get_realtime(code: str) -> dict:
    """
    baostock 不支持实时行情，返回 None
    使用新浪/腾讯数据源
    """
    return None


def get_stock_list() -> pd.DataFrame:
    """获取全市场A股列表"""
    if not _ensure_login():
        return pd.DataFrame()

    try:
        rs = bs.query_stock_basic()
        symbols = []
        while rs.next():
            row = rs.get_row_data()
            code = row[0]  # "sh.600000"
            status = row[4]  # "1" = 上市
            stock_type = row[5]  # "1" = 股票
            if status == "1" and stock_type == "1":
                symbols.append({"ts_code": code.split(".")[1], "name": row[1]})

        return pd.DataFrame(symbols)
    except Exception as e:
        logger.warning(f"baostock 获取股票列表失败: {e}")
        return pd.DataFrame()


def get_last_trading_date() -> Optional[date]:
    """获取最近一个有交易的日期"""
    if not _ensure_login():
        return None

    try:
        # 获取最近30天有交易的日期
        end = date.today()
        start = end - timedelta(days=30)

        rs = bs.query_trade_dates(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        if rs.error_code != "0":
            return None

        trade_dates = []
        while rs.next():
            row = rs.get_row_data()
            if row[1] == "1":  # is_trading_day
                trade_dates.append(row[0])

        if trade_dates:
            return datetime.strptime(trade_dates[-1], "%Y-%m-%d").date()
        return None
    except Exception as e:
        logger.warning(f"baostock 查询交易日失败: {e}")
        return None


def logout():
    """登出baostock"""
    global _bs_conn
    if _bs_conn:
        bs.logout()
        _bs_conn = None