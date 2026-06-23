"""
实时报价 → 当日 K线合并（全工程唯一真相源）

§B1 之前：fetcher / data_layer / tencent_realtime 各有一份合成逻辑，
字段命名（中文 vs 英文）和合并语义不一致；其中 tencent_realtime.get_kline
还会返回"只有今日 1 行"的 df 污染缓存（§C2）。本模块为收口实现，
所有需要把实时一根 K线接到历史末尾的调用都应走这里。
"""

from datetime import datetime
from typing import Optional, Dict, Any

import pandas as pd

# 实时报价字段同义词：把任意源的 quote 标准化到 (price/open/high/low/vol/pct)
_PRICE_KEYS  = ("最新价", "price", "close", "now")
_OPEN_KEYS   = ("今开", "open")
_HIGH_KEYS   = ("最高价", "high")
_LOW_KEYS    = ("最低价", "low")
_VOL_KEYS    = ("成交量", "volume", "vol")
_PCT_KEYS    = ("涨跌幅", "pct_chg", "changepercent")


def _pick(quote: Dict[str, Any], keys: tuple, default=0.0) -> float:
    for k in keys:
        v = quote.get(k)
        if v not in (None, "", 0):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    # 没非零的命中，再退一步用第一个非 None
    for k in keys:
        v = quote.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return default


def normalize_quote(quote: Dict[str, Any]) -> Dict[str, float]:
    """把任意源（中文/英文 keys）的 quote 标准化。"""
    price = _pick(quote, _PRICE_KEYS)
    return {
        "price": price,
        "open":  _pick(quote, _OPEN_KEYS, default=price),
        "high":  _pick(quote, _HIGH_KEYS, default=price),
        "low":   _pick(quote, _LOW_KEYS, default=price),
        "vol":   _pick(quote, _VOL_KEYS),
        "pct":   _pick(quote, _PCT_KEYS),
    }


def estimate_full_day_volume(current_vol: float, now: Optional[datetime] = None) -> float:
    """按 A 股交易时段比例预估全天成交量。盘外原样返回。"""
    if current_vol <= 0:
        return 0.0
    now = now or datetime.now()
    t = now.time()
    morning_start = datetime.strptime("09:30", "%H:%M").time()
    morning_end   = datetime.strptime("11:30", "%H:%M").time()
    afternoon_start = datetime.strptime("13:00", "%H:%M").time()
    afternoon_end   = datetime.strptime("15:00", "%H:%M").time()

    if morning_start <= t <= morning_end:
        minutes = (now.hour - 9) * 60 + (now.minute - 30)
    elif afternoon_start <= t <= afternoon_end:
        minutes = 120 + (now.hour - 13) * 60 + now.minute
    else:
        return current_vol
    if minutes <= 0:
        return current_vol
    return current_vol * 240 / minutes


def merge_realtime_into_history(
    history_df: pd.DataFrame,
    quote: Dict[str, Any],
    today_str: Optional[str] = None,
    volume_unit_is_lots: bool = False,
) -> pd.DataFrame:
    """
    把今日实时报价合并到历史 K线末尾。

    Args:
        history_df: 历史 K线（必含 date/open/close/high/low/vol）
        quote: 实时报价 dict（中文或英文 keys 自动识别）
        today_str: 今日日期 YYYY-MM-DD；默认 datetime.now()
        volume_unit_is_lots: True 时把成交量从"手"转"股"（×100）

    Returns:
        合并后的 df。若 quote 无效 / 已含今日 / 历史空，原样返回。
    """
    if history_df is None or history_df.empty or "date" not in history_df.columns:
        return history_df if history_df is not None else pd.DataFrame()

    today = today_str or datetime.now().strftime("%Y-%m-%d")

    # 末尾是否已经是今日？是则不动
    last_date_val = history_df["date"].iloc[-1]
    if hasattr(last_date_val, "strftime"):
        last_date = last_date_val.strftime("%Y-%m-%d")
    else:
        last_date = str(last_date_val).split()[0]
    if last_date == today:
        return history_df

    if not quote:
        return history_df

    q = normalize_quote(quote)
    if q["price"] <= 0:
        return history_df

    vol = q["vol"]
    if volume_unit_is_lots:
        vol = vol * 100
    # 没成交量且涨跌幅近 0：可能是空报价，不污染
    if vol <= 0 and abs(q["pct"]) < 0.01:
        return history_df

    est_vol = estimate_full_day_volume(vol)
    row = {
        "date": pd.Timestamp(today),
        "open": q["open"],
        "close": q["price"],
        "high": q["high"],
        "low": q["low"],
        "vol": est_vol,
    }
    # 补齐其它列
    for col in history_df.columns:
        if col not in row:
            if col in ("pct_chg", "daily_chg"):
                row[col] = q["pct"]
            else:
                row[col] = 0.0
    new_row = pd.DataFrame([row])
    if history_df["date"].dtype != "object":
        new_row["date"] = pd.to_datetime(new_row["date"])
    return pd.concat([history_df, new_row], ignore_index=True)
