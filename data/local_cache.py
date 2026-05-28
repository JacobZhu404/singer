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
from typing import Dict, Optional, Tuple

import pandas as pd

from .data_sources import _to_code6

logger = logging.getLogger(__name__)

_CACHE_ROOT  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
_KLINE_DIR   = os.path.join(_CACHE_ROOT, "klines")
_META_FILE   = os.path.join(_CACHE_ROOT, "meta.json")
_STOCKS_FILE = os.path.join(_CACHE_ROOT, "stocks.json")
_MAX_DAYS    = 400

_lock = threading.RLock()


def _ensure_dirs():
    os.makedirs(_KLINE_DIR, exist_ok=True)


def _kline_path(code: str) -> str:
    code6 = _to_code6(code)
    return os.path.join(_KLINE_DIR, f"{code6}.csv")


def _load_meta() -> Dict:
    with _lock:
        if os.path.exists(_META_FILE):
            try:
                with open(_META_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
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
    path = _kline_path(code)
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        with _lock:
            df = pd.read_csv(path, encoding="utf-8")
        if not df.empty and "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
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
            existing = get_cached_kline(code6)
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
    except Exception as e:
        logger.warning(f"保存缓存失败 {code}: {e}")


def merge_kline_to_cache(code: str, new_df: pd.DataFrame) -> pd.DataFrame:
    save_kline_to_cache(code, new_df)
    return get_cached_kline(code)


def needs_update(code: str, max_age_hours: int = 4) -> bool:
    meta = _load_meta()
    code6 = _to_code6(code)
    entry = meta.get(code6, {})
    last = entry.get("last_update", "")
    if not last:
        return True
    try:
        dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - dt).total_seconds() > max_age_hours * 3600
    except ValueError:
        return True


def get_cached_stock_list() -> pd.DataFrame:
    if os.path.exists(_STOCKS_FILE):
        try:
            with open(_STOCKS_FILE, "r", encoding="utf-8") as f:
                return pd.DataFrame(json.load(f))
        except Exception as e:
            logger.warning(f"读取股票列表缓存失败: {e}")
    return pd.DataFrame()


def save_stock_list(df: pd.DataFrame):
    _ensure_dirs()
    try:
        with open(_STOCKS_FILE, "w", encoding="utf-8") as f:
            json.dump(df.to_dict(orient="records"), f, ensure_ascii=False, indent=2)
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
    logger.info("缓存已清空")
