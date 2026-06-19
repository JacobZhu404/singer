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
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# 延迟导入
_local_cache = None


def _get_cache():
    global _local_cache
    if _local_cache is None:
        from . import local_cache
        _local_cache = local_cache
    return _local_cache

# 延迟导入
_realtime_source = None
_baostock_source = None

# 是否使用腾讯批量（默认开启）
_USE_TENCENT_BATCH = True


def _get_realtime_source():
    global _realtime_source
    if _realtime_source is None:
        if _USE_TENCENT_BATCH:
            try:
                from . import tencent_realtime
                _realtime_source = tencent_realtime
                logger.info("使用腾讯批量实时数据源")
            except Exception as e:
                logger.warning(f"腾讯批量加载失败，使用默认: {e}")
                from .data_sources import data_manager
                _realtime_source = data_manager
        else:
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
# 交易时间判断（统一来源：data/market_calendar.py）
# ─────────────────────────────────────────────────────────

from .market_calendar import (  # noqa: E402,F401  re-export 以保持向后兼容
    is_market_open,
    is_market_break,
    is_market_closed_today,
    get_today_str,
    get_last_trading_date,
    get_last_trading_date_str,
)


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
    核心判断：是否需要更新，更新什么。

    核心原则：「从上次更新到这次更新，大盘有没有发生新交易」。没有新交易 →
    本地数据理论上仍与当前一致 → NO_UPDATE；有新交易（或正在交易）→ 更新。

    1. 盘中（交易日且正在交易）：当前 tick 永远比上次新 → REALTIME。
    2. 午休（11:30-13:00）：上午已收、盘价冻结。已有今日数据 → NO_UPDATE；
       尚无今日数据 → REALTIME 补抓上午行情。
    3. 盘前 / 盘后 / 周末 / 节假日：本地是否已覆盖「最新交易日」。覆盖 →
       区间内无新交易 → NO_UPDATE；落后 → CLOSE 补收盘价。

    Args:
        code: 股票代码
        local_last_date: 本地缓存的最新日期（"YYYY-MM-DD" 或 meta 的 "YYYYMMDD"）
        meta_last_update: meta 中的更新时间（保留参数，供调用方/排错使用）

    Returns:
        DataUpdateDecision: 是否需要更新及类型
    """
    in_market = is_market_open()
    in_break = is_market_break()
    today_str = get_today_str()
    # 「最新交易日」——节假日感知，替代旧的「日历昨天」。周末/盘前/假期后
    # 都能正确指向真正有行情的那一天，避免无谓的更新拉取。
    last_trading_str = get_last_trading_date_str()

    # 无本地数据，需要获取
    if not local_last_date:
        if in_market or in_break:
            return DataUpdateDecision("无数据，需要获取", DataUpdateDecision.REALTIME)
        return DataUpdateDecision("无数据，非交易时间", DataUpdateDecision.CLOSE)

    # 有本地数据。统一归一化为 YYYY-MM-DD：
    #   - prefetch_batch 传入 CSV 末行 → "2026-06-18"
    #   - DataFetcher.get_kline 传入 meta.end_date → "20260618"
    # 不归一化时，"20260618" 与 "2026-06-18" 字符串比较因 '0' > '-' 恒判最新，
    # 会让收盘后链路永远 NO_UPDATE、过期数据不刷新。
    local_date = str(local_last_date).split()[0] if local_last_date else ""
    if len(local_date) == 8 and local_date.isdigit():
        local_date = f"{local_date[:4]}-{local_date[4:6]}-{local_date[6:]}"

    # 场景1：盘中正在交易（交易日且未收盘）——实时刷新今日价
    if in_market:
        return DataUpdateDecision("盘中，刷新今日实时价", DataUpdateDecision.REALTIME)

    # 场景2：午休（上午已收，盘价冻结）
    if in_break:
        if local_date == today_str:
            return DataUpdateDecision("午休，已有今日数据，大盘暂停无新交易", DataUpdateDecision.NO_UPDATE)
        return DataUpdateDecision("午休，补抓上午行情", DataUpdateDecision.REALTIME)

    # 场景3：盘前 / 盘后 / 周末 / 节假日 —— 看是否已覆盖最新交易日
    # 本地已覆盖 → 上次更新至今大盘无新交易 → 一致，无需更新
    # （今天若停盘，最新交易日就是节前那天）
    if local_date >= last_trading_str:
        return DataUpdateDecision(
            f"非交易时间，本地{local_date}已覆盖最新交易日{last_trading_str}，无新交易",
            DataUpdateDecision.NO_UPDATE,
        )

    # 落后于最新交易日，需要获取收盘价
    return DataUpdateDecision(
        f"非交易时间，本地{local_date} < 最新交易日{last_trading_str}，需要收盘价",
        DataUpdateDecision.CLOSE,
    )


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

    def get_kline(self, code: str, days: int = 60, meta: Optional[dict] = None) -> pd.DataFrame:
        """
        获取K线数据，自动判断需要什么类型

        Args:
            meta: 可选的预加载 meta dict，避免每次重新读 meta.json（763KB）。
                  批量场景应一次性加载并复用，单只场景可省略。

        Returns:
            DataFrame: date, open, high, low, close, volume
        """
        from . import local_cache

        code6 = str(code).strip()

        # 1. 检查本地缓存和更新决策
        if meta is None:
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
            # 需要收盘数据：首选 baostock；失败/空 → 走多源降级（Sina/Tencent/Eastmoney）
            # §C3 修复（2026-06-18）：CLOSE 分支原先只调 baostock 单源，挂了即失败。
            df = pd.DataFrame()
            try:
                df = self._baostock.get_kline(code6, days)
            except Exception as e:
                logger.warning(f"baostock 获取 {code6} 失败，降级到多源: {e}")
            if df is None or df.empty:
                try:
                    from .data_sources import data_manager
                    df = data_manager.get_kline(code6, days)
                    if not df.empty:
                        logger.debug(f"{code6} baostock 无数据，多源降级成功")
                except Exception as e:
                    logger.warning(f"多源降级 {code6} 失败: {e}")
            if df is not None and not df.empty:
                # 写入本地缓存
                local_cache.merge_kline_to_cache(code6, df)
                return df.tail(days)

        # 兜底：尝试本地缓存
        df = local_cache.get_cached_kline(code6)
        return df.tail(days) if not df.empty else pd.DataFrame()

    def get_realtime(self, code: str) -> dict:
        """获取实时行情"""
        return self._realtime.get_realtime(code)

# 全局实例
data_fetcher = DataFetcher()