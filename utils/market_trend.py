"""
大盘趋势判断工具

指数代码无法通过 6 位数字推断市场（如 000001 既是上证综指又是平安银行），
因此 market_scanner 的常规路由对指数失效。这里直接走腾讯指数 K 线接口，
用显式前缀（sh/sz）绕开 data_sources 的数字推断。
"""

import pandas as pd
import logging
from typing import Optional, Tuple

from ..data.data_sources import _get, _TENCENT_SESSION

logger = logging.getLogger(__name__)

# 大盘指数：腾讯接口符号（显式前缀，不可由数字推断）
INDICES = {
    "shanghai": "sh000001",   # 上证指数
    "shenzhen": "sz399001",   # 深证成指
    "chinext": "sz399006",    # 创业板指
}


def _fetch_index_close(symbol: str, days: int) -> Optional[pd.Series]:
    """直接拉取指数日线收盘序列（腾讯 fqkline 接口）。返回 close Series 或 None。"""
    resp = _get(_TENCENT_SESSION,
                "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                {"param": f"{symbol},day,,,{days},qfq"})
    if not resp:
        logger.warning(f"大盘指数 {symbol} 拉取失败")
        return None
    try:
        node = resp.json().get("data", {}).get(symbol, {})
        klines = node.get("day", []) or node.get("qfqday", [])
        closes = [float(k[2]) for k in klines if isinstance(k, list) and len(k) >= 3]
        if not closes:
            logger.warning(f"大盘指数 {symbol} 无 K 线数据")
            return None
        return pd.Series(closes)
    except Exception as e:
        logger.warning(f"大盘指数 {symbol} 解析失败: {type(e).__name__}: {e}")
        return None


def _get_index_ma(scanner, symbol: str, days: int = 60) -> Optional[Tuple[float, float, float]]:
    """
    获取单只指数的历史K线并计算 MA20、MA60 和当前收盘价。

    Args:
        scanner: 兼容旧签名，未使用（指数不走 scanner 路由）
        symbol: 指数符号（如 sh000001）
        days: 历史天数
    Returns:
        (current_close, ma20, ma60) 或 None（数据不足时）
    """
    close = _fetch_index_close(symbol, days)
    if close is None or len(close) < 60:
        logger.warning(f"大盘指数 {symbol} 数据不足（{0 if close is None else len(close)} < 60）")
        return None

    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    current = close.iloc[-1]

    if pd.isna(ma20) or pd.isna(ma60):
        return None

    return current, ma20, ma60


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

    for name, symbol in INDICES.items():
        result = _get_index_ma(scanner, symbol, days)
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

    for name, symbol in INDICES.items():
        result = _get_index_ma(scanner, symbol, days)
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
