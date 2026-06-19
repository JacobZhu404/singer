"""
A 股交易时间 / 交易日判断（全工程唯一真相源）

历史上同样的函数在 data/fetcher.py 和 data/data_layer.py 各写过一份，
返回类型甚至不一致（date vs datetime）。本模块为 §B2 收口后的唯一实现，
其他文件请 `from .market_calendar import is_market_open, ...`。
"""

import os
import json
import logging
from datetime import datetime, date, timedelta
from typing import Optional, Set

logger = logging.getLogger(__name__)


# 交易时段（A 股）
_MORNING_START = datetime.strptime("09:30", "%H:%M").time()
_MORNING_END   = datetime.strptime("11:30", "%H:%M").time()
_AFTERNOON_START = datetime.strptime("13:00", "%H:%M").time()
_AFTERNOON_END   = datetime.strptime("15:00", "%H:%M").time()
_CLOSE_TIME      = datetime.strptime("15:00", "%H:%M").time()


# ─────────────────────────────────────────────────────────
# 交易日历（节假日感知）
#
# 策略：硬编码官方休市日为离线兜底（每年须按国务院/交易所公告核对更新），
# 若曾成功联网，refresh_calendar_from_baostock() 会把校正结果写入
# data/cache/trade_calendar.json，import 时合并，让校正长期生效。
# 仅含「工作日里的休市日」（周末天然非交易日，不必列入）。
# ─────────────────────────────────────────────────────────
_STATIC_HOLIDAYS: Set[str] = {
    # 2025
    "2025-01-01",                                              # 元旦
    "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31",   # 春节
    "2025-02-03", "2025-02-04",
    "2025-04-04",                                              # 清明
    "2025-05-01", "2025-05-02", "2025-05-05",                 # 劳动节
    "2025-06-02",                                              # 端午
    "2025-10-01", "2025-10-02", "2025-10-03",                 # 国庆+中秋
    "2025-10-06", "2025-10-07", "2025-10-08",
    # 2026（须于年末按官方公告复核）
    "2026-01-01", "2026-01-02",                               # 元旦
    "2026-02-16", "2026-02-17", "2026-02-18",                 # 春节
    "2026-02-19", "2026-02-20",
    "2026-04-06",                                              # 清明
    "2026-05-01", "2026-05-04", "2026-05-05",                 # 劳动节
    "2026-06-19",                                              # 端午
    "2026-09-25",                                              # 中秋
    "2026-10-01", "2026-10-02", "2026-10-05",                 # 国庆
    "2026-10-06", "2026-10-07", "2026-10-08",
}

_CALENDAR_CACHE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cache", "trade_calendar.json"
)

_HOLIDAYS: Set[str] = set(_STATIC_HOLIDAYS)


def _load_cached_calendar() -> None:
    """合并 baostock 校正缓存（若存在）。缓存只增不减，作为硬编码的补充。"""
    try:
        if os.path.exists(_CALENDAR_CACHE):
            with open(_CALENDAR_CACHE, "r", encoding="utf-8") as f:
                data = json.load(f)
            holidays = data.get("holidays", []) if isinstance(data, dict) else data
            _HOLIDAYS.update(str(d) for d in holidays)
    except Exception as e:  # 缓存损坏不致命，退回硬编码
        logger.warning(f"读取交易日历缓存失败，使用硬编码: {e}")


_load_cached_calendar()


def is_holiday(d: date) -> bool:
    """该日期是否为 A 股休市的节假日（不含周末）。"""
    return d.strftime("%Y-%m-%d") in _HOLIDAYS


def is_trading_day(d: date) -> bool:
    """该日期是否为交易日（工作日且非节假日）。"""
    return d.weekday() < 5 and not is_holiday(d)


