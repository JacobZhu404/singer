"""
大盘趋势判断工具
"""

import pandas as pd
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# 大盘指数代码
INDICES = {
    "shanghai": "000001.SH",   # 上证指数
    "shenzhen": "399001.SZ",   # 深证成指
    "chinext": "399006.SZ",     # 创业板指
}


def _get_index_ma(scanner, code: str, days: int = 60) -> Optional[Tuple[float, float, float]]:
    """
    获取单只指数的历史K线并计算 MA20、MA60 和当前收盘价。

    Args:
        scanner: MarketScanner 实例
        code: 指数代码
        days: 历史天数
    Returns:
        (current_close, ma20, ma60) 或 None（数据不足时）
    """
    try:
        df = scanner.get_history(code, days=days)
        if df is None or len(df) < days:
            logger.warning(f"大盘指数 {code} 数据不足")
            return None

        close = df["close"]
        ma20 = close.rolling(20).mean().iloc[-1]
        ma60 = close.rolling(60).mean().iloc[-1]
        current = close.iloc[-1]

        if pd.isna(ma20) or pd.isna(ma60):
            return None

        return current, ma20, ma60
    except Exception as e:
        logger.error(f"判断大盘趋势失败 ({code}): {e}")
        return None


def get_market_trend(scanner, days: int = 60) -> str:
    """
    判断大盘趋势
    返回: "bull" | "bear" | "neutral"

    逻辑：
    1. 获取3个大盘指数的MA20和MA60
    2. 如果≥2个指数 MA20>MA60 且 收盘价>MA20 → bull
    3. 如果≥2个指数 MA20<MA60 且 收盘价<MA20 → bear
    4. 否则 → neutral
    """
    bull_count = 0
    bear_count = 0

    for name, code in INDICES.items():
        result = _get_index_ma(scanner, code, days)
        if result is None:
            continue

        current, ma20, ma60 = result

        # 多头排列
        if ma20 > ma60 and current > ma20:
            bull_count += 1
        # 空头排列
        elif ma20 < ma60 and current < ma20:
            bear_count += 1

    if bull_count >= 2:
        return "bull"
    elif bear_count >= 2:
        return "bear"
    else:
        return "neutral"


def get_market_trend_strength(scanner, days: int = 60) -> float:
    """
    获取大盘趋势强度（-1.0 到 1.0）
    -1.0 = 极度熊市
     0.0 = 中性
     1.0 = 极度牛市
    """
    trends = []

    for name, code in INDICES.items():
        result = _get_index_ma(scanner, code, days)
        if result is None:
            continue

        current, ma20, ma60 = result

        # 计算趋势强度
        ma_trend = (ma20 - ma60) / ma60  # MA20相对MA60的涨幅
        price_trend = (current - ma20) / ma20  # 价格相对MA20的涨幅

        # 综合强度（-1.0 到 1.0）
        strength = (ma_trend + price_trend) / 2
        strength = max(-1.0, min(1.0, strength))  # 限制在[-1, 1]
        trends.append(strength)

    if not trends:
        return 0.0

    return sum(trends) / len(trends)
