"""
数据获取层：本地缓存 + 多数据源（新浪/腾讯/东财），无需 Token
"""

import time
import logging
import threading
from datetime import datetime
from typing import Optional, List, Dict, Callable, Tuple, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

logger = logging.getLogger(__name__)

# 延迟导入避免循环依赖
_lazy_cache = None
_lazy_manager = None


def _get_cache():
    global _lazy_cache
    if _lazy_cache is None:
        from . import local_cache
        _lazy_cache = local_cache
    return _lazy_cache


def _get_manager():
    global _lazy_manager
    if _lazy_manager is None:
        from .data_sources import data_manager
        _lazy_manager = data_manager
    return _lazy_manager


def get_stock_history(code: str, days: int = 60) -> pd.DataFrame:
    """
    获取单只股票历史K线（本地缓存 → 网络多源降级）
    """
    cache = _get_cache()
    manager = _get_manager()
    code6 = str(code).strip().replace("sh", "").replace("sz", "")

    cached = cache.get_cached_kline(code6)
    if not cached.empty:
        cached_days = len(cached)
        if cached_days >= days and not cache.needs_update(code6, max_age_hours=4):
            return cached.tail(days).reset_index(drop=True)
        days = max(days, days - cached_days + 10)

    df = manager.get_kline(code6, days)
    if df.empty:
        if not cached.empty:
            logger.warning(f"网络失败，使用旧缓存: {code6}")
            return cached.tail(days).reset_index(drop=True)
        return pd.DataFrame()

    full_df = cache.merge_kline_to_cache(code6, df)
    return full_df.tail(days).reset_index(drop=True)


def get_stock_realtime(code: str) -> dict:
    """获取单只股票实时行情"""
    return _get_manager().get_realtime(code)


def get_stock_list() -> pd.DataFrame:
    """获取全市场股票列表（本地缓存优先），返回标准化列名 [ts_code, name]"""
    cache = _get_cache()
    manager = _get_manager()

    cached = cache.get_cached_stock_list()
    if not cached.empty:
        # 标准化列名：支持中文列名或旧编码列名
        rename = {}
        for col in list(cached.columns):
            if col in ("symbol", "code", "代码", "股票代码"):
                rename[col] = "ts_code"
            elif col in ("name", "名称", "股票名称"):
                rename[col] = "name"
        if rename:
            cached = cached.rename(columns=rename)
        # 兜底：如果列名仍是乱码，尝试用位置索引
        if "ts_code" not in cached.columns and len(cached.columns) >= 1:
            cached = cached.rename(columns={cached.columns[0]: "ts_code"})
        if "name" not in cached.columns and len(cached.columns) >= 2:
            cached = cached.rename(columns={cached.columns[1]: "name"})
        logger.info(f"股票列表(缓存): {len(cached)} 只")
        return cached

    df = manager.get_stock_list()
    if not df.empty:
        # 标准化列名
        rename = {}
        for col in list(df.columns):
            if col in ("symbol", "code", "代码", "股票代码"):
                rename[col] = "ts_code"
            elif col in ("name", "名称", "股票名称"):
                rename[col] = "name"
        if rename:
            df = df.rename(columns=rename)
        cache.save_stock_list(df)
    return df


def get_latest_trade_date() -> str:
    return datetime.now().strftime("%Y%m%d")


