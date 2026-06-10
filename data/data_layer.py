"""
数据层核心逻辑：6层设计

1. 判断数据是否需要更新
2. 需要更新实时还是收盘数据
3. 实时→新浪/腾讯，收盘→baostock
4. baostock写入本地，合并到历史数据
5. 交易时间→实时数据，非交易时间→收盘数据
"""

import logging
import threading
from datetime import datetime, date, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# 延迟导入
_realtime_source = None
_baostock_source = None


def _get_realtime_source():
    global _realtime_source
    if _realtime_source is None:
        from .data_sources import data_manager
        _realtime_source = data_manager
    return _realtime_source


def _get_baostock_source():
    global _baostock_source
    if _baostock_source is None:
        from . import baostock_source
        _baostock_source = baostock_source
    return _baostock_source


# ─────────────────────────────────────────────────────────
# 交易时间判断
# ─────────────────────────────────────────────────────────

def is_market_open() -> bool:
    """判断当前是否在A股交易时间"""
    now = datetime.now()
    weekday = now.weekday()

    # 周六日休市
    if weekday >= 5:
        return False

    current_time = now.time()
    morning_start = datetime.strptime("09:30", "%H:%M").time()
    morning_end = datetime.strptime("11:30", "%H:%M").time()
    afternoon_start = datetime.strptime("13:00", "%H:%M").time()
    afternoon_end = datetime.strptime("15:00", "%H:%M").time()

    if morning_start <= current_time <= morning_end:
        return True
    if afternoon_start <= current_time <= afternoon_end:
        return True
    return False


def is_market_closed_today() -> bool:
    """今天是否已收盘（15:00后）"""
    now = datetime.now()
    return now.time() >= datetime.strptime("15:00", "%H:%M").time()


def get_today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def get_last_trading_date() -> Optional[date]:
    """获取最近一个有交易的日期"""
    now = datetime.now()
    today = now.date()

    # 如果现在在交易时间，返回今天
    if is_market_open():
        return today

    # 否则找上一个交易日
    for days_ago in range(1, 8):
        check_date = today - timedelta(days=days_ago)
        if check_date.weekday() < 5:  # 周一到周五
            return check_date
    return None


# ─────────────────────────────────────────────────────────
# 数据更新判断
# ─────────────────────────────────────────────────────────

class DataUpdateDecision:
    """数据更新决策"""

    NO_UPDATE = "no_update"      # 不需要更新
    REALTIME = "realtime"        # 需要实时数据
    CLOSE = "close"             # 需要收盘数据

    def __init__(self, reason: str, update_type: str):
        self.reason = reason
        self.update_type = update_type


def check_update_need(code: str, local_last_date: str, meta_last_update: str) -> DataUpdateDecision:
    """
    核心判断：是否需要更新，更新什么

    逻辑：
    1. 交易时间 → 必须实时
    2. 非交易时间 → 看是否收盘
       - 15:00后更新过 → 收盘数据，有效
       - 15:00前更新 → 需要更新获取收盘价

    Args:
        code: 股票代码
        local_last_date: 本地缓存的最新日期
        meta_last_update: meta中的更新时间

    Returns:
        DataUpdateDecision: 是否需要更新及类型
    """
    in_market = is_market_open()
    today_str = get_today_str()
    market_close_time = datetime.strptime("15:00", "%H:%M").time()

    # 无本地数据，需要获取
    if not local_last_date:
        if in_market:
            return DataUpdateDecision("无数据，交易时间", DataUpdateDecision.REALTIME)
        return DataUpdateDecision("无数据，非交易时间", DataUpdateDecision.CLOSE)

    # 有本地数据
    local_date = str(local_last_date).split()[0] if local_last_date else ""

    # 场景1：交易时间
    if in_market:
        if local_date != today_str:
            return DataUpdateDecision(f"交易时间，本地是{local_date}，需要今天", DataUpdateDecision.REALTIME)
        return DataUpdateDecision("交易时间已是今天", DataUpdateDecision.NO_UPDATE)

    # 场景2：非交易时间
    # 检查是否是收盘价（15:00后更新）
    if meta_last_update:
        try:
            dt = datetime.strptime(meta_last_update, "%Y-%m-%d %H:%M:%S")
            if dt.time() >= market_close_time:
                # 15:00后更新的，是收盘价
                return DataUpdateDecision(f"非交易时间，已在15:00后更新", DataUpdateDecision.NO_UPDATE)
        except:
            pass

    # 不是收盘价，需要更新获取收盘价
    return DataUpdateDecision(f"非交易时间，上次{local_date}，需要收盘价", DataUpdateDecision.CLOSE)


# ─────────────────────────────────────────────────────────
# 数据获取入口
# ─────────────────────────────────────────────────────────

class DataFetcher:
    """
    统一数据获取入口

    根据更新决策自动选择数据源：
    - REALTIME → 新浪/腾讯
    - CLOSE → baostock
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._realtime = _get_realtime_source()
        self._baostock = _get_baostock_source()

    def get_kline(self, code: str, days: int = 60) -> pd.DataFrame:
        """
        获取K线数据，自动判断需要什么类型

        Returns:
            DataFrame: date, open, high, low, close, volume
        """
        from . import local_cache

        code6 = str(code).strip()

        # 1. 检查本地缓存和更新决策
        meta = local_cache._load_meta()
        cache_info = meta.get(code6, {})
        local_last_date = cache_info.get("end_date", "")
        meta_last_update = cache_info.get("last_update", "")

        decision = check_update_need(code6, local_last_date, meta_last_update)

        # 2. 根据决策获取数据
        if decision.update_type == DataUpdateDecision.NO_UPDATE:
            # 不需要更新，直接用本地
            df = local_cache.get_cached_kline(code6)
            if not df.empty:
                return df.tail(days)

        elif decision.update_type == DataUpdateDecision.REALTIME:
            # 需要实时数据
            df = self._realtime.get_kline(code6, days)
            if not df.empty:
                # 写入本地缓存
                local_cache.merge_kline_to_cache(code6, df)
                return df.tail(days)

        elif decision.update_type == DataUpdateDecision.CLOSE:
            # 需要收盘数据，用baostock
            df = self._baostock.get_kline(code6, days)
            if not df.empty:
                # 写入本地缓存
                local_cache.merge_kline_to_cache(code6, df)
                return df.tail(days)

        # 兜底：尝试本地缓存
        df = local_cache.get_cached_kline(code6)
        return df.tail(days) if not df.empty else pd.DataFrame()

    def get_realtime(self, code: str) -> dict:
        """获取实时行情"""
        return self._realtime.get_realtime(code)

    def get_batch(self, codes: list, days: int = 60) -> dict:
        """
        批量获取，自动判断每只股票需要什么类型的数据
        """
        from . import local_cache

        results = {}
        for code6 in codes:
            df = self.get_kline(code6, days)
            if not df.empty:
                results[code6] = df
        return results


# 全局实例
data_fetcher = DataFetcher()