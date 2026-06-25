"""
横截面因子计算模块

与 utils/precalc.py 的「单股逐只」指标不同，本模块计算的是**全市场横截面**因子：
需要把同一交易日所有股票放在一起比较、排名，无法在 BaseStrategy 的
逐只 _evaluate_single_stock 内算出。

当前提供：横截面反转分（cross-sectional reversal）。
依据 factor-ic-findings：A 股短周期是**反转**而非动量
（mom5 的 1 日 IC≈-0.027，t≈-2.38；近期涨多→次日跌）。
故这里按「近 N 日涨幅」做横截面排名，**跌得越多 → 反转分越高**。
"""

import logging
from typing import Callable, List, Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _to6(code: str) -> str:
    """去掉 sh/sz/bj 前缀，统一为 6 位代码（与 fetcher._kline_cache 键一致）"""
    c = str(code).strip()
    if len(c) > 2 and c[:2].lower() in ("sh", "sz", "bj"):
        c = c[2:]
    return c


def _default_df_getter(scanner, code6: str) -> Optional[pd.DataFrame]:
    """默认数据源：MarketScanner._kline_cache（实盘/Web 路径）。"""
    lock = getattr(scanner, "_lock", None)
    cache = getattr(scanner, "_kline_cache", None)
    if cache is None:
        return None
    if lock is not None:
        with lock:
            return cache.get(code6)
    return cache.get(code6)


def compute_reversal_scores(
    scanner,
    codes: List[str],
    lookback: int = 5,
    min_history: int = 20,
    df_getter: Optional[Callable] = None,
) -> Dict[str, dict]:
    """
    基于全市场 K 线构建横截面，按近 lookback 日涨幅反向排名。

    近期跌幅越大（ret 越低）→ 反转分越高（0-100 百分位）。结果挂到
    scanner._reversal_scores = {code6: {"ret": float, "score": float, "rank_pct": float}}
    并返回同一份 dict。

    只读取已有 K 线（不触发磁盘/网络 I/O）——调用前应已 prefetch/download。

    Args:
        scanner: 任何实现了数据访问的 scanner 实例
        codes: 参与横截面的股票代码（决定排名的可比集合）
        lookback: 反转窗口（默认 5 个交易日）
        min_history: 最少历史 bar 数，不足者不参与排名
        df_getter: 自定义数据获取函数 `(scanner, code6) -> DataFrame|None`，
            None=默认走 MarketScanner._kline_cache。回测路径传 PIT scanner 的 _pit_df。
    """
    getter = df_getter or _default_df_getter
    rets: Dict[str, float] = {}
    for code in codes:
        code6 = _to6(code)
        df = getter(scanner, code6)
        if df is None or len(df) < min_history or "close" not in df.columns:
            continue
        if len(df) <= lookback:
            continue
        close = df["close"].astype(float)
        c_now = float(close.iloc[-1])
        c_past = float(close.iloc[-1 - lookback])
        if c_past <= 0 or c_now <= 0:
            continue
        rets[code6] = (c_now - c_past) / c_past * 100.0

    if not rets:
        scanner._reversal_scores = {}
        logger.warning(f"横截面反转：可用样本为 0（codes={len(codes)}），跳过")
        return {}

    s = pd.Series(rets)
    # ascending=True：ret 最低者 rank_pct 最小；反转分 = (1 - rank_pct) * 100，故低 ret → 高分
    rank_pct = s.rank(pct=True, ascending=True)
    rev_score = (1.0 - rank_pct) * 100.0

    result = {
        code: {
            "ret": round(rets[code], 2),
            "score": round(float(rev_score[code]), 1),
            "rank_pct": round(float(rank_pct[code]), 4),
        }
        for code in rets
    }
    scanner._reversal_scores = result
    logger.info(f"横截面反转分计算完成: {len(result)} 只（lookback={lookback}）")
    return result