def refresh_calendar_from_baostock(years_ahead: int = 1) -> bool:
    """用 baostock 交易日历校正节假日并写入缓存（best-effort，失败返回 False）。

    仅在能联网时调用一次即可（节假日表全年固定）；离线时跳过，沿用硬编码。
    """
    try:
        import baostock as bs
    except Exception:
        return False
    try:
        lg = bs.login()
        if lg.error_code != "0":
            return False
        start = date(date.today().year, 1, 1)
        end = date(date.today().year + years_ahead, 12, 31)
        rs = bs.query_trade_dates(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        holidays = []
        while rs.error_code == "0" and rs.next():
            day_str, is_trade = rs.get_row_data()
            d = datetime.strptime(day_str, "%Y-%m-%d").date()
            if is_trade == "0" and d.weekday() < 5:  # 工作日但非交易日 = 节假日
                holidays.append(day_str)
        bs.logout()
        if holidays:
            _HOLIDAYS.update(holidays)
            os.makedirs(os.path.dirname(_CALENDAR_CACHE), exist_ok=True)
            with open(_CALENDAR_CACHE, "w", encoding="utf-8") as f:
                json.dump({"updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                           "holidays": sorted(_HOLIDAYS)}, f, ensure_ascii=False, indent=2)
            return True
    except Exception as e:
        logger.warning(f"baostock 交易日历校正失败，沿用硬编码: {e}")
    return False


def check_calendar_coverage() -> Optional[str]:
    """检查硬编码日历是否快用完，返回提醒文案（无需提醒返回 None）。

    - 当年完全没有节假日数据：严重——已退化为仅按周末判断。
    - 进入 Q4 仍无次年数据：提前提醒在年底前补充（春节可能落在 1 月）。

    每年手动更新一次 _STATIC_HOLIDAYS 即可；也可联网调用
    refresh_calendar_from_baostock() 自动补全。
    """
    years = {d[:4] for d in _HOLIDAYS}
    today = date.today()
    cur_y = str(today.year)
    nxt_y = str(today.year + 1)

    if cur_y not in years:
        return (f"交易日历缺少 {cur_y} 年节假日数据，已退化为仅按周末判断，"
                f"可能在节假日误判更新。请手动更新 _STATIC_HOLIDAYS 或联网校正。")
    if today.month >= 10 and nxt_y not in years:
        return (f"交易日历尚未包含 {nxt_y} 年节假日数据，请在年底前手动补充 "
                f"_STATIC_HOLIDAYS（否则春节等长假会误判更新）。")
    return None


def is_market_open() -> bool:
    """当前是否在 A 股可交易时段（含上下午两段，午休/周末/节假日返回 False）。"""
    now = datetime.now()
    if not is_trading_day(now.date()):
        return False
    t = now.time()
    return (_MORNING_START <= t <= _MORNING_END) or (_AFTERNOON_START <= t <= _AFTERNOON_END)


def is_market_break() -> bool:
    """当前是否在午休（11:30–13:00）。"""
    t = datetime.now().time()
    return _MORNING_END <= t <= _AFTERNOON_START


def is_market_closed_today() -> bool:
    """今天是否已收盘（15:00 后；周末也算"已收盘"，调用者自行判断是否交易日）。"""
    return datetime.now().time() >= _CLOSE_TIME


def get_today_str() -> str:
    """今天的 YYYY-MM-DD 字符串。"""
    return datetime.now().strftime("%Y-%m-%d")


def get_last_trading_date() -> Optional[date]:
    """
    最近一个「已经产生行情」的交易日（节假日感知）。

    - 盘中：返回今天。
    - 今天是交易日但尚未开盘：返回上一个交易日（今天还没有数据）。
    - 今天非交易日（周末/节假日）：返回最近的交易日。

    返回 datetime.date（不是 datetime）。需要时分秒的调用方请用 datetime.combine。
    """
    now = datetime.now()
    today = now.date()
    if is_market_open():
        return today
    # 往回最多找 15 天，足以跨过最长的春节/国庆长假
    for days_ago in range(0, 15):
        d = today - timedelta(days=days_ago)
        if is_trading_day(d):
            # 今天虽是交易日，但开盘前还没有今日数据，跳到上一个交易日
            if d != today or now.time() >= _MORNING_START:
                return d
    return None


def get_last_trading_date_str() -> str:
    """get_last_trading_date 的 YYYY-MM-DD 字符串形式（无交易日时退回今天）。"""
    d = get_last_trading_date()
    return d.strftime("%Y-%m-%d") if d else get_today_str()


def get_latest_trade_date() -> str:
    """YYYYMMDD 格式的"当前认定交易日"（保持与历史 fetcher.get_latest_trade_date 一致）。"""
    return datetime.now().strftime("%Y%m%d")
