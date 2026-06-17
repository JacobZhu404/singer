"""
全量指标预计算模块
在策略执行前，一次性为所有股票预计算常用技术指标并缓存到 scanner._indicator_cache，
避免各策略重复计算相同指标。

[优化]
- 并发数从8提升到16
- 跳过已有缓存的股票
"""

import logging
from typing import List, Callable, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..core.constants import MAX_WORKERS_PRECALC

logger = logging.getLogger(__name__)


def precalc_indicators(
    codes: List[str],
    scanner,
    days: int = 120,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """
    遍历所有股票代码，预计算技术指标并缓存到 scanner._indicator_cache。
    使用线程池并行加速（默认 8 并发）。

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

    # [优化] 跳过已有缓存的股票
    cache_key_suffix = f"_{days}_False"
    codes_to_calc = [c for c in codes if f"{c}{cache_key_suffix}" not in scanner._indicator_cache]

    if len(codes_to_calc) < len(codes):
        skipped = len(codes) - len(codes_to_calc)
        logger.info(f"预计算跳过 {skipped} 只（已缓存），实际计算 {len(codes_to_calc)} 只")

    if not codes_to_calc:
        return {"total": len(codes), "success": len(codes), "failed": 0}

    def _calc_one(code: str) -> tuple:
        # 跳过不在内存缓存的股票，避免触发磁盘/网络I/O
        with scanner._lock:
            in_memory = code in scanner._kline_cache
        if not in_memory:
            return code, False
        try:
            indicators = scanner.get_indicators(code, days=days)
            if indicators and "kline" in indicators:
                return code, True
            else:
                return code, False
        except Exception as e:
            logger.debug(f"预计算指标失败 {code}: {e}")
            return code, False

    with ThreadPoolExecutor(max_workers=MAX_WORKERS_PRECALC) as executor:
        futures = {executor.submit(_calc_one, code): code for code in codes_to_calc}
        for idx, future in enumerate(as_completed(futures), 1):
            code = futures[future]
            try:
                _, ok = future.result()
                if ok:
                    success += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

            if progress_callback and (idx % 50 == 0 or idx == len(codes_to_calc)):
                progress_callback(idx, len(codes_to_calc), code)

    logger.info(f"指标预计算完成: 成功 {success}/{len(codes_to_calc)}, 失败 {failed}, 跳过{len(codes)-len(codes_to_calc)}")
    return {"total": len(codes), "success": success + (len(codes) - len(codes_to_calc)), "failed": failed + len(codes_to_calc) - success - failed}
