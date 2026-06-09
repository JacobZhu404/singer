"""
数据获取层：本地缓存 + 多数据源（新浪/腾讯/东财），无需 Token
"""

import time
import logging
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Callable, Tuple, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

logger = logging.getLogger(__name__)

# 延迟导入避免循环依赖
_lazy_cache = None
_lazy_manager = None
_lazy_tdx = None


# ── 公共列名标准化 ─────────────────────────────────────────────────────────

# 代码列的可能名称（按优先级）
_CODE_ALIASES = ("symbol", "code", "代码", "股票代码")
# 名称列的可能名称（按优先级）
_NAME_ALIASES = ("name", "名称", "股票名称")


def normalize_stock_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    标准化股票 DataFrame 列名：
    - 代码相关列统一为 'ts_code'
    - 名称相关列统一为 'name'
    返回一个新的 DataFrame（不修改原表）。
    """
    if df.empty:
        return df.copy()

    rename = {}
    for col in list(df.columns):
        if col in _CODE_ALIASES:
            rename[col] = "ts_code"
        elif col in _NAME_ALIASES:
            rename[col] = "name"

    if rename:
        df = df.rename(columns=rename)

    # 兜底：按位置索引
    if "ts_code" not in df.columns and len(df.columns) >= 1:
        df = df.rename(columns={df.columns[0]: "ts_code"})
    if "name" not in df.columns and len(df.columns) >= 2:
        df = df.rename(columns={df.columns[1]: "name"})

    return df


# ── 延迟加载辅助 ───────────────────────────────────────────────────────────

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


def _get_tdx():
    global _lazy_tdx
    if _lazy_tdx is None:
        from . import tdx_offline
        _lazy_tdx = tdx_offline.tdx_store
    return _lazy_tdx


# ── 单只股票数据接口 ───────────────────────────────────────────────────────

def get_stock_history(code: str, days: int = 60) -> pd.DataFrame:
    """
    获取单只股票历史K线
    优先级：通达信离线 → 本地CSV缓存 → 网络多源降级
    """
    cache = _get_cache()
    manager = _get_manager()
    code6 = str(code).strip().replace("sh", "").replace("sz", "").replace("bj", "")

    # ── 第0层：通达信离线数据 ──
    tdx = _get_tdx()
    tdx_df = tdx.get_kline(code6, days)
    if not tdx_df.empty and len(tdx_df) >= days:
        last_date = tdx_df["date"].iloc[-1]
        try:
            age_days = (datetime.now() - pd.to_datetime(last_date)).days
        except Exception:
            age_days = 99
            logger.debug(f"TDX日期解析失败: {last_date}")
        if age_days <= 3:
            logger.debug(f"TDX命中: {code6}, 最新={last_date}, 距今{age_days}天")
            return tdx_df.reset_index(drop=True)

    # ── 第1层：本地CSV缓存 ──
    cached = cache.get_cached_kline(code6)
    if not cached.empty:
        cached_days = len(cached)
        # 缓存足够且有效，直接返回（跳过网络请求）
        if cached_days >= days and not cache.needs_update(code6, max_age_hours=4):
            return cached.tail(days).reset_index(drop=True)
        # 缓存不够，需要请求网络补全
        
    df = manager.get_kline(code6, days)
    if df.empty:
        if not tdx_df.empty:
            logger.warning(f"网络失败，使用通达信离线数据: {code6}")
            return tdx_df.tail(days).reset_index(drop=True)
        if not cached.empty:
            logger.warning(f"网络失败，使用旧缓存: {code6}")
            return cached.tail(days).reset_index(drop=True)
        return pd.DataFrame()


    full_df = cache.merge_kline_to_cache(code6, df)
    return full_df.tail(days).reset_index(drop=True)


def get_stock_realtime(code: str) -> dict:
    """获取单只股票实时行情"""
    return _get_manager().get_realtime(code)


def get_stock_list(force_refresh: bool = False) -> pd.DataFrame:
    """
    获取全市场股票列表。
    
    Cache validity rules:
    1. If cache exists AND cache contains today's closing data AND market is closed → use cache (skip API)
    2. If force_refresh=True → always try API first
    3. If API fails → degrade to cache with warning
    
    Args:
        force_refresh: True when user clicks "更新数据", False for passive loading
    """
    cache = _get_cache()
    manager = _get_manager()

    cached = cache.get_cached_stock_list()
    
    # 规则1: 被动加载 + 缓存有效 → 直接用缓存（不请求API）
    if not force_refresh and not cached.empty:
        if _is_cache_valid_for_today(cached):
            logger.info(f"股票列表(缓存有效): {len(cached)} 只，跳过网络请求")
            return normalize_stock_columns(cached)
    
    # 规则2: 主动更新 或 缓存无效 → 请求API
    df = manager.get_stock_list()

    # 网络返回完整（>=3000只）才信任并更新缓存
    if not df.empty and len(df) >= 3000:
        df = normalize_stock_columns(df)
        cache.save_stock_list(df)
        logger.info(f"股票列表(网络): {len(df)} 只")
        return df

    # 规则3: 网络不完整或失败，用本地缓存
    if not cached.empty:
        if not df.empty and len(df) < len(cached):
            logger.warning(f"网络股票列表不完整({len(df)}只)，使用本地缓存({len(cached)}只)")
        df = normalize_stock_columns(cached)
        logger.info(f"股票列表(缓存回退): {len(df)} 只")
        return df

    # 两者都失败，返回网络结果（可能为空）
    return normalize_stock_columns(df) if not df.empty else df


def _is_cache_valid_for_today(cached: pd.DataFrame) -> bool:
    """
    判断缓存是否包含今日收盘数据。
    不能只看日期==今天，要看缓存中最新数据是否是收盘后生成的。
    """
    import os
    cache_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "stocks.json")
    if not os.path.exists(cache_file):
        return False
    
    import time
    mtime = os.path.getmtime(cache_file)
    cache_hour = datetime.fromtimestamp(mtime).hour
    
    # 缓存文件修改时间在收盘后（15点后）→ 认为是收盘数据，有效
    # 缓存文件修改时间在开盘期间（9~15点）→ 认为是盘中数据，无效
    if cache_hour >= 15:
        # 且是今天生成的
        cache_date = datetime.fromtimestamp(mtime).date()
        if cache_date == datetime.now().date():
            return True
    return False


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

        return normalize_stock_columns(df).reset_index(drop=True)
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
        self._max_cache_size: int = 4000   # 内存缓存上限，覆盖全市场A股（约3300+只）
        # 批量实时行情临时缓存（预计算阶段使用，避免逐只请求限流）
        self._realtime_batch: Dict[str, dict] = {}
        self._last_update_time: Optional[datetime] = None  # 最后更新时间

    def load(self) -> bool:
        self._loaded = True
        return True

    def get_history(self, code: str, days: int = 60, pure: bool = False) -> pd.DataFrame:
        """
        单只K线（内存 → 本地 → 网络），线程安全
        优先级：内存缓存 > 本地CSV > 网络API
        """
        code6 = str(code).strip()
        if len(code6) > 2 and code6[:2].lower() in ("sh", "sz", "bj"):
            code6 = code6[2:]

        # ── 优化：LRU淘汰防止内存溢出 ──
        with self._lock:
            if len(self._kline_cache) >= self._max_cache_size:
                evict_n = self._max_cache_size // 5
                evict_keys = list(self._kline_cache.keys())[:evict_n]
                for k in evict_keys:
                    del self._kline_cache[k]
                    self._cache_days.pop(k, None)
                logger.info(f"LRU淘汰: 移除{len(evict_keys)}只股票缓存")

        # 先检查内存缓存
        with self._lock:
            if code6 in self._kline_cache:
                cached = self._kline_cache[code6]
                if len(cached) >= days:
                    return cached

        # 内存没有，尝试本地CSV缓存
        cache = _get_cache()
        local_df = cache.get_cached_kline(code6)
        if not local_df.empty and len(local_df) >= days:
            with self._lock:
                self._kline_cache[code6] = local_df
                self._cache_days[code6] = days
            return local_df

        # 本地也没有，从网络获取
        df = get_stock_history(code6, days)

        if not df.empty:
            with self._lock:
                self._kline_cache[code6] = df
                self._cache_days[code6] = days
                self._last_update_time = datetime.now()
        return df

    def prefetch_batch(
        self,
        codes: List[str],
        days: int = 60,
        max_workers: int = 50,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
        force_refresh: bool = False,
        stop_event: Optional[threading.Event] = None,  # ← 新增停止事件参数
    ) -> Dict:
        """
        并发预加载K线到内存缓存，带自动补全机制。

        分3轮执行：
          第1轮：高并发(max_workers)快速下载
          第2轮：低并发(15)补全第1轮失败的
          第3轮：串行补全剩余的（每次间隔1s避免限流）

        Args:
            force_refresh: True=强制从网络重新下载，忽略本地缓存
            stop_event: 停止事件，用于中断下载
        Returns: {"cached": int, "failed": list, "total": int}
        """
        def to6(c: str) -> str:
            c = str(c).strip()
            return c[2:] if len(c) > 2 and c[:2].lower() in ("sh", "sz", "bj") else c

        all_codes = [to6(c) for c in codes]

        # 智能更新：交易时间强制更新，收盘后检查是否是收盘价
        from .fetcher import is_market_open
        in_market = is_market_open()
        today = datetime.now()
        today_str = today.strftime("%Y-%m-%d")
        # 15:00后为收盘价标记时间
        market_close_time = datetime.strptime("15:00", "%H:%M").time()

        def _is_close_price(last_update_str: str) -> bool:
            """检查是否是收盘价（15:00后更新）"""
            if not last_update_str:
                return False
            try:
                dt = datetime.strptime(last_update_str, "%Y-%m-%d %H:%M:%S")
                return dt.time() >= market_close_time
            except:
                return False

        with self._lock:
            need_fetch = []
            for c in all_codes:
                if c not in self._kline_cache:
                    # 不在内存，尝试从本地缓存加载
                    cache = _get_cache()
                    local_df = cache.get_cached_kline(c)
                    meta = cache._load_meta()
                    # 检查缓存是否有效（数据不为空且记录数>0）
                    cache_info = meta.get(c, {})
                    records = cache_info.get("records", 0)
                    if not local_df.empty and records > 0:
                        # 检查缓存时间戳
                        last_update = cache_info.get("last_update", "")
                        last_date = str(local_df["date"].iloc[-1]).split()[0]
                        self._kline_cache[c] = local_df
                        self._cache_days[c] = days
                        if in_market:
                            # 交易时间：强制更新
                            need_fetch.append(c)
                        elif _is_close_price(last_update):
                            # 收盘后且是收盘价：缓存有效
                            continue
                        else:
                            # 非交易时间但不是收盘价：需要更新获取收盘价
                            need_fetch.append(c)
                    else:
                        # 缓存无效（空文件），需要下载
                        need_fetch.append(c)
                    continue
                # 检查内存缓存的最新日期
                cached = self._kline_cache[c]
                if cached.empty:
                    need_fetch.append(c)
                    continue
                last_date = str(cached["date"].iloc[-1]).split()[0]
                # 检查缓存时间戳
                meta = cache._load_meta()
                cache_info = meta.get(c, {})
                records = cache_info.get("records", 0)
                if records <= 0:
                    need_fetch.append(c)
                    continue
                last_update = cache_info.get("last_update", "")
                if in_market:
                    # 交易时间：强制更新到最新
                    if last_date != today_str:
                        need_fetch.append(c)
                elif _is_close_price(last_update):
                    # 收盘后且是收盘价：缓存有效
                    continue
                elif self._cache_days.get(c, 0) < days:
                    # 需要更新获取收盘价
                    need_fetch.append(c)

        if not need_fetch:
            logger.info("预加载K线: 全部命中缓存")
            return {"cached": len(all_codes), "failed": [], "total": len(all_codes)}

        if force_refresh:
            logger.info(f"强制刷新模式: {len(all_codes)}只股票将全部从网络重新下载")
            need_fetch = all_codes
        else:
            logger.info(f"预加载K线: {len(need_fetch)}/{len(all_codes)} 只需网络获取")

        # 并发自适应降级：20 → 10 → 5 → 串行（降低并发避免卡死）
        concurrency_levels = [max_workers, 20, 10, 5]
        failed = []
        for round_idx, cur_workers in enumerate(concurrency_levels):
            if not need_fetch:
                break
            # 检查停止事件
            if stop_event and stop_event.is_set():
                logger.info("prefetch_batch: 收到停止信号，中止下载")
                break
            label = f"第{round_idx+1}轮(并发{cur_workers})"
            logger.info(f"{label}: 尝试 {len(need_fetch)} 只")
            failed = self._fetch_round(
                need_fetch, days, cur_workers, label,
                progress_callback, force_refresh=force_refresh,
                stop_event=stop_event  # ← 传递停止事件
            )
            if not failed:
                break
            need_fetch = failed
            if round_idx + 1 < len(concurrency_levels):
                logger.warning(f"{label} 失败 {len(failed)} 只，降级到并发 {concurrency_levels[round_idx+1]}")
            else:
                logger.warning(f"{label} 失败 {len(failed)} 只，进入串行兜底")

        # 最终串行兜底
        if failed:
            logger.info(f"并发全部失败，启动串行兜底，共 {len(failed)} 只")
            failed = self._fetch_round(
                failed, days, 1, "串行兜底",
                progress_callback, force_refresh=force_refresh,
                stop_event=stop_event  # ← 传递停止事件
            )

        if failed:
            logger.warning(f"最终仍有 {len(failed)} 只无法获取: {failed[:20]}...")

        cache = _get_cache()
        with self._lock:
            mem_count = len(self._kline_cache)
        logger.info(f"K线缓存完成: 内存{mem_count}只 | "
                    f"本地{cache.get_cache_status()['total_stocks']}只 | 失败{len(failed)}只")

        if progress_callback:
            progress_callback("完成", mem_count, len(all_codes))

        return {"cached": mem_count, "failed": failed or [], "total": len(all_codes)}

    def _fetch_round(self, codes: List[str], days: int, workers: int,
                     round_label: str, callback=None, force_refresh: bool = False,
                     stop_event: Optional[threading.Event] = None) -> List[str]:
        """单轮下载，返回失败的代码列表。支持停止事件中断。"""
        done_count = 0
        total = len(codes)
        failed_codes = []
        lock = threading.Lock()

        def _fetch_one(code6: str):
            nonlocal done_count
            df = None
            # 每次重试前都检查停止事件
            if stop_event and stop_event.is_set():
                return code6, None
            try:
                if force_refresh:
                    manager = _get_manager()
                    df = manager.get_kline(code6, days)
                    for i, delay in enumerate((0.5, 1.0, 2.0)):
                        if df is not None and not df.empty:
                            break
                        if stop_event and stop_event.is_set():
                            return code6, None
                        # wait 在 stop_event.set() 时立即返回，替代 time.sleep
                        if stop_event:
                            stop_event.wait(timeout=delay)
                            if stop_event.is_set():
                                return code6, None
                        else:
                            time.sleep(delay)
                        df = manager.get_kline(code6, days)
                    if df is not None and not df.empty:
                        cache = _get_cache()
                        cache.merge_kline_to_cache(code6, df)
                else:
                    df = get_stock_history(code6, days)
                    for i, delay in enumerate((0.5, 1.0, 2.0)):
                        if df is not None and not df.empty:
                            break
                        if stop_event and stop_event.is_set():
                            return code6, None
                        if stop_event:
                            stop_event.wait(timeout=delay)
                            if stop_event.is_set():
                                return code6, None
                        else:
                            time.sleep(delay)
                        df = get_stock_history(code6, days)
            except Exception:
                pass
            finally:
                with lock:
                    done_count += 1
                    cur = done_count
            if callback and done_count % max(50, workers * 2) == 0:
                callback(code6, done_count, total)
            if df is not None and not df.empty:
                return code6, df
            return code6, None

        with ThreadPoolExecutor(max_workers=workers) as pool:
            # 分批提交，每批前检查停止事件
            futures = {}
            for c in codes:
                if stop_event and stop_event.is_set():
                    break
                f = pool.submit(_fetch_one, c)
                futures[f] = c

            # 收集已完成的
            done_futures = set()
            try:
                for future in as_completed(list(futures.keys())):
                    if stop_event and stop_event.is_set():
                        # 停止：取消所有未完成的任务
                        for f in futures:
                            if not f.done():
                                f.cancel()
                        break
                    try:
                        code6, result = future.result()
                        done_futures.add(future)
                        if code6 is None:
                            continue
                        if result is not None and not (hasattr(result, 'empty') and result.empty):
                            with self._lock:
                                self._kline_cache[code6] = result
                                self._cache_days[code6] = days
                        else:
                            failed_codes.append(code6)
                    except Exception:
                        failed_codes.append(futures.get(future, 'unknown'))
            except Exception:
                pass

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
            return normalize_stock_columns(df)
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
            bb_upper, bb_mid, bb_lower = ind.calc_bollinger(close)
            vr = ind.calc_volume_ratio(vol, 5)
            high = df["high"] if "high" in df.columns else close
            low = df["low"] if "low" in df.columns else close
            td = ind.td_sequential_count(close, high=high, low=low)
            sk, sd = ind.calc_skdj(close, high, low)

            result = {
                "kline": df,
                "macd": macd,
                "rsi": rsi,
                "ma": ma,
                "bollinger": {"upper": bb_upper, "mid": bb_mid, "lower": bb_lower},
                "vol_ratio": vr,
                "td_count": td,
                "skdj": (sk, sd),
            }

            with self._lock:
                self._indicator_cache[cache_key] = result

            return result
        except Exception:
            return {}

    def get_cached_codes(self) -> List[str]:
        """
        返回本地缓存中已有的股票代码列表（内存 + 磁盘）。
        用于增量更新场景：只更新已缓存的股票，不依赖股票列表API。
        重启后内存缓存为空，但从磁盘缓存读取代码列表。
        """
        import traceback
        # 内存缓存中的代码
        with self._lock:
            mem_codes = set(self._kline_cache.keys())

        # 磁盘缓存中的代码（调 local_cache 的公开方法，路径最权威）
        disk_codes = set()
        try:
            from .local_cache import get_cached_codes as _disk_scan
            disk_codes = set(_disk_scan())
            logger.info(f"get_cached_codes: 磁盘扫描到 {len(disk_codes)} 个代码")
        except Exception as e:
            logger.error(f"get_cached_codes 调用 local_cache 失败: {e}\n{traceback.format_exc()}")

        all_codes = mem_codes | disk_codes
        logger.info(f"get_cached_codes: 内存={len(mem_codes)}, 磁盘={len(disk_codes)}, 合计={len(all_codes)}")
        return list(all_codes)

    def get_cache_status(self) -> dict:
        cache = _get_cache()
        local = cache.get_cache_status()
        with self._lock:
            mem_count = len(self._kline_cache)
            last_update = self._last_update_time
        return {
            "memory_cached": mem_count,
            "cached_count": local.get("total_stocks", 0),
            "local_records": local.get("total_records", 0),
            "cache_dir": local.get("cache_dir", ""),
            "last_update": last_update.strftime("%Y-%m-%d %H:%M") if last_update else None,
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
        优先使用批量实时行情缓存（预计算阶段），没有再单只查询。
        """
        if df is None or df.empty or "date" not in df.columns:
            return df if df is not None else pd.DataFrame()
        today = datetime.now().strftime("%Y-%m-%d")
        # 兼容 date 列是 Timestamp 或字符串
        last_date_val = df["date"].iloc[-1]
        if hasattr(last_date_val, "strftime"):
            last_date = last_date_val.strftime("%Y-%m-%d")
        else:
            last_date = str(last_date_val).split()[0]
        if last_date == today:
            return df
        # 优先使用批量实时行情缓存，避免逐只请求限流
        quote = self._realtime_batch.get(code6)
        if quote is None:
            quote = self.get_realtime(code6)
        if not quote:
            return df
        price = quote.get("最新价", 0)
        if price <= 0:
            return df
        vol = quote.get("成交量", 0) * 100   # 实时行情成交量单位是"手"，转"股"
        pct = quote.get("涨跌幅", 0)
        if vol <= 0 and abs(pct) < 0.01:
            return df
        open_price = quote.get("今开", price)
        high_price = quote.get("最高价", max(open_price, price))
        low_price = quote.get("最低价", min(open_price, price))
        est_vol = self._estimate_full_day_volume(vol)
        # date 用 Timestamp 保持类型一致，避免 concat 后变成 object
        row = {"date": pd.Timestamp(today), "open": open_price, "close": price,
               "high": high_price, "low": low_price, "vol": est_vol}
        for col in df.columns:
            if col not in row:
                if col in ("pct_chg", "daily_chg"):
                    row[col] = pct
                else:
                    row[col] = 0.0
        new_row = pd.DataFrame([row])
        # 保持 date 列类型一致
        if df["date"].dtype != "object":
            new_row["date"] = pd.to_datetime(new_row["date"])
        df = pd.concat([df, new_row], ignore_index=True)
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