def get_limit_list(trade_date: Optional[str] = None) -> pd.DataFrame:
    """
    获取涨停股票列表（按涨跌幅倒序）
    通过新浪接口获取全市场数据，筛选涨幅 >= 9.5% 的股票
    """
    try:
        import requests
        url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
        page = 1
        all_data = []
        headers = {
            "Referer": "https://finance.sina.com.cn/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }
        while page <= 10:
            params = {
                "page": page,
                "num": 100,
                "sort": "changepercent",
                "asc": 0,
                "node": "hs_a",
                "symbol": "",
                "_s_r_a": "page",
            }
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            resp.encoding = "utf-8"
            text = resp.text.strip()
            if not text or text == "null" or "data" not in text.lower():
                break
            # 解析JS对象格式
            import re, json
            # 提取数组部分
            m = re.search(r'\[(.+)\]', text, re.DOTALL)
            if not m:
                break
            items = json.loads(f"[{m.group(1)}]")
            if not items:
                break
            all_data.extend(items)
            if len(items) < 100:
                break
            page += 1

        if not all_data:
            return pd.DataFrame()

        df = pd.DataFrame(all_data)
        # 筛选涨停股
        if "changepercent" in df.columns:
            df = df[df["changepercent"] >= 9.5]
        elif "pct_chg" in df.columns:
            df = df[df["pct_chg"] >= 9.5]

        # 标准化列名
        rename = {}
        for col in df.columns:
            if col in ("symbol", "code", "代码"):
                rename[col] = "ts_code"
            elif col in ("name", "名称"):
                rename[col] = "name"
        if rename:
            df = df.rename(columns=rename)

        return df.reset_index(drop=True)
    except Exception as e:
        logger.debug(f"获取涨停股列表失败: {e}")
        return pd.DataFrame()


# ─── 全市场扫描器 ────────────────────────────────────────────────────────────

class MarketScanner:
    """
    全市场扫描器：三层缓存（内存 → 本地文件 → 网络），线程安全。
    """

    def __init__(self):
        self._loaded: bool = False
        self._kline_cache: Dict[str, pd.DataFrame] = {}
        self._cache_days: Dict[str, int] = {}
        self._indicator_cache: Dict[str, Dict[str, Any]] = {}  # 代码 → 预计算指标
        self._lock = threading.Lock()
        self._include_realtime: bool = True

    def load(self) -> bool:
        self._loaded = True
        return True

    def get_history(self, code: str, days: int = 60, pure: bool = False) -> pd.DataFrame:
        """单只K线（内存 → 本地 → 网络），线程安全"""
        code6 = str(code).strip()
        if len(code6) > 2 and code6[:2].lower() in ("sh", "sz"):
            code6 = code6[2:]
        today = datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            if code6 in self._kline_cache and self._cache_days.get(code6, 0) >= days:
                cached = self._kline_cache[code6]
                last_date = str(cached["date"].iloc[-1]).split()[0]
                if pure:
                    # pure 模式：只有缓存不含今日数据时才直接返回（含实时合并的缓存要绕过）
                    if last_date != today:
                        return cached
                    # 被实时污染了，继续走下面的逻辑重新获取纯历史
                elif self._include_realtime:
                    # 非 pure + 开关开启：只有缓存已有今天数据才直接返回
                    if last_date == today:
                        return cached
                    # 否则继续重新获取并合并
                else:
                    # 开关关闭：直接返回缓存（纯历史）
                    return cached
        df = get_stock_history(code6, days)
        if not df.empty and not pure and self._include_realtime:
            df = self._merge_today_realtime(df, code6)
        if not df.empty:
            with self._lock:
                self._kline_cache[code6] = df
                self._cache_days[code6] = days
        return df

    def prefetch_batch(
        self,
        codes: List[str],
        days: int = 60,
        max_workers: int = 50,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict:
        """
        并发预加载K线到内存缓存，带自动补全机制。

        分3轮执行：
          第1轮：高并发(max_workers)快速下载
          第2轮：低并发(15)补全第1轮失败的
          第3轮：串行补全剩余的（每次间隔1s避免限流）

        Returns: {"cached": int, "failed": list, "total": int}
        """
        def to6(c: str) -> str:
            c = str(c).strip()
            return c[2:] if len(c) > 2 and c[:2].lower() in ("sh", "sz") else c

        all_codes = [to6(c) for c in codes]
        with self._lock:
            need_fetch = [c for c in all_codes
                          if c not in self._kline_cache or self._cache_days.get(c, 0) < days]

        if not need_fetch:
            logger.info("预加载K线: 全部命中缓存")
            return {"cached": len(all_codes), "failed": [], "total": len(all_codes)}

        logger.info(f"预加载K线: {len(need_fetch)}/{len(all_codes)} 只需网络获取")

        # 第1轮：高并发快速下载
        failed = self._fetch_round(need_fetch, days, max_workers, "第1轮",
                                   progress_callback)

        # 第2轮：中并发补全
        if failed:
            logger.info(f"第1轮完成，{len(failed)}只失败，启动第2轮补全(并发15)")
            failed = self._fetch_round(failed, days, 15, "第2轮",
                                       progress_callback)

        # 第3轮：串行补全（每只间隔1s）
        if failed:
            logger.info(f"第2轮后仍有{len(failed)}只失败，启动第3轮串行补全")
            failed = self._fetch_round(failed, days, 1, "第3轮",
                                       progress_callback)

        if failed:
            logger.warning(f"最终仍有 {len(failed)} 只无法获取: {failed[:20]}...")

        cache = _get_cache()
        with self._lock:
            mem_count = len(self._kline_cache)
        logger.info(f"K线缓存完成: 内存{mem_count}只 | 本地{cache.get_cache_status()['total_stocks']}只 | 失败{len(failed)}只")

        if progress_callback:
            progress_callback("完成", mem_count, len(all_codes))

        return {"cached": mem_count, "failed": failed or [], "total": len(all_codes)}

    def _fetch_round(self, codes: List[str], days: int, workers: int,
                     round_label: str, callback=None) -> List[str]:
        """单轮下载，返回失败的代码列表"""
        done_count = 0
        total = len(codes)
        failed_codes = []

        def _fetch_one(code6: str):
            nonlocal done_count
            df = get_stock_history(code6, days)

            # 重试逻辑：最多重试3次，递增延迟
            retry_delays = [0.5, 1.0, 2.0]
            for delay in retry_delays:
                if not df.empty:
                    break
                time.sleep(delay)
                df = get_stock_history(code6, days)

            with self._lock:
                done_count += 1
                cur = done_count

            if not df.empty:
                with self._lock:
                    self._kline_cache[code6] = df
                    self._cache_days[code6] = days
            else:
                failed_codes.append(code6)

            if callback and cur % max(50, workers * 2) == 0:
                callback(code6, cur, total)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(_fetch_one, codes))

        return failed_codes

    def clear_memory_cache(self) -> None:
        with self._lock:
            self._kline_cache.clear()
            self._cache_days.clear()
            self._indicator_cache.clear()

    def get_realtime(self, code: str) -> dict:
        return get_stock_realtime(code)

    def get_quotes(self, codes: List[str]) -> pd.DataFrame:
        """
        批量获取股票实时行情，返回 DataFrame。
        兼容涨停基因策略的调用方式。
        """
        results = self.get_realtime_batch(codes)
        if not results:
            return pd.DataFrame()
        df = pd.DataFrame(list(results.values()))
        return df

    def get_realtime_quotes_sina(self, page: int = 1, num: int = 100) -> pd.DataFrame:
        """
        通过新浪接口获取全市场实时行情（单页）。
        可分页调用获取全市场数据，用于快速行情扫描。
        """
        import requests
        url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
        headers = {
            "Referer": "https://finance.sina.com.cn/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        }
        params = {
            "page": page, "num": num,
            "sort": "changepercent", "asc": 0,
            "node": "hs_a", "_s_r_a": "page",
        }
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            resp.encoding = "utf-8"
            import re, json
            m = re.search(r'\[(.+)\]', resp.text.strip(), re.DOTALL)
            if not m:
                return pd.DataFrame()
            rows = json.loads(f"[{m.group(1)}]")
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(rows)
            # 标准化列名
            col_map = {}
            for c in df.columns:
                if c in ("symbol", "code", "代码"):
                    col_map[c] = "ts_code"
                elif c in ("name", "名称"):
                    col_map[c] = "name"
            return df.rename(columns=col_map) if col_map else df
        except Exception as e:
            logger.debug(f"get_realtime_quotes_sina page={page} failed: {e}")
            return pd.DataFrame()

    def get_realtime_batch(self, codes: List[str]) -> Dict[str, dict]:
        """
        批量获取实时行情（利用腾讯批量接口，最多 90 只/次）

        Args:
            codes: 股票代码列表
        Returns:
            Dict[str, dict]: {code: quote_dict}，不含该代码时 key 不存在
        """
        return _get_manager().get_realtime_batch(codes)

    def get_indicators(self, code: str, days: int = 60, pure: bool = False) -> Dict[str, Any]:
        """
        获取预计算的常用技术指标（带内存缓存）

        策略层可直接复用，避免每个策略重复计算相同指标。
        缓存以 (code, days, pure) 为 key，按需过期。

        返回 dict 结构:
            kline   — DataFrame, K线数据
            macd    — (dif Series, dea Series, bar Series)
            rsi     — Series
            ma      — dict: {ma5, ma10, ma20, ma60}
            bollinger — dict: {upper, mid, lower}
            vol_ratio — Series
            td_count  — Series
            skdj    — (sk Series, sd Series)
        """
        cache_key = f"{code}_{days}_{pure}"
        with self._lock:
            if cache_key in self._indicator_cache:
                return self._indicator_cache[cache_key]

        # 延迟导入避免循环依赖
        from ..utils import indicators as ind

        df = self.get_history(code, days, pure=pure)
        if df.empty or len(df) < 20:
            return {}

        close = df["close"]
        vol = df["vol"]

        try:
            macd = ind.calc_macd(close)
            rsi = ind.calc_rsi(close, 14)
            ma = ind.calc_ma(close, [5, 10, 20, 60])
            bb = ind.calc_bollinger(close)
            vr = ind.calc_volume_ratio(vol, 5)
            high = df["high"] if "high" in df.columns else close
            low = df["low"] if "low" in df.columns else close
            td = ind.td_sequential_count(close, high=high, low=low)
            sk, sd = ind.calc_skdj(close, high, low)

            result = {
                "kline": df,
                "macd": macd,          # (dif, dea, bar)
                "rsi": rsi,            # Series
                "ma": ma,               # {ma5, ma10, ...}
                "bollinger": bb,        # {upper, mid, lower}
                "vol_ratio": vr,        # Series
                "td_count": td,         # Series
                "skdj": (sk, sd),       # (sk, sd)
            }

            with self._lock:
                self._indicator_cache[cache_key] = result

            return result
        except Exception:
            return {}

    def get_cache_status(self) -> dict:
        cache = _get_cache()
        local = cache.get_cache_status()
        with self._lock:
            mem_count = len(self._kline_cache)
        return {
            "memory_cached": mem_count,
            "cached_count": local.get("total_stocks", 0),
            "local_records": local.get("total_records", 0),
            "cache_dir": local.get("cache_dir", ""),
        }

    def evaluate_batch(
        self,
        codes: List[str],
        evaluator: Callable[[str, Optional[pd.DataFrame]], Optional[Tuple[str, any]]],
        max_workers: int = 30,
    ) -> List[Tuple[str, any]]:
        """
        并行批量评估股票（所有策略通用并行接口）。

        Args:
            codes: 股票代码列表
            evaluator: 评估函数，签名为 (code, df_or_None) -> Optional[(code, result)]
                      - df_or_None: 该股票的K线DataFrame（可能为None表示数据不足）
                      - 返回 None 则该股票被过滤，返回 (code, result) 则加入结果
            max_workers: 并发线程数（默认30）

        Returns:
            List[(code, result)] - 所有通过评估的股票及其结果
        """
        def _eval_one(code: str) -> Optional[Tuple[str, any]]:
            df = self.get_history(code, days=60)
            return evaluator(code, df)

        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_eval_one, c): c for c in codes}
            for future in as_completed(futures):
                try:
                    r = future.result()
                    if r is not None:
                        results.append(r)
                except Exception:
                    pass
        return results

    def _merge_today_realtime(self, df: pd.DataFrame, code6: str) -> pd.DataFrame:
        """
        将实时行情合成到K线末尾，让策略能基于当天盘中数据计算。
        仅在交易日盘中有效，非交易日不合并。
        """
        if df.empty or "date" not in df.columns:
            return df
        today = datetime.now().strftime("%Y-%m-%d")
        # 避免重复合并（兼容 date 为字符串或 datetime64 两种格式）
        last_date = str(df["date"].iloc[-1]).split()[0]
        if last_date == today:
            return df
        quote = self.get_realtime(code6)
        if not quote:
            return df
        price = quote.get("最新价", 0)
        if price <= 0:
            return df
        # 判断今天是否有交易：有成交量或涨跌幅非零
        vol = quote.get("成交量", 0)
        pct = quote.get("涨跌幅", 0)
        if vol <= 0 and abs(pct) < 0.01:
            return df
        open_price = quote.get("今开", price)
        high_price = quote.get("最高价", max(open_price, price))
        low_price = quote.get("最低价", min(open_price, price))
        # 成交量用预估全天量，避免早盘量比被严重低估
        est_vol = self._estimate_full_day_volume(vol)
        # 构造今日K线行（与历史K线列对齐）
        row = {"date": today, "open": open_price, "close": price,
               "high": high_price, "low": low_price, "vol": est_vol}
        # 兼容可能的额外列（如 pct_chg）
        for col in df.columns:
            if col not in row:
                if col in ("pct_chg", "daily_chg"):
                    row[col] = pct
                else:
                    row[col] = 0.0
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        return df

    @staticmethod
    def _estimate_full_day_volume(current_vol: float) -> float:
        """
        按A股交易时间比例预估全天成交量。
        9:30-11:30(120min), 13:00-15:00(120min), 共240min。
        """
        if current_vol <= 0:
            return 0.0
        now = datetime.now().time()
        morning_start = datetime.strptime("09:30", "%H:%M").time()
        morning_end = datetime.strptime("11:30", "%H:%M").time()
        afternoon_start = datetime.strptime("13:00", "%H:%M").time()
        afternoon_end = datetime.strptime("15:00", "%H:%M").time()

        if morning_start <= now <= morning_end:
            minutes = (now.hour - 9) * 60 + (now.minute - 30)
        elif afternoon_start <= now <= afternoon_end:
            minutes = 120 + (now.hour - 13) * 60 + now.minute
        else:
            return current_vol
        if minutes <= 0:
            return current_vol
        return current_vol * 240 / minutes


# 全局扫描器（所有策略共享）
market_scanner = MarketScanner()
