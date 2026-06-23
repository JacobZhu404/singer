"""
腾讯批量实时行情适配器
- 集成到数据层作为实时数据源
"""

import logging
from typing import Dict

import pandas as pd

logger = logging.getLogger(__name__)

# 延迟导入
_tencent_batch = None


def _get_tencent_batch():
    global _tencent_batch
    if _tencent_batch is None:
        from . import tencent_batch
        _tencent_batch = tencent_batch
    return _tencent_batch


def get_realtime_batch(codes: list) -> Dict[str, dict]:
    """批量获取实时行情"""
    tb = _get_tencent_batch()
    return tb.get_realtime_fast(codes, max_workers=3)


def get_realtime(code: str) -> dict:
    """单只获取实时行情"""
    result = get_realtime_batch([code])
    return result.get(code, {})


def get_kline(code: str, days: int = 60) -> pd.DataFrame:
    """
    获取 K线：本地 CSV 历史 + 今日实时合并。

    §C2 修复（2026-06-18）：旧实现只返回今日 1 行，调用方拿到后写入
    内存缓存 / 本地 CSV，污染历史；策略层因 `len < days` 又忽略它退回读盘。
    现行：先取本地完整历史，再把今日报价 merge 到末尾，返回 tail(days)。
    """
    from . import local_cache
    from .realtime_merge import merge_realtime_into_history

    history = local_cache.get_cached_kline(code)
    # 没有本地历史就无法形成完整 K线 —— 返回空，让 data_layer 走兜底
    if history is None or history.empty:
        logger.debug(f"{code} 无本地历史，realtime.get_kline 返回空让上层兜底")
        return pd.DataFrame()

    realtime = get_realtime(code)
    merged = merge_realtime_into_history(history, realtime)
    return merged.tail(days) if merged is not None and not merged.empty else history.tail(days)