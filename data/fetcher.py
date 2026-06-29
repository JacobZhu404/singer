"""
数据获取层：本地缓存 + 多数据源（新浪/腾讯/东财），无需 Token
"""

import time
import logging
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Callable, Tuple, Any
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED

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
        try:
            from . import local_cache
            _lazy_cache = local_cache
        except Exception as e:
            raise RuntimeError(f"local_cache 导入失败: {e}") from e
    if _lazy_cache is None:
        raise RuntimeError("_lazy_cache 仍未初始化")
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
    获取单只股票历史K线（双数据源设计）

    优先级：
    1. 通达信离线（最新）→ 2. 本地缓存（有效）→ 3. 网络（实时/收盘）
    """
    from . import local_cache
    from .data_layer import data_fetcher

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

    # ── 第1层：使用新的数据层（智能判断）──
    df = data_fetcher.get_kline(code6, days)
    if not df.empty:
        return df.reset_index(drop=True)

    # 兜底：通达信或本地缓存
    if not tdx_df.empty:
        logger.warning(f"网络失败，使用通达信离线数据: {code6}")
        return tdx_df.tail(days).reset_index(drop=True)

    cache = _get_cache()
    cached = cache.get_cached_kline(code6)
    if not cached.empty:
        logger.warning(f"网络失败，使用旧缓存: {code6}")
        return cached.tail(days).reset_index(drop=True)

    return pd.DataFrame()


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


def _kline_is_fresh(df: "pd.DataFrame") -> bool:
    """实时筛选用：最新 K 线 bar 距今是否在 SCREEN_MAX_STALE_DAYS 内。

    无日期列/无法解析时返回 True（不拦截，交给后续长度/质量校验），
    只在能确认数据陈旧（停牌/退市）时返回 False。
    """
    if df is None or len(df) == 0:
        return True
    date_col = "date" if "date" in df.columns else ("trade_date" if "trade_date" in df.columns else None)
    if date_col is None:
        return True
    try:
        last = pd.to_datetime(df[date_col].iloc[-1])
        if pd.isna(last):
            return True
    except Exception:
        return True
    try:
        from ..core.constants import SCREEN_MAX_STALE_DAYS as _MAX_STALE
    except Exception:
        _MAX_STALE = 15
    return (pd.Timestamp.now().normalize() - last.normalize()).days <= _MAX_STALE


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
        # get_indicators 命中/未命中计数，用于评估 precalc 实际复用率
        self._indicator_stats: Dict[str, Any] = {
            "hit": 0, "miss": 0,
            "hit_by_days": {}, "miss_by_days": {},
        }
        self._lock = threading.Lock()
        self._include_realtime: bool = True
        self._max_cache_size: int = 6500   # 内存缓存上限，覆盖全市场A股（约5500+只）
        # 批量实时行情临时缓存（预计算阶段使用，避免逐只请求限流）
        self._realtime_batch: Dict[str, dict] = {}
        self._last_update_time: Optional[datetime] = None  # 最后更新时间
        # 基本面快照：{code6: {pe, pb, mktcap_wan, nmc_wan, turnover}}，PE/PB 隔夜变化极小
        self._fundamentals: Dict[str, Dict[str, Any]] = {}

    def load(self) -> bool:
        self._loaded = True
        return True

    def ensure_fundamentals(
        self,
        force_refresh: bool = False,
        progress_callback: Optional[Callable] = None,
    ) -> Dict[str, dict]:
        """加载或刷新全市场 PE/PB 快照，结果挂在 self._fundamentals 上。

        失败时不抛异常（基本面是辅助维度，缺失只导致风险标签/惩罚不生效）。
        """
        if self._fundamentals and not force_refresh:
            return self._fundamentals
        try:
            from .fundamentals import load_or_fetch_fundamentals
            data = load_or_fetch_fundamentals(
                force_refresh=force_refresh,
                progress_callback=progress_callback,
            )
            self._fundamentals = data or {}
        except Exception as e:
            logger.warning(f"基本面加载失败（继续运行，无 PE/PB 维度）: {e}")
            self._fundamentals = {}
        return self._fundamentals

    def get_fundamental(self, code: str) -> Dict[str, Any]:
        """返回单只股票的基本面字典，未命中时返回空 dict"""
        code6 = str(code).strip()
        if len(code6) > 2 and code6[:2].lower() in ("sh", "sz", "bj"):
            code6 = code6[2:]
        return self._fundamentals.get(code6, {})

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

        # 使用新的数据层进行智能判断
        from .data_layer import check_update_need, DataUpdateDecision
        # 预先获取cache实例，避免循环中重复调用
        cache = _get_cache()
        # 一次性加载 meta（763KB JSON，循环内反复加载会拖慢初始化）
        meta_snapshot = cache._load_meta()

        with self._lock:
            need_fetch = []
            for c in all_codes:
                if c not in self._kline_cache:
                    # 不在内存，尝试从本地缓存加载
                    local_df = cache.get_cached_kline(c)
                    cache_info = meta_snapshot.get(c, {})
                    records = cache_info.get("records", 0)

                    if not local_df.empty and records > 0:
                        self._kline_cache[c] = local_df
                        self._cache_days[c] = days

                        # 使用智能判断
                        last_update = cache_info.get("last_update", "")
                        local_last_date = str(local_df["date"].iloc[-1]).split()[0]
                        decision = check_update_need(c, local_last_date, last_update)

                        if decision.update_type == DataUpdateDecision.NO_UPDATE:
                            continue
                        else:
                            need_fetch.append(c)
                    else:
                        # 缓存无效，需要下载
                        need_fetch.append(c)
                    continue

                # 检查内存缓存
                cached = self._kline_cache[c]
                if cached.empty:
                    need_fetch.append(c)
                    continue

                cache_info = meta_snapshot.get(c, {})
                records = cache_info.get("records", 0)
                if records <= 0:
                    need_fetch.append(c)
                    continue

                # 使用智能判断
                last_update = cache_info.get("last_update", "")
                last_date = str(cached["date"].iloc[-1]).split()[0]
                decision = check_update_need(c, last_date, last_update)

                if decision.update_type == DataUpdateDecision.NO_UPDATE:
                    continue
                else:
                    need_fetch.append(c)

        if not need_fetch:
            logger.info("预加载K线: 全部命中缓存")
            return {"cached": len(all_codes), "failed": [], "total": len(all_codes)}

        if force_refresh:
            logger.info(f"强制刷新模式: {len(all_codes)}只股票将全部从网络重新下载")
            need_fetch = all_codes
        else:
            logger.info(f"预加载K线: {len(need_fetch)}/{len(all_codes)} 只需网络获取")

        # 立刻把进度分母从"全集 total"切到"真正需联网的 need_fetch"，
        # 否则进度条会一直显示 0/总数(如 0/3480)，看上去像在下全部。
        if progress_callback:
            try:
                progress_callback("", 0, len(need_fetch))
            except Exception:
                logger.debug("progress_callback raised", exc_info=True)

        # ─── §C1 快速路径 ───────────────────────────────────────────────
        # 分类：REALTIME（只缺今日一行） vs CLOSE/其它（需要拉历史 bar）
        # REALTIME 走腾讯批量（90 只/请求），把今日 quote merge 到本地历史；
        # CLOSE 才进入下面的 _fetch_round 单只多源链路。
        if not force_refresh:
            need_fetch = self._batch_realtime_path(
                need_fetch, days, meta_snapshot,
                progress_callback=progress_callback,
                stop_event=stop_event,
            )
            if not need_fetch:
                logger.info("批量快速路径完成全部 REALTIME，无需进入单只链路")

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
            attempted = len(need_fetch)
            logger.info(f"{label}: 尝试 {attempted} 只")
            failed = self._fetch_round(
                need_fetch, days, cur_workers, label,
                progress_callback, force_refresh=force_refresh,
                stop_event=stop_event  # ← 传递停止事件
            )
            if not failed:
                break
            # 数据源整体不可用短路：第 1 轮在样本足够(>=20)的情况下失败率 >= 95%，
            # 通常是 baostock/东财等同时连不上，再降级重试 2/3/4 轮也是空转，直接停。
            if round_idx == 0 and attempted >= 20 and len(failed) / attempted >= 0.95:
                logger.warning(
                    f"{label} 失败 {len(failed)}/{attempted} (>=95%)，判定数据源整体不可用，"
                    f"放弃后续降级重试"
                )
                try:
                    from ..core.observability import obs
                    obs.warn("data.fetch", "sources_unavailable",
                             f"{label} 失败率 {len(failed)/attempted:.0%}，数据源整体不可用，已中止重试",
                             context={"attempted": attempted, "failed_count": len(failed),
                                      "samples": failed[:5]})
                except Exception:
                    logger.debug("obs.warn sources_unavailable failed", exc_info=True)
                break
            need_fetch = failed
            if round_idx + 1 < len(concurrency_levels):
                logger.warning(f"{label} 失败 {len(failed)} 只，降级到并发 {concurrency_levels[round_idx+1]}")
                try:
                    from ..core.observability import obs
                    obs.warn("data.fetch", "concurrency_downgrade",
                             f"{label} 失败 {len(failed)}，降级到并发 {concurrency_levels[round_idx+1]}",
                             context={"failed_count": len(failed),
                                      "next_workers": concurrency_levels[round_idx+1],
                                      "samples": failed[:5]})
                except Exception:
                    logger.debug("obs.warn concurrency_downgrade failed", exc_info=True)
            else:
                logger.warning(f"{label} 失败 {len(failed)} 只，进入串行兜底")
                try:
                    from ..core.observability import obs
                    obs.warn("data.fetch", "serial_fallback",
                             f"{label} 失败 {len(failed)}，进入串行兜底",
                             context={"failed_count": len(failed),
                                      "samples": failed[:5]})
                except Exception:
                    logger.debug("obs.warn serial_fallback failed", exc_info=True)

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
            try:
                from ..core.observability import obs
                obs.error("data.fetch", "final_failure",
                          f"K线最终失败 {len(failed)} 只",
                          context={"failed_count": len(failed),
                                   "samples": failed[:20],
                                   "action": "标记失败，使用本地旧缓存兜底"})
            except Exception:
                logger.debug("obs.error final_failure failed", exc_info=True)

        cache = _get_cache()
        with self._lock:
            mem_count = len(self._kline_cache)
        logger.info(f"K线缓存完成: 内存{mem_count}只 | "
                    f"本地{cache.get_cache_status()['total_stocks']}只 | 失败{len(failed)}只")

        if progress_callback:
            progress_callback("完成", mem_count, len(all_codes))

        return {"cached": mem_count, "failed": failed or [], "total": len(all_codes)}

    def _batch_realtime_path(
        self,
        codes: List[str],
        days: int,
        meta_snapshot: dict,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> List[str]:
        """
        §C1 快速路径：把所有"只缺今日一行"的 code 用腾讯批量接口（90/HTTP）一次性补完。

        Returns: 未走完快速路径、需要 _fetch_round 处理的 code 列表（CLOSE 类）。
        """
        from .data_layer import check_update_need, DataUpdateDecision
        from .realtime_merge import merge_realtime_into_history
        from .tencent_batch import get_realtime_fast
        from . import local_cache

        realtime_codes: List[str] = []
        residual: List[str] = []  # 非 REALTIME 决策的 code（CLOSE 等）

        for c in codes:
            cache_info = meta_snapshot.get(c, {})
            local_last_date = cache_info.get("end_date", "")
            meta_last_update = cache_info.get("last_update", "")
            decision = check_update_need(c, local_last_date, meta_last_update)
            if decision.update_type == DataUpdateDecision.REALTIME:
                realtime_codes.append(c)
            else:
                residual.append(c)

        if not realtime_codes:
            return residual

        if stop_event and stop_event.is_set():
            return codes  # 已停止，全部退回，不做事

        logger.info(f"快速路径: {len(realtime_codes)} 只走批量实时，{len(residual)} 只走 CLOSE 链路")
        if progress_callback:
            progress_callback("批量实时", 0, len(realtime_codes))

        # 一次性批量补今日报价（内部按 90/HTTP × 5 并发分批）
        try:
            from ..core.observability import obs
            with obs.timer("data.fetch", "batch_realtime",
                           context={"codes": len(realtime_codes)}):
                quote_map = get_realtime_fast(realtime_codes, max_workers=5)
        except Exception as e:
            logger.warning(f"批量实时失败，退回单只链路: {e}")
            try:
                from ..core.observability import obs
                obs.error("data.fetch", "batch_realtime_failed",
                          f"批量实时彻底失败: {e}",
                          context={"codes": len(realtime_codes),
                                   "action": "退回 _fetch_round 单只链路"},
                          exc=e)
            except Exception:
                logger.debug("obs.error batch_realtime_failed failed", exc_info=True)
            return codes  # 全部退回单只链路兜底

        if stop_event and stop_event.is_set():
            return residual + [c for c in realtime_codes if c not in self._kline_cache]

        # 把 quote merge 到本地历史，写入内存缓存
        merged_count = 0
        no_quote: List[str] = []
        for idx, c in enumerate(realtime_codes, 1):
            if stop_event and stop_event.is_set():
                break
            quote = quote_map.get(c)
            if not quote:
                no_quote.append(c)
                continue
            history = local_cache.get_cached_kline(c)
            if history is None or history.empty:
                # 无本地历史 → 这只必须走 CLOSE 单只链路去拉历史 bar
                no_quote.append(c)
                continue
            merged = merge_realtime_into_history(history, quote)
            if merged is None or merged.empty:
                no_quote.append(c)
                continue
            with self._lock:
                self._kline_cache[c] = merged
                self._cache_days[c] = days
            merged_count += 1
            if progress_callback and (idx % 200 == 0 or idx == len(realtime_codes)):
                progress_callback("批量实时", idx, len(realtime_codes))

        logger.info(f"批量实时合并完成: 成功 {merged_count}/{len(realtime_codes)}, 缺报价或无历史 {len(no_quote)} 只退回单只链路")
        return residual + no_quote

    def _fetch_round(self, codes: List[str], days: int, workers: int,
                     round_label: str, callback=None, force_refresh: bool = False,
                     stop_event: Optional[threading.Event] = None) -> List[str]:
        """单轮下载，返回失败的代码列表。支持停止事件中断。"""
        done_count = 0
        total = len(codes)
        failed_codes = []
        lock = threading.Lock()

        # 一次性加载 meta，传给 data_fetcher 复用，避免每只股票重新读 763KB JSON
        cache = _get_cache()
        meta_snapshot = cache._load_meta()

        def _fetch_one(code6: str):
            nonlocal done_count
            df = None
            # 每次重试前都检查停止事件
            if stop_event and stop_event.is_set():
                return code6, None
            try:
                # 使用新的数据层（智能判断）
                from .data_layer import data_fetcher
                df = data_fetcher.get_kline(code6, days, meta=meta_snapshot)
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
                    df = data_fetcher.get_kline(code6, days, meta=meta_snapshot)
            except Exception as e:
                logger.debug(f"fetch_one {code6} failed: {e}")
                try:
                    from ..core.observability import obs
                    obs.error("data.fetch", "fetch_one",
                              f"获取K线失败: {e}",
                              context={"code": code6, "round": round_label,
                                       "action": "进入下一轮重试"},
                              exc=e)
                except Exception:
                    logger.debug("obs.error fetch_one failed", exc_info=True)
            finally:
                with lock:
                    done_count += 1
                    cur = done_count
            if callback and (cur == total or cur % 10 == 0):
                callback(code6, cur, total)
            if df is not None and not df.empty:
                return code6, df
            return code6, None

        # 注意：不用 `with ThreadPoolExecutor() as pool:`，因为它的 __exit__
        # 会调 shutdown(wait=True)，等所有正在跑的 HTTP 请求自然结束。
        # 我们需要"停止时立刻撒手"的语义 —— 手工管理 + 手动取消未启动 future + shutdown(wait=False)。
        # （cancel_futures 参数是 Python 3.9+，本项目跑在 3.7，故手动 future.cancel()）
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            # 分批提交，每批前检查停止事件
            futures = {}
            for c in codes:
                if stop_event and stop_event.is_set():
                    break
                f = pool.submit(_fetch_one, c)
                futures[f] = c

            stopped = False
            # 数据源 socket 可能无超时（如 baostock），单只卡死会让整轮 future 永不完成。
            # 用 wait(FIRST_COMPLETED) 轮询替代 as_completed：①每 2s 检查停止事件，
            # ②超过 STALL_TIMEOUT 无任何 future 完成 → 判定卡死，撒手剩余标记失败。
            STALL_TIMEOUT = 30.0
            try:
                pending = set(futures.keys())
                last_progress = time.monotonic()
                while pending:
                    if stop_event and stop_event.is_set():
                        stopped = True
                        break
                    done_set, pending = wait(pending, timeout=2.0, return_when=FIRST_COMPLETED)
                    if done_set:
                        last_progress = time.monotonic()
                    elif time.monotonic() - last_progress > STALL_TIMEOUT:
                        stalled = [futures.get(f, 'unknown') for f in pending]
                        failed_codes.extend(stalled)
                        logger.warning(f"{round_label}: {len(stalled)} 个 future 卡死 "
                                       f"{STALL_TIMEOUT:.0f}s 无响应，撒手标记失败（数据源 socket 无超时）")
                        try:
                            from ..core.observability import obs
                            obs.warn("data.fetch", "round_stalled",
                                     f"{round_label} {len(stalled)} 只卡死 {STALL_TIMEOUT:.0f}s，撒手标记失败",
                                     context={"stalled_count": len(stalled),
                                              "samples": stalled[:10], "round": round_label})
                        except Exception:
                            logger.debug("obs.warn round_stalled failed", exc_info=True)
                        break
                    for future in done_set:
                        try:
                            code6, result = future.result()
                            if code6 is None:
                                continue
                            if result is not None and not (hasattr(result, 'empty') and result.empty):
                                with self._lock:
                                    self._kline_cache[code6] = result
                                    self._cache_days[code6] = days
                            else:
                                failed_codes.append(code6)
                        except Exception as e:
                            code = futures.get(future, 'unknown')
                            failed_codes.append(code)
                            try:
                                from ..core.observability import obs
                                obs.error("data.fetch", "future_result",
                                          f"future 收集异常: {e}",
                                          context={"code": code, "round": round_label,
                                                   "action": "标记为失败"},
                                          exc=e)
                            except Exception:
                                logger.debug("obs.error future_result failed", exc_info=True)
            except Exception as e:
                try:
                    from ..core.observability import obs
                    obs.error("data.fetch", "as_completed_loop",
                              f"as_completed 循环异常: {e}",
                              context={"round": round_label, "action": "退出本轮"},
                              exc=e)
                except Exception:
                    logger.debug("obs.error as_completed_loop failed", exc_info=True)

            if stopped:
                logger.info(f"{round_label}: 收到停止信号，撒手未完成的 future（不等 HTTP 回包）")
                try:
                    from ..core.observability import obs
                    pending = sum(1 for f in futures if not f.done())
                    obs.warn("data.fetch", "round_stopped",
                             f"{round_label} 停止，{pending} 个 future 在后台自然结束",
                             context={"pending": pending, "round": round_label})
                except Exception:
                    logger.debug("obs.warn round_stopped failed", exc_info=True)
        finally:
            # 手动取消尚未启动的 future（等价于 3.9+ 的 cancel_futures=True）；
            # wait=False 不等已启动的 HTTP 回包——已启动线程会在 HTTP timeout（≤10s）后自然死亡，
            # 不阻塞当前调用栈。
            for f in futures:
                if not f.done():
                    f.cancel()
            pool.shutdown(wait=False)

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
                self._indicator_stats["hit"] += 1
                self._indicator_stats["hit_by_days"][days] = self._indicator_stats["hit_by_days"].get(days, 0) + 1
                return self._indicator_cache[cache_key]
            self._indicator_stats["miss"] += 1
            self._indicator_stats["miss_by_days"][days] = self._indicator_stats["miss_by_days"].get(days, 0) + 1

        # 延迟导入避免循环依赖
        from ..utils.indicators import compute_indicator_bundle

        df = self.get_history(code, days, pure=pure)
        # ── 新鲜度护栏：剔除停牌/退市标的 ──
        # 退市股（如 000024 招商地产，K 线冻结在 2015-12-07）历史数据仍可取到，
        # 若不拦截，策略会把"最后一根 bar"当成今日、用多年前的形态把它选出来。
        # 仅作用于实时盘；回测的 PointInTimeScanner 是独立类，不走这里。
        if not _kline_is_fresh(df):
            return {}
        result = compute_indicator_bundle(df)
        if not result:
            return {}

        with self._lock:
            self._indicator_cache[cache_key] = result
        return result

    def reset_indicator_stats(self) -> None:
        """清零 get_indicators 的命中/未命中计数（precalc 前调用）。"""
        with self._lock:
            self._indicator_stats = {
                "hit": 0, "miss": 0,
                "hit_by_days": {}, "miss_by_days": {},
            }

    def get_indicator_stats(self) -> Dict[str, Any]:
        """返回 get_indicators 调用统计，用于评估 precalc 复用率。"""
        with self._lock:
            return {
                "hit": self._indicator_stats["hit"],
                "miss": self._indicator_stats["miss"],
                "hit_by_days": dict(self._indicator_stats["hit_by_days"]),
                "miss_by_days": dict(self._indicator_stats["miss_by_days"]),
                "cache_size": len(self._indicator_cache),
            }

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
                except Exception as e:
                    code = futures.get(future, 'unknown')
                    try:
                        from ..core.observability import obs
                        obs.error("data.evaluate", "evaluate_batch",
                                  f"评估异常: {e}",
                                  context={"code": code, "action": "跳过该股"},
                                  exc=e)
                    except Exception:
                        logger.debug("obs.error evaluate_batch failed", exc_info=True)
        return results

    def _merge_today_realtime(self, df: pd.DataFrame, code6: str) -> pd.DataFrame:
        """
        将实时行情合成到 K线末尾，让策略基于当天盘中数据计算。
        优先用批量实时缓存（_realtime_batch），缺则单只查询。
        合并细节统一走 data/realtime_merge.merge_realtime_into_history。
        """
        from .realtime_merge import merge_realtime_into_history

        quote = self._realtime_batch.get(code6)
        if quote is None:
            quote = self.get_realtime(code6)
        # data_sources 返回的 quote 用中文 keys，成交量单位是"手"
        return merge_realtime_into_history(df, quote, volume_unit_is_lots=True)


# 全局扫描器（所有策略共享）
market_scanner = MarketScanner()


# ── 智能更新判断 ─────────────────────────────────────────────────────────

# 交易时间/交易日判断的唯一来源
from .market_calendar import is_market_open  # noqa: F401  re-export
