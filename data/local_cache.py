"""
本地K线数据缓存层（CSV格式，线程安全）

目录结构：
  data/cache/klines/{code}.csv   K线数据
  data/cache/meta.json           元数据（更新时间/条数）
  data/cache/stocks.json         股票列表缓存
"""

import os
import json
import threading
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, Any

import pandas as pd

from .data_sources import _to_code6

logger = logging.getLogger(__name__)

_CACHE_ROOT  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
_KLINE_DIR   = os.path.join(_CACHE_ROOT, "klines")
_META_FILE   = os.path.join(_CACHE_ROOT, "meta.json")
_STOCKS_FILE = os.path.join(_CACHE_ROOT, "stocks.json")
_MAX_DAYS    = 400

_lock = threading.RLock()

# 内存缓存层：避免重复读磁盘
_kline_memory_cache: Dict[str, pd.DataFrame] = {}
_memory_cache_lock = threading.RLock()


def _ensure_dirs():
    os.makedirs(_KLINE_DIR, exist_ok=True)


def _kline_path(code: str) -> str:
    code6 = _to_code6(code)
    return os.path.join(_KLINE_DIR, f"{code6}.csv")


def _load_meta() -> Dict:
    """加载元数据缓存，跳过损坏的文件"""
    with _lock:
        if os.path.exists(_META_FILE):
            try:
                with open(_META_FILE, "r", encoding="utf-8") as f:
                    content = f.read()
                    if not content.strip():
                        return {}
                    return json.loads(content)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                # 损坏的JSON文件，直接跳过加载
                logger.warning(f"元数据缓存损坏: {e}，跳过加载")
                return {}
            except Exception as e:
                logger.warning(f"读取元数据缓存失败: {e}")
    return {}


def _save_meta(meta: Dict):
    with _lock:
        try:
            with open(_META_FILE, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存缓存元数据失败: {e}")


def get_cached_kline(code: str) -> pd.DataFrame:
    """优先从内存缓存读取，避免重复读磁盘"""
    code6 = _to_code6(code)
    # 先查内存缓存
    with _memory_cache_lock:
        if code6 in _kline_memory_cache:
            return _kline_memory_cache[code6].copy()

    path = _kline_path(code)
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        with _lock:
            df = pd.read_csv(path, encoding="utf-8")
        if not df.empty and "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])

        # 写入内存缓存
        with _memory_cache_lock:
            _kline_memory_cache[code6] = df.copy()
        return df
    except Exception as e:
        logger.warning(f"读取缓存失败 {code}: {e}")
        return pd.DataFrame()


def save_kline_to_cache(code: str, df: pd.DataFrame):
    if df.empty or "date" not in df.columns:
        return
    _ensure_dirs()
    code6 = _to_code6(code)
    path = _kline_path(code6)
    try:
        with _lock:
            df["date"] = pd.to_datetime(df["date"])
            # 直接从磁盘读取，避免递归调用 get_cached_kline
            existing = pd.DataFrame()
            if os.path.exists(path):
                try:
                    existing = pd.read_csv(path, encoding="utf-8")
                    if not existing.empty and "date" in existing.columns:
                        existing["date"] = pd.to_datetime(existing["date"])
                except Exception as e:
                    logger.warning(f"读取本地 K 线 CSV 失败 {path}: {e}（视为无旧数据，使用新数据覆盖）")
                    try:
                        from ..core.observability import obs
                        obs.warn("data.cache", "read_existing_csv",
                                 f"读取本地 CSV 失败 {code6}: {e}",
                                 context={"path": path, "action": "视为空，新数据直接写入"})
                    except Exception:
                        pass
            if not existing.empty:
                combined = pd.concat([existing, df], ignore_index=True)
                combined = combined.drop_duplicates(subset=["date"], keep="last")
            else:
                combined = df.copy()
            combined = combined.sort_values("date").reset_index(drop=True)
            # 裁剪超过最大保留天数的数据
            cutoff = datetime.now() - timedelta(days=_MAX_DAYS)
            combined = combined[combined["date"] >= cutoff].copy()
            combined.to_csv(path, index=False, encoding="utf-8")
            # 更新元数据
            meta = _load_meta()
            meta[code6] = {
                "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "records": len(combined),
                "start_date": combined["date"].min().strftime("%Y%m%d") if not combined.empty else "",
                "end_date":   combined["date"].max().strftime("%Y%m%d") if not combined.empty else "",
            }
            _save_meta(meta)
            # 同步更新内存缓存
            with _memory_cache_lock:
                _kline_memory_cache[code6] = combined.copy()
    except Exception as e:
        logger.warning(f"保存缓存失败 {code}: {e}")


def merge_kline_to_cache(code: str, new_df: pd.DataFrame) -> pd.DataFrame:
    save_kline_to_cache(code, new_df)
    return get_cached_kline(code)


def get_cached_stock_list() -> pd.DataFrame:
    if os.path.exists(_STOCKS_FILE):
        try:
            with open(_STOCKS_FILE, "r", encoding="utf-8") as f:
                return pd.DataFrame(json.load(f))
        except Exception as e:
            logger.warning(f"读取股票列表缓存失败: {e}")
    return pd.DataFrame()


def save_stock_list(df: pd.DataFrame):
    """原子写 stocks.json：先写临时文件再 os.replace，避免并发/中断产生
    "Extra data" 半截 JSON（曾把缓存写坏成无法解析）。空列表拒绝写入，
    避免一次失败把好缓存覆盖成 []。"""
    _ensure_dirs()
    records = df.to_dict(orient="records") if df is not None else []
    if not records:
        logger.warning("save_stock_list: 空列表，拒绝写入（避免覆盖好缓存）")
        return
    try:
        tmp = f"{_STOCKS_FILE}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _STOCKS_FILE)  # 原子替换
    except Exception as e:
        logger.warning(f"保存股票列表缓存失败: {e}")


def get_cached_codes() -> list:
    """
    返回本地缓存中已有的股票代码列表（扫描 klines 目录）。
    供 MarketScanner.get_cached_codes() 调用，避免在 fetcher.py 里重复拼路径。
    """
    import traceback
    codes = []
    try:
        if os.path.isdir(_KLINE_DIR):
            for fname in os.listdir(_KLINE_DIR):
                if fname.endswith(".csv"):
                    codes.append(fname[:-4])
        logger.info(f"get_cached_codes: 扫描到 {len(codes)} 个CSV文件, 路径={_KLINE_DIR}")
    except Exception as e:
        logger.error(f"get_cached_codes 扫描磁盘异常: {e}\n{traceback.format_exc()}")
    return codes


def get_cache_status() -> Dict:
    _ensure_dirs()
    meta = _load_meta()
    csv_files = [f for f in os.listdir(_KLINE_DIR) if f.endswith(".csv")]
    total_records = sum(m.get("records", 0) for m in meta.values())
    return {
        "total_stocks": len(csv_files),
        "total_records": total_records,
        "cache_dir": _CACHE_ROOT,
    }


def clear_cache():
    import shutil
    if os.path.exists(_CACHE_ROOT):
        shutil.rmtree(_CACHE_ROOT)
    _ensure_dirs()
    # 清空内存缓存
    with _memory_cache_lock:
        _kline_memory_cache.clear()
    logger.info("缓存已清空")


def clear_memory_cache():
    """仅清空内存缓存，保留磁盘文件"""
    with _memory_cache_lock:
        _kline_memory_cache.clear()
    logger.info("内存缓存已清空")
