"""
baostock 数据源：收盘数据，后复权
"""

import logging
import time
from datetime import datetime, date, timedelta
from typing import Optional

import baostock as bs
import pandas as pd

logger = logging.getLogger(__name__)

# 延迟初始化
_bs_conn = None

# ── 登录熔断 ──────────────────────────────────────────────
# baostock 登录失败（尤其「黑名单用户」）是账号级、会话级的持久失败：
# 每只股票都重试一次 login 会拖垮整轮下载（3000+ 只 × 一次注定失败的登录）。
# 失败后进入冷却期，期间直接跳过 baostock，让上层秒降级到多源（东财/新浪/腾讯）。
_login_failures = 0
_login_cooldown_until = 0.0
_FAIL_THRESHOLD = 3          # 连续失败 N 次进入冷却
_COOLDOWN_TRANSIENT = 300.0  # 普通失败冷却 5 分钟
_COOLDOWN_BLACKLIST = 86400.0  # 黑名单：本会话基本放弃（24h）


def _trip_cooldown(err_msg: str):
    """根据错误类型设置冷却终点，并只在进入冷却时打一条日志（避免刷屏）。"""
    global _login_failures, _login_cooldown_until
    _login_failures += 1
    is_blacklist = "黑名单" in (err_msg or "")
    if is_blacklist:
        _login_cooldown_until = time.monotonic() + _COOLDOWN_BLACKLIST
        logger.error(f"baostock 登录被拉黑（{err_msg}），本会话停用 baostock，全部降级到多源数据")
    elif _login_failures >= _FAIL_THRESHOLD:
        _login_cooldown_until = time.monotonic() + _COOLDOWN_TRANSIENT
        logger.warning(
            f"baostock 连续登录失败 {_login_failures} 次（{err_msg}），"
            f"熔断 {int(_COOLDOWN_TRANSIENT)}s，期间降级到多源数据"
        )


def _ensure_login():
    global _bs_conn, _login_failures
    # 冷却期内直接跳过，不再尝试 login（静默，不刷屏）
    if _login_cooldown_until and time.monotonic() < _login_cooldown_until:
        return False
    if _bs_conn is None:
        # baostock 自身不给 socket 设超时，登录/查询若遇服务端不响应会永久阻塞。
        # 在建连时给一个默认超时上限（登录用的持久 socket 会继承它），防止卡死整轮下载。
        import socket
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(15)
        try:
            _bs_conn = bs.login()
        finally:
            socket.setdefaulttimeout(old_timeout)
        if _bs_conn is None or _bs_conn.error_code != "0":
            err_msg = getattr(_bs_conn, "error_msg", "None")
            _bs_conn = None  # 重置，否则失败的连接对象会被缓存，后续调用误判为已登录
            _trip_cooldown(err_msg)
            return False
        # 登录成功，清零失败计数
        _login_failures = 0
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