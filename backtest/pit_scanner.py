# -*- coding: utf-8 -*-
"""
PointInTimeScanner — 回测专用的「时点扫描器」适配器

目的：让回测直接复用 strategies/*.py 里**真实的策略类**（_evaluate_single_stock），
而不是在回测里重新实现一遍策略逻辑（旧 backtest_engine._strategy_check 的做法，
会与实盘策略漂移，且新策略加不进来）。

做法：实现策略所需的最小 scanner 接口（get_indicators / get_realtime / get_history /
load），但所有数据都裁剪到 as_of 当日为止——严格杜绝未来函数。指标用与实盘相同的
utils.indicators.compute_indicator_bundle 计算，保证「策略在回测看到的指标」与实盘一致。

迭代顺序为「日期 → 策略 → 股票」，故指标缓存按 as_of 复用、换日清空。
"""

import os
import threading
from typing import Dict

import pandas as pd

from stock_screener.utils.indicators import compute_indicator_bundle

_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "cache", "klines",
)


class PointInTimeScanner:
    """按 as_of 日期提供时点数据的扫描器（线程安全）。"""

    def __init__(self):
        self._df_cache: Dict[str, pd.DataFrame] = {}     # code -> 完整 df（不可变）
        self._ind_cache: Dict[tuple, dict] = {}          # (code, days) -> 指标包，换日清空
        self._as_of = None
        self._df_lock = threading.Lock()
        self._ind_lock = threading.Lock()

    # ── 时点控制 ──────────────────────────────────────────────
    def set_as_of(self, date_str: str):
        """设置当前回测时点（YYYYMMDD），并清空上一时点的指标缓存。"""
        with self._ind_lock:
            self._as_of = pd.to_datetime(date_str)
            self._ind_cache.clear()

    def load(self):
        """策略基类会调用 scanner.load()，回测无需操作。"""
        return None

    # ── 数据读取 ──────────────────────────────────────────────
    def _full_df(self, code: str) -> pd.DataFrame:
        with self._df_lock:
            if code in self._df_cache:
                return self._df_cache[code]
        path = os.path.join(_CACHE_DIR, f"{code}.csv")
        if not os.path.exists(path):
            df = pd.DataFrame()
        else:
            try:
                df = pd.read_csv(path, encoding="utf-8")
                df["date"] = pd.to_datetime(df["date"])
                df = df.sort_values("date").reset_index(drop=True)
            except Exception:
                df = pd.DataFrame()
        with self._df_lock:
            self._df_cache[code] = df
        return df

    def _pit_df(self, code: str, days: int = None) -> pd.DataFrame:
        """返回 as_of 当日及之前的 K 线（最近 days 根）。"""
        df = self._full_df(code)
        if df.empty or self._as_of is None:
            return df
        sl = df[df["date"] <= self._as_of]
        if days:
            sl = sl.tail(days)
        return sl.reset_index(drop=True)

    # ── 策略所需接口 ──────────────────────────────────────────
    def get_history(self, code: str, days: int = 60, pure: bool = False) -> pd.DataFrame:
        return self._pit_df(code, days)

    def get_indicators(self, code: str, days: int = 60, pure: bool = False) -> dict:
        key = (code, days)
        with self._ind_lock:
            if key in self._ind_cache:
                return self._ind_cache[key]
        df = self._pit_df(code, days)
        result = compute_indicator_bundle(df)
        with self._ind_lock:
            self._ind_cache[key] = result
        return result

    def get_realtime(self, code: str) -> dict:
        """用 as_of 当日 K 线合成实时行情；换手率/量比无法回溯，给中性值。"""
        df = self._pit_df(code, 2)
        if df.empty:
            return {}
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else last
        close = float(last["close"])
        prev_close = float(prev["close"])
        pct = (close - prev_close) / prev_close * 100 if prev_close > 0 else 0.0
        return {
            "code": code,
            "最新价": close,
            "昨收": prev_close,
            "今开": float(last["open"]) if "open" in df.columns else close,
            "成交量": float(last["vol"]) if "vol" in df.columns else 0.0,
            "成交额": 0.0,
            "换手率": 0.0,   # 回测无法回溯换手率
            "市盈率": 0.0,
            "涨跌幅": pct,
            "涨跌额": close - prev_close,
            "最高价": float(last["high"]) if "high" in df.columns else close,
            "最低价": float(last["low"]) if "low" in df.columns else close,
        }