# ── 智能更新判断 ─────────────────────────────────────────────────────────

def is_market_open() -> bool:
    """判断当前是否在A股交易时间"""
    now = datetime.now()
    weekday = now.weekday()

    # 周六日休市
    if weekday >= 5:
        return False

    current_time = now.time()
    morning_start = datetime.strptime("09:30", "%H:%M").time()
    morning_end = datetime.strptime("11:30", "%H:%M").time()
    afternoon_start = datetime.strptime("13:00", "%H:%M").time()
    afternoon_end = datetime.strptime("15:00", "%H:%M").time()

    if morning_start <= current_time <= morning_end:
        return True
    if afternoon_start <= current_time <= afternoon_end:
        return True
    return False


def get_last_trading_date() -> Optional[datetime]:
    """获取最近一个有A股交易的日期"""
    now = datetime.now()
    today = now.date()

    # 如果现在在交易时间，返回今天
    if is_market_open():
        return now

    # 否则找上一个交易日
    for days_ago in range(1, 8):
        check_date = now.date() - timedelta(days=days_ago)
        weekday = check_date.weekday()
        if weekday < 5:  # 周一到周五
            return datetime.combine(check_date, datetime.min.time())
    return None


def check_data_freshness(code: str, cache_days: Dict[str, Any], max_age_minutes: int = 5) -> Dict:
    """
    检查缓存数据是否需要更新
    返回: {"need_update": bool, "reason": str, "last_update": datetime}
    """
    code6 = str(code).strip()
    if len(code6) > 2 and code6[:2].lower() in ("sh", "sz", "bj"):
        code6 = code6[2:]

    # 检查缓存时间戳
    cache_time = cache_days.get(code6)
    if not cache_time:
        return {"need_update": True, "reason": "无缓存", "last_update": None}

    # 如果在交易时间，需要更新
    if is_market_open():
        return {"need_update": True, "reason": "交易时间", "last_update": cache_time}

    # 休市后，检查是否是最近交易日的收盘数据
    last_trading = get_last_trading_date()
    if not last_trading:
        return {"need_update": True, "reason": "无法确定交易日", "last_update": cache_time}

    # 缓存时间是否来自最近交易日
    if cache_time.date() == last_trading.date():
        # 同一天的数据已经是收盘价，不需要更新
        return {"need_update": False, "reason": f"数据为{last_trading.strftime('%Y-%m-%d')}收盘价，无需更新", "last_update": cache_time}
    else:
        # 不是最新交易日，需要更新
        return {"need_update": True, "reason": f"缓存为{cache_time.strftime('%Y-%m-%d')}，需要更新到最新", "last_update": cache_time}
