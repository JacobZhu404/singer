"""
全量指标预计算模块
在策略执行前，一次性为所有股票预计算常用技术指标并缓存到 scanner，
避免各策略重复计算相同指标。
"""

import logging
from typing import List, Callable, Optional

logger = logging.getLogger(__name__)


def precalc_indicators(
    codes: List[str],
    scanner,
    days: int = 120,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """
    遍历所有股票代码，预计算技术指标并缓存到 scanner._indicator_cache。

    默认 days=120 可覆盖所有策略需求（volume_breakout/rsi_oversold 用 60，
    chanlun/chanlun_strict/macd_bull/chan20 用 120）。

    Args:
        codes: 股票代码列表
        scanner: MarketScanner 实例
        days: K线天数
        progress_callback: 进度回调 (done, total, current_code)

    Returns:
        {"total": int, "success": int, "failed": int}
    """
    total = len(codes)
    success = 0
    failed = 0

    for idx, code in enumerate(codes, 1):
        try:
            indicators = scanner.get_indicators(code, days=days)
            if indicators and "kline" in indicators:
                success += 1
            else:
                failed += 1
        except Exception as e:
            logger.debug(f"预计算指标失败 {code}: {e}")
            failed += 1

        if progress_callback and (idx % 50 == 0 or idx == total):
            progress_callback(idx, total, code)

    logger.info(f"指标预计算完成: 成功 {success}/{total}, 失败 {failed}")
    return {"total": total, "success": success, "failed": failed}
