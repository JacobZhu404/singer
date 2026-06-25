"""把通达信离线 .day 文件批量转储到 data/cache/klines/{code}.csv。

输入: data/tdx_vipdoc/{sh,sz,bj}/lday/*.day
输出: data/cache/klines/{6位代码}.csv  (覆盖现有)

过滤策略:
  - 沪市: 6xxxxx (主板) + 688xxx (科创板) + 689xxx (科创板存托凭证) + 保留 000001/000300 (基准用)
  - 深市: 000/001/002/003/300/301 (跳过 39xxxx 指数和基金/可转债代码段)
  - 北交所: 全保留 (4/8/92 开头)
  - 其他 (ETF/指数/可转债/权证) 跳过

CSV 列与现有 cache 保持一致: date,open,close,high,low,vol,volume
"""

import os
import sys
import time
import logging
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT.parent))

from stock_screener.data.tdx_offline import TdxOfflineStore  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("import_tdx_to_cache")

TDX_BASE = PROJECT_ROOT / "data" / "tdx_vipdoc"
CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "klines"

# 基准/交易日历需要保留的指数/ETF
KEEP_INDEX = {"000001", "000300", "000852", "510300"}  # 上证综指、沪深300、中证1000、沪深300ETF


def _is_a_share(market: str, code6: str) -> bool:
    """判断一个 (市场, 6位代码) 是否是 A 股个股（或要保留的基准）。"""
    if code6 in KEEP_INDEX:
        return True
    if not code6.isdigit() or len(code6) != 6:
        return False
    if market == "sh":
        # 600/601/603/605 主板, 688/689 科创板
        if code6.startswith(("600", "601", "603", "605", "688", "689")):
            return True
        return False
    if market == "sz":
        # 000/001/002/003 主板/中小板, 300/301 创业板
        prefix3 = code6[:3]
        if prefix3 in ("000", "001", "002", "003", "300", "301"):
            return True
        return False
    if market == "bj":
        # 北交所 4/8/92 开头
        if code6.startswith(("4", "8", "92")):
            return True
        return False
    return False


def _list_targets(base_dir: Path):
    """扫描 TDX 目录，返回 [(market, code6, file_path), ...]"""
    targets = []
    for market in ("sh", "sz", "bj"):
        lday = base_dir / market / "lday"
        if not lday.is_dir():
            continue
        for f in os.scandir(lday):
            if not f.name.endswith(".day"):
                continue
            stem = f.name[:-4]  # sh600000
            if not stem.startswith(market):
                continue
            code6 = stem[len(market):]
            if not _is_a_share(market, code6):
                continue
            targets.append((market, code6, f.path))
    return targets


_STORE = None


def _init_worker(base_dir):
    global _STORE
    _STORE = TdxOfflineStore(base_dir)


def _convert_one(args):
    """读 .day 文件 → 写 CSV。返回 (code6, n_rows) 或 (code6, -1) 表示失败。"""
    market, code6, _fpath = args
    try:
        df = _STORE.read_day(market + code6)
        if df.empty:
            return code6, 0
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df["volume"] = ""
        df = df[["date", "open", "close", "high", "low", "vol", "volume"]]
        out_path = CACHE_DIR / f"{code6}.csv"
        df.to_csv(out_path, index=False)
        return code6, len(df)
    except Exception as e:
        logger.warning(f"{code6} 转换失败: {e}")
        return code6, -1


def main():
    if not TDX_BASE.is_dir():
        logger.error(f"TDX 目录不存在: {TDX_BASE}")
        sys.exit(1)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    targets = _list_targets(TDX_BASE)
    logger.info(f"扫描完成: 共 {len(targets)} 只待转储（过滤掉 ETF/指数/可转债后）")
    if not targets:
        logger.error("无目标文件")
        sys.exit(1)

    by_market = {}
    for m, _, _ in targets:
        by_market[m] = by_market.get(m, 0) + 1
    logger.info(f"分布: {by_market}")

    t0 = time.time()
    n_ok = n_empty = n_fail = 0
    with ProcessPoolExecutor(max_workers=8, initializer=_init_worker, initargs=(str(TDX_BASE),)) as ex:
        futures = [ex.submit(_convert_one, t) for t in targets]
        for i, fut in enumerate(as_completed(futures), 1):
            code6, n = fut.result()
            if n > 0:
                n_ok += 1
            elif n == 0:
                n_empty += 1
            else:
                n_fail += 1
            if i % 1000 == 0:
                logger.info(f"进度 {i}/{len(targets)}  ok={n_ok} empty={n_empty} fail={n_fail}")

    elapsed = time.time() - t0
    logger.info(f"\n转储完成: ok={n_ok} empty={n_empty} fail={n_fail}  用时 {elapsed:.1f}s")
    logger.info(f"输出目录: {CACHE_DIR}")


if __name__ == "__main__":
    main()
