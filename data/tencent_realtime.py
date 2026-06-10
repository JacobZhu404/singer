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
    """获取K线（实时数据合成）"""
    """
    获取K线（实时模式）
    使用批量实时数据合成K线
    """
    # 获取实时数据
    realtime = get_realtime(code)
    if not realtime:
        return pd.DataFrame()

    # 转换为K线格式
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")

    price = realtime.get("price", 0)
    if price <= 0:
        return pd.DataFrame()

    open_price = realtime.get("open", price)
    high = realtime.get("high", price)
    low = realtime.get("low", price)
    volume = realtime.get("volume", 0)

    # 粗略估算全天成交量
    now = datetime.now()
    if now.hour < 11:
        # 上午，预估
        minutes_passed = (now.hour - 9) * 60 + now.minute - 30
    else:
        # 下午
        minutes_passed = 120 + (now.hour - 13) * 60 + now.minute

    if minutes_passed > 0:
        est_volume = volume * 240 / max(minutes_passed, 1)
    else:
        est_volume = volume

    df = pd.DataFrame([{
        "date": today,
        "open": open_price,
        "high": high,
        "low": low,
        "close": price,
        "volume": est_volume,
    }])

    return df