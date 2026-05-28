"""
核心引擎：策略调度 + 胜率计算 + 综合推荐
"""

import logging
import threading
from typing import List, Optional, Dict, Callable

import pandas as pd

from ..strategies.base import ScreenResult, StockSignal
from ..strategies.registry import get_strategy, STRATEGY_REGISTRY
from ..data.fetcher import get_stock_list, market_scanner, get_latest_trade_date
from ..utils.indicators import calc_risk_flags
from ..utils.market_trend import get_market_trend, get_market_trend_strength
from ..utils.sell_signals import detect_sell_signals, assess_buy_risk

from datetime import datetime

logger = logging.getLogger(__name__)

# ── 模块级常量 ───────────────────────────────────────────────────────────

_EMPTY_POSITION_RESULT = {
    "ts_code": "",
    "name": "",
    "latest_price": None,
    "pct_chg": 0.0,
    "volume_ratio": None,
    "trade_date": "",
    "n_strategies": 0,
    "composite_score": 0.0,
    "weighted_score": 0.0,
    "total_score": 0,
    "strategies_hit": [],
    "all_signals": [],
    "strategy_rank_sum": 0,
    "strategy_count": 0,
    "risk_tag": "unknown",
    "risk_reasons": [],
    "risk_score": 0,
    "risk_flags": [],
    "sell_signals": [],
    "sell_score": 0,
    "stop_loss_price": None,
    "take_profit_price": None,
    "sell_risk_level": "unknown",
    "buy_risk": "unknown",
    "buy_risk_reasons": [],
    "avg_rank_pct": 1.0,
}


class _ScoreWeights:
    """综合评分权重常量"""
    WEIGHTED = 0.5
    RANK = 20.0
    CONSENSUS = 5.0
    RISK_FACTOR = 20.0
    MARKET_STRENGTH = 3.0


class _Thresholds:
    """门槛常量"""
    MIN_SINGLE_SCORE = 20
    MIN_WEIGHTED_SCORE = 30
    RANK_BONUS_MAX = 15.0
    BEAR_FACTOR = 1.3
    BULL_FACTOR = 0.9
    RISK_SCAN_MIN_BARS = 15
    SELL_SCAN_MIN_BARS = 20
    BUY_RISK_MIN_BARS = 20


class ScreenEngine:
    """
    选股引擎：
    - 单策略执行（策略内部已并行）
    - 多策略串行调度，结果合并：同一只股票被多策略命中则评分叠加
    - 统一胜率输出
    """

    def __init__(self, market: str = "主板", top_n: int = 20):
        self.market = market
        self.top_n = top_n
        self._stock_list: Optional[pd.DataFrame] = None
        self._stop_event: Optional[threading.Event] = None
        # 进度状态（供外部轮询）
        self._progress: Dict = {
            "phase": "idle",
            "current": "",
            "current_index": 0,
            "total": 0,
            "pct": 0,
            "strategies": {},
        }
        self._progress_lock = threading.Lock()

    def _stop_requested(self) -> bool:
        return self._stop_event is not None and self._stop_event.is_set()

    def _set_progress(self, phase: str, current: str = "", current_index: int = 0,
                      total: int = 0, pct: int = -1):
        with self._progress_lock:
            if pct < 0:
                pct = int(current_index / total * 100) if total > 0 else 0
            self._progress.update({
                "phase": phase,
                "current": current,
                "current_index": current_index,
                "total": total,
                "pct": pct,
            })
        # 自动同步到外部回调（如果设置了 _progress_cb）
        cb = getattr(self, '_progress_cb', None)
        if cb:
            try:
                cb(phase, current, current_index, total)
            except Exception:
                pass

    def _set_strategy_progress(self, name: str, status: str, pct: int = 0, hits: int = 0,
                                phase: str = "", scanned: int = 0, total_stocks: int = 0):
        """更新单个策略的进度状态"""
        with self._progress_lock:
            self._progress["strategies"][name] = {
                "status": status,
                "phase": phase or status,
                "pct": pct,
                "hits": hits,
                "scanned": scanned,
                "total_stocks": total_stocks,
            }

    def _calc_strategy_pct(self, scanned: int, total: int) -> int:
        """计算策略内部进度百分比"""
        if total <= 0:
            return 0
        return min(int(scanned / total * 100), 99)

    def get_progress(self) -> Dict:
        with self._progress_lock:
            return dict(self._progress)

    def _filter_by_market(self, codes: List[str]) -> List[str]:
        """
        根据 self.market 过滤股票代码列表。
        兼容两种代码格式：
          - 带前缀: sh600000, sz000001, bj920339
          - 纯数字:  600000, 000001, 920339
        市场分类规则（基于代码前缀）：
          - 沪市主板：60xxxx
          - 深市主板：000xxx / 001xxx / 002xxx / 003xxx
          - 创业板：  300xxx / 301xxx / 302xxx
          - 科创板：  688xxx / 689xxx
          - 北交所：  8xxxxx / 4xxxxx / 9xxxxx
        """
        market = self.market

        def _pure(c: str) -> str:
            """去掉 sh/sz/bj 前缀，返回纯数字代码"""
            c = str(c).strip()
            for p in ("sh", "sz", "bj"):
                if c.startswith(p):
                    return c[len(p):]
            return c

        def _is_主板(c):
            return (_pure(c).startswith("60") or _pure(c).startswith("000")
                    or _pure(c).startswith("001") or _pure(c).startswith("002")
                    or _pure(c).startswith("003"))
        def _is_创业板(c):
            return _pure(c).startswith("300") or _pure(c).startswith("301") or _pure(c).startswith("302")
        def _is_科创板(c):
            return _pure(c).startswith("688") or _pure(c).startswith("689")

        if market == "主板":
            return [c for c in codes if _is_主板(c)]
        elif market == "创业板":
            return [c for c in codes if _is_创业板(c)]
        elif market == "科创板":
            return [c for c in codes if _is_科创板(c)]
        elif market == "主板+创业板":
            return [c for c in codes if _is_主板(c) or _is_创业板(c)]
        elif market == "主板+科创板":
            return [c for c in codes if _is_主板(c) or _is_科创板(c)]
        else:
            # "全部市场" 或其他，不过滤
            return codes

    def _load_stock_list(self) -> pd.DataFrame:
        if self._stock_list is None:
            logger.info("加载股票列表")
            self._stock_list = get_stock_list()
        return self._stock_list

    @staticmethod
    def _get_code_name_cols(stock_list: pd.DataFrame) -> tuple:
        """提取股票列表的代码列和名称列"""
        code_col = "代码" if "代码" in stock_list.columns else "ts_code"
        name_col = "名称" if "名称" in stock_list.columns else "name"
        return code_col, name_col

    def _precalc(self, stock_list: pd.DataFrame,
                 progress_callback: Optional[Callable[[str, str, int, int], None]] = None):
        """预计算指标并缓存，避免各策略重复计算"""
        from ..utils.precalc import precalc_indicators
        code_col, _ = self._get_code_name_cols(stock_list)
        if stock_list.empty or code_col not in stock_list.columns:
            return
        codes = stock_list[code_col].astype(str).tolist()
        if not codes:
            return

        def _on_precalc_progress(done: int, total: int, code: str):
            if progress_callback:
                progress_callback("precalc", f"预计算指标 {done}/{total}...", done, total)

        precalc_indicators(codes, market_scanner, days=120, progress_callback=_on_precalc_progress)

    def run_single(self, strategy_name: str) -> ScreenResult:
        """执行单个策略"""
        stock_list = self._load_stock_list()
        if self.market != "全部市场":
            code_col, _ = self._get_code_name_cols(stock_list)
            codes = stock_list[code_col].astype(str).tolist()
            filtered = self._filter_by_market(codes)
            stock_list = stock_list[stock_list[code_col].astype(str).isin(filtered)]
        strategy = get_strategy(strategy_name, top_n=self.top_n)
        market_scanner.load()
        logger.info(f"执行策略: {strategy_name}")
        result = strategy.screen(stock_list, scanner=market_scanner)
        return result

    def download_data(
        self,
        force_refresh: bool = False,
        progress_callback: Optional[Callable[[str, str, int, int], None]] = None,
    ) -> dict:
        """
        阶段1：下载/更新K线数据到缓存。

        两种路径：
          增量更新（force_refresh=False 且本地已有缓存）
            → 直接从内存缓存读取已有股票代码，不调股票列表API
          全量更新（force_refresh=True 或缓存为空）
            → 获取股票列表，更新全市场

        拆分子阶段（通过 progress_callback 的 phase 参数）：
          prefetch_init   — 确定股票代码来源（缓存 or 股票列表API）
          prefetch_tdx    — 检测通达信离线数据
          prefetch_fetch  — 网络下载/更新K线
          prefetch_merge  — 批量合并今日实时行情
          prefetch_done   — 下载完成

        Args:
            force_refresh: True=强制全量更新（忽略缓存，重新获取股票列表）
                              False=增量更新（优先用本地缓存代码）
            progress_callback: 进度回调 (phase, message, current, total)
        Returns:
            {"status": "ok", "cached_count": int, "downloaded": int, "failed_count": int}
        """
        # 设置进度回调，_set_progress 内部会自动同步
        self._progress_cb = progress_callback

        # ── 子阶段1：确定股票代码来源 ──
        logger.info(f"download_data 被调用: force_refresh={force_refresh}, market={self.market}")
        self._set_progress("prefetch_init", "正在确定股票代码来源...", 0, 100)

        market_scanner.load()
        cached_codes = market_scanner.get_cached_codes()

        if not force_refresh and len(cached_codes) > 0:
            # 增量更新：直接用本地缓存的代码，不调股票列表API
            codes = cached_codes
            total = len(codes)
            logger.info(f"增量更新：使用本地缓存代码 {total} 只（跳过股票列表API）")
            self._set_progress("prefetch_init", f"增量更新，共 {total} 只", 100, 100)

        else:
            # 全量更新：获取股票列表
            reason = "force_refresh=True" if force_refresh else "本地缓存为空"
            logger.info(f"全量更新：{reason}，获取股票列表...")
            self._set_progress("prefetch_init", "正在加载股票列表...", 0, 100)

            stock_list = self._load_stock_list()
            code_col, _ = self._get_code_name_cols(stock_list)
            codes = stock_list[code_col].astype(str).tolist() if not stock_list.empty else []
            total = len(codes)

            self._set_progress("prefetch_init", f"股票列表加载完成，共 {total} 只", 100, 100)

        if total == 0:
            return {"status": "ok", "cached_count": 0, "downloaded": 0, "failed_count": 0}

        # ── 检查停止信号 ──
        if self._stop_requested():
            logger.info("收到停止信号，中止下载（确定代码后）")
            return {"status": "stopped", "cached_count": 0, "downloaded": 0, "failed_count": 0}

        # ── 根据 market 过滤代码 ──
        if self.market != "全部市场":
            before_filter = total
            codes = self._filter_by_market(codes)
            total = len(codes)
            logger.info(f"市场过滤 [{self.market}]: {before_filter} → {total} 只")
            if total == 0:
                return {"status": "ok", "cached_count": 0, "downloaded": 0, "failed_count": 0}

        # ── 子阶段2：检测通达信离线数据 ──
        self._set_progress("prefetch_tdx", "正在检测通达信离线数据...", 0, total)

        tdx_available = False
        try:
            from ..data import tdx_offline
            tdx_available = True
        except Exception:
            pass

        tdx_msg = f"通达信离线数据{'可用' if tdx_available else '不可用'}，开始下载..."
        self._set_progress("prefetch_tdx", tdx_msg, 50, total)
        self._set_progress("prefetch_tdx", "通达信数据检测完成", 100, total)

        # ── 子阶段3：网络下载/更新K线 ──
        status_before = market_scanner.get_cache_status()
        before_count = status_before["memory_cached"]

        # 强制刷新：清空现有缓存（通过公共方法，线程安全）
        if force_refresh:
            market_scanner.clear_memory_cache()
            logger.info("强制刷新：已清空K线缓存")

        self._set_progress("prefetch_fetch", "正在下载/更新K线数据...", 0, total)
        with self._progress_lock:
            self._progress["strategies"] = {}

        def _on_fetch(code: str, done: int, total_codes: int):
            pct = int(done / total_codes * 100) if total_codes > 0 else 0
            self._set_progress("prefetch_fetch", f"下载K线 {done}/{total_codes}...", done, total_codes)

        fetch_result = market_scanner.prefetch_batch(
            codes, days=120, max_workers=50, progress_callback=_on_fetch,
            force_refresh=force_refresh,
            stop_event=self._stop_event,  # ← 传递停止事件
        )

        # ── 子阶段3.5：批量合并今日实时行情到内存缓存 ──
        logger.info(f"prefetch_merge 开始，缓存股票数: {len(market_scanner._kline_cache)}")
        self._set_progress("prefetch_merge", "正在获取实时行情...", 0, total)

        # 批量获取实时行情，并写入 _realtime_batch 缓存
        try:
            rb = market_scanner.get_realtime_batch(codes)
            if rb:
                market_scanner._realtime_batch = rb
                logger.info(f"prefetch_merge: 获取实时行情 {len(rb)} 只")
        except Exception as e:
            logger.warning(f"prefetch_merge: 批量获取实时行情失败: {e}")

        self._set_progress("prefetch_merge", "正在合并今日实时行情到内存...", 50, total)

        cached_codes_after = list(market_scanner._kline_cache.keys())
        merged_count = 0
        logger.info(f"prefetch_merge: 开始合并，缓存股票数={len(cached_codes_after)}")
        for i, code6 in enumerate(cached_codes_after):
            df = market_scanner._kline_cache[code6]
            if not df.empty:
                merged = market_scanner._merge_today_realtime(df, code6)
                market_scanner._kline_cache[code6] = merged
                merged_count += 1
            if i % 300 == 0 and total > 0:
                pct = int(i / max(len(cached_codes_after), 1) * 100)
                self._set_progress("prefetch_merge", f"合并实时行情 {i}/{len(cached_codes_after)}...", pct, 100)

        self._set_progress("prefetch_merge", f"今日实时行情合并完成（{merged_count}只）", 100, 100)

        # ── 子阶段4：完成 ──
        status_after = market_scanner.get_cache_status()
        after_count = status_after["memory_cached"]
        downloaded = after_count - before_count
        failed_codes = fetch_result.get("failed", [])

        if failed_codes:
            msg = f"数据更新完成，已缓存 {after_count}/{total} 只（{len(failed_codes)}只获取失败）"
            self._set_progress("prefetch_done", msg, after_count, total)
        else:
            msg = f"数据更新完成，已缓存全部 {after_count} 只"
            self._set_progress("prefetch_done", msg, after_count, total)

        return {"status": "ok", "cached_count": after_count, "downloaded": downloaded,
                "failed_count": len(failed_codes)}
    def screen_strategies(
        self,
        strategy_names: List[str],
        progress_callback: Optional[Callable[[str, str, int, int], None]] = None,
        on_strategy_done: Optional[Callable[[str, ScreenResult], None]] = None,
    ) -> Dict[str, ScreenResult]:
        """
        阶段2：运行策略（依赖缓存数据，速度快）。
        串行执行保证线程安全，每个策略完成后立即回调 on_strategy_done。

        Args:
            strategy_names: 要运行的策略列表
            progress_callback: 进度回调 (phase, message, current, total)
            on_strategy_done: 单策略完成回调 (name, result)
        Returns:
            {"status": "ok", "results": Dict[str, ScreenResult]}
        """
        stock_list = self._load_stock_list()
        market_scanner.load()

        # 按市场过滤股票列表
        if self.market != "全部市场":
            code_col, _ = self._get_code_name_cols(stock_list)
            codes = stock_list[code_col].astype(str).tolist()
            filtered = self._filter_by_market(codes)
            stock_list = stock_list[stock_list[code_col].astype(str).isin(filtered)]
            logger.info(f"screen_strategies: 按市场[{self.market}]过滤，{len(codes)} → {len(stock_list)} 只")

        total = len(strategy_names)
        results = {}

        # 初始化各策略状态为 pending
        for name in strategy_names:
            self._set_strategy_progress(name, "pending")

        for idx, name in enumerate(strategy_names, 1):
            if self._stop_requested():
                logger.info("收到停止信号，提前终止")
                self._set_progress("done", "已停止", idx, total)
                break

            # 阶段1: 数据加载
            self._set_progress("running", name, idx - 1, total)
            self._set_strategy_progress(name, "running", 0, 0, "loading")
            self._notify_progress(progress_callback, "running", name, idx - 1, total)

            total_codes = 0
            try:
                strategy = get_strategy(name, top_n=self.top_n)
                total_codes = len(strategy._get_codes(stock_list))

                # 阶段2: 策略执行中（带内部进度回调）
                def _on_strategy_progress(phase: str, scanned: int, total_stocks: int):
                    pct = self._calc_strategy_pct(scanned, total_stocks)
                    self._set_strategy_progress(
                        name, "running", pct, 0, "executing", scanned, total_stocks
                    )

                strategy.set_progress_callback(_on_strategy_progress)
                result = strategy.screen(stock_list, scanner=market_scanner)
                results[name] = result
                logger.info(f"策略 {name} 完成，命中 {len(result.all_signals)} 只")

                # 阶段3: 结果写入
                self._set_strategy_progress(name, "running", 99, 0, "writing", total_stocks=total_codes)

                # 阶段4: 执行完成
                self._set_strategy_progress(name, "done", 100, len(result.all_signals), "done",
                                            total_stocks=total_codes)
                if on_strategy_done:
                    on_strategy_done(name, result)
                self._set_progress("running", name, idx, total)
                self._notify_progress(progress_callback, "running", name, idx, total)
            except Exception as e:
                logger.error(f"策略 {name} 执行失败: {e}")
                self._set_strategy_progress(name, "done", 100, 0, "done", total_stocks=total_codes)

        # 强制将所有未完成的策略标记为 done，避免前端状态不一致
        with self._progress_lock:
            for name, info in self._progress["strategies"].items():
                if info.get("status") != "done":
                    self._progress["strategies"][name] = {
                        **info,
                        "status": "done",
                        "phase": "done",
                        "pct": 100,
                    }
        self._set_progress("done", "筛选完成", total, total)
        self._notify_progress(progress_callback, "done", "筛选完成", total, total)

        return {"status": "ok", "results": results}

    def run_multi(
        self,
        strategy_names: List[str],
        progress_callback: Optional[Callable[[str, str, int, int], None]] = None,
        force_refresh: bool = False,
        on_strategy_done: Optional[Callable[[str, ScreenResult], None]] = None,
    ) -> Dict[str, ScreenResult]:
        """
        完整执行（两阶段合一）：
        1. 预加载K线 → 2. 串行运行策略（流式回调）
        串行执行避免线程安全问题，每个策略完成后立即回调。
        """
        # 阶段1：下载数据
        self.download_data(force_refresh=force_refresh, progress_callback=progress_callback)
        # 阶段2：串行运行策略（带流式回调）
        return self.screen_strategies(
            strategy_names,
            progress_callback=progress_callback,
            on_strategy_done=on_strategy_done,
        )["results"]

    def run_all(self) -> Dict[str, ScreenResult]:
        """执行所有已注册策略"""
        return self.run_multi(list(STRATEGY_REGISTRY.keys()))

    def merge_results(
        self,
        results: Dict[str, ScreenResult],
        top_n: int = 20,
        min_single_score: int = _Thresholds.MIN_SINGLE_SCORE,
        min_weighted_score: int = _Thresholds.MIN_WEIGHTED_SCORE,
        market_trend: Optional[str] = None,
        market_strength: Optional[float] = None,
    ) -> List[Dict]:
        """
        多策略结果合并（加权评分 + 质量门槛 + 大盘趋势过滤）：
        同一只股票在多策略中命中 → 加权评分累加 → 胜率取最高值并叠加加成

        Args:
            results: 各策略筛选结果
            top_n: 返回前 N 只
            min_single_score: 单策略原始分最低门槛（低于此分的命中不计入）
            min_weighted_score: 加权总分最低门槛（低于此分的股票被过滤）
            market_trend: 大盘趋势（"bull" | "bear" | "neutral"），由调用方传入
            market_strength: 大盘趋势强度（-1.0 到 1.0），由调用方传入
        """
        # ── 根据大盘趋势调整门槛 ──
        adjusted_min_single = min_single_score
        adjusted_min_weighted = min_weighted_score

        if market_trend == "bear":
            adjusted_min_single = int(min_single_score * _Thresholds.BEAR_FACTOR)
            adjusted_min_weighted = int(min_weighted_score * _Thresholds.BEAR_FACTOR)
            logger.info(f"[大盘过滤器] 熊市模式：提高门槛至 single={adjusted_min_single}, "
                        f"weighted={adjusted_min_weighted}")
        elif market_trend == "bull":
            adjusted_min_single = int(min_single_score * _Thresholds.BULL_FACTOR)
            adjusted_min_weighted = int(min_weighted_score * _Thresholds.BULL_FACTOR)
            logger.info(f"[大盘过滤器] 牛市模式：降低门槛至 single={adjusted_min_single}, "
                        f"weighted={adjusted_min_weighted}")
        else:
            logger.info(f"[大盘过滤器] 中性模式：使用默认门槛 single={min_single_score}, "
                        f"weighted={min_weighted_score}")

        # ── 预计算每个策略内部排名 ──
        strategy_rank_info = {}
        for strategy_name, result in results.items():
            all_signals = result.all_signals
            total = len(all_signals)
            for rank, sig in enumerate(all_signals, 1):
                strategy_rank_info[(strategy_name, sig.ts_code)] = {
                    "rank": rank,
                    "rank_pct": rank / total if total > 0 else 1.0,
                }

        merged: Dict[str, Dict] = {}

        for strategy_name, result in results.items():
            meta = STRATEGY_REGISTRY.get(strategy_name, {})
            weight = meta.get("weight", 1.0)

            for sig in result.all_signals:
                # ── 质量门槛1：单策略原始分过滤（使用大盘调整后的门槛）──
                if sig.score < adjusted_min_single:
                    continue

                code = sig.ts_code
                rank_info = strategy_rank_info.get((strategy_name, code), {})
                rank_pct = rank_info.get("rank_pct", 1.0)

                if code not in merged:
                    merged[code] = {
                        "ts_code": code,
                        "name": sig.name,
                        "latest_price": sig.latest_price,
                        "pct_chg": sig.pct_chg,
                        "volume_ratio": sig.volume_ratio,
                        "trade_date": sig.trade_date,
                        "strategies_hit": [],
                        "total_score": 0,
                        "weighted_score": 0.0,
                        "all_signals": [],
                        "strategy_rank_sum": 0,
                        "strategy_count": 0,
                    }
                entry = merged[code]
                entry["strategies_hit"].append({
                    "id": strategy_name,
                    "name": meta.get("name", strategy_name),
                    "icon": meta.get("icon", ""),
                    "score": sig.score,
                    "weight": weight,
                    "signals": sig.signals,
                })

                # 策略内部排名加成：排名越靠前，加成越高
                rank_bonus = (1 - rank_pct) * _Thresholds.RANK_BONUS_MAX

                entry["total_score"] += sig.score
                entry["weighted_score"] += sig.score * weight + rank_bonus
                entry["all_signals"].extend(sig.signals)
                entry["strategy_rank_sum"] += rank_pct
                entry["strategy_count"] += 1

                # 收集风险标签（各策略可能返回相同flag，去重）
                if sig.risk_flags:
                    existing = {f["type"] for f in entry.get("_risk_flags", [])}
                    for f in sig.risk_flags:
                        if f["type"] not in existing:
                            entry.setdefault("_risk_flags", []).append(f)

        final_list = []
        for code, entry in merged.items():
            n_strategies = len(entry["strategies_hit"])

            # ── 质量门槛2：加权总分过滤（使用大盘调整后的门槛）──
            if entry["weighted_score"] < adjusted_min_weighted:
                continue

            # ── 一次性获取K线，复用于三个扫描 ──
            df = market_scanner.get_history(code, days=60)

            # ── 轻量风险扫描 ──
            risk_tag, risk_reasons, risk_score, calc_flags = self._quick_risk_scan(code, df)

            # ── 卖出信号扫描 ──
            sell_info = self._quick_sell_scan(code, df)

            # ── 买入风险评估（基于卖出信号）──
            buy_risk_info = self._quick_buy_risk_assess(code, df)

            # 合并策略计算的 risk_flags 和 K线计算的
            all_flags = entry.pop("_risk_flags", [])
            existing_types = {f["type"] for f in all_flags}
            for f in calc_flags:
                if f["type"] not in existing_types:
                    all_flags.append(f)

            # 计算平均排名百分比（越低越好）
            avg_rank_pct = (entry["strategy_rank_sum"] / entry["strategy_count"]
                            if entry["strategy_count"] > 0 else 1.0)

            # 综合评分 = 加权得分 + 排名加成 + 多策略共识 + 风险调整 + 大盘强度
            risk_adjustment = buy_risk_info["adjustment"] + (risk_score / 100.0) * _ScoreWeights.RISK_FACTOR
            market_strength_adj = market_strength * _ScoreWeights.MARKET_STRENGTH if market_strength is not None else 0
            composite_score = (
                entry["weighted_score"] * _ScoreWeights.WEIGHTED +
                (1 - avg_rank_pct) * _ScoreWeights.RANK +
                n_strategies * _ScoreWeights.CONSENSUS +
                risk_adjustment +
                market_strength_adj
            )
            composite_score = max(composite_score, 0)

            final_list.append({
                **entry,
                "n_strategies": n_strategies,
                "all_signals": list(dict.fromkeys(entry["all_signals"])),  # 去重保序保留频次（不转set）
                # 风险信息
                "risk_tag": risk_tag,
                "risk_reasons": risk_reasons,
                "risk_score": risk_score,
                "risk_flags": all_flags,
                # 卖出信号信息
                "sell_signals": sell_info["sell_signals"],
                "sell_score": sell_info["sell_score"],
                "stop_loss_price": sell_info["stop_loss_price"],
                "take_profit_price": sell_info["take_profit_price"],
                "sell_risk_level": sell_info["risk_level"],
                # 买入风险评估
                "buy_risk": buy_risk_info["buy_risk"],
                "buy_risk_reasons": buy_risk_info["risk_reasons"],
                # 新增排序因子
                "composite_score": round(composite_score, 2),
                "avg_rank_pct": round(avg_rank_pct, 3),
            })

        # 排序：命中策略数 > 综合评分
        final_list.sort(key=lambda x: (x["n_strategies"], x["composite_score"]), reverse=True)
        return final_list[:top_n]

    def _quick_risk_scan(self, code: str, df: Optional[pd.DataFrame] = None):
        """
        轻量风险扫描：利用已缓存的K线快速判断是否存在卖出风险。
        不发任何新网络请求——仅读取内存缓存。

        Args:
            code: 股票代码
            df: 可选，已获取的K线DataFrame。传入可避免重复获取。
        Returns:
            (risk_tag, risk_reasons, risk_score, risk_flags)
        """
        try:
            if df is None:
                df = market_scanner.get_history(code, days=60)
            if df is None or df.empty or len(df) < _Thresholds.RISK_SCAN_MIN_BARS:
                return "unknown", [], 0, []

            close = df["close"].astype(float)
            high = df["high"].astype(float)
            low = df["low"].astype(float)
            vol = df["vol"].astype(float)
            pct_col = "pct_chg" if "pct_chg" in df.columns else "daily_chg"
            pct_chg = df[pct_col].astype(float) if pct_col in df.columns else pd.Series(0, index=close.index)

            flags = calc_risk_flags(close, high, low, vol, pct_chg)

            score = sum(25 if f["level"] == "danger" else 12 for f in flags)
            score = min(score, 100)
            reasons = [f["desc"] for f in flags]

            if score >= 50:
                tag = "high_risk"
            elif score >= 28:
                tag = "conflict"
            elif score >= 12:
                tag = "watch"
            else:
                tag = "safe"

            return tag, reasons, score, flags

        except Exception as e:
            logger.debug(f"风险扫描 {code} 失败: {e}")
            return "unknown", [], 0, []

    def _quick_sell_scan(self, code: str, df: Optional[pd.DataFrame] = None) -> dict:
        """
        卖出信号快速扫描：利用已缓存的K线检测卖出信号

        Args:
            code: 股票代码
            df: 可选，已获取的K线DataFrame。传入可避免重复获取。
        Returns:
            detect_sell_signals() 的返回值
        """
        try:
            if df is None:
                df = market_scanner.get_history(code, days=60)
            if df is None or df.empty or len(df) < _Thresholds.SELL_SCAN_MIN_BARS:
                return {
                    "has_sell_signal": False,
                    "sell_signals": [],
                    "sell_score": 0,
                    "stop_loss_price": None,
                    "take_profit_price": None,
                    "risk_level": "unknown",
                }

            return detect_sell_signals(df)

        except Exception as e:
            logger.debug(f"卖出信号扫描 {code} 失败: {e}")
            return {
                "has_sell_signal": False,
                "sell_signals": [],
                "sell_score": 0,
                "stop_loss_price": None,
                "take_profit_price": None,
                "risk_level": "unknown",
            }

    def _quick_buy_risk_assess(self, code: str, df: Optional[pd.DataFrame] = None) -> dict:
        """
        买入风险评估：基于卖出信号评估买入风险

        Args:
            code: 股票代码
            df: 可选，已获取的K线DataFrame。传入可避免重复获取。
        Returns:
            assess_buy_risk() 的返回值
        """
        try:
            if df is None:
                df = market_scanner.get_history(code, days=60)
            if df is None or df.empty or len(df) < _Thresholds.BUY_RISK_MIN_BARS:
                return {
                    "buy_risk": "unknown",
                    "risk_reasons": [],
                    "adjustment": 0,
                }

            return assess_buy_risk(df)

        except Exception as e:
            logger.debug(f"买入风险评估 {code} 失败: {e}")
            return {
                "buy_risk": "unknown",
                "risk_reasons": [],
                "adjustment": 0,
            }

    def get_recommendation(
        self,
        strategies: Optional[List[str]] = None,
        top_n: int = 10,
        progress_callback: Optional[Callable[[str, str, int, int], None]] = None,
        force_refresh: bool = False,
        on_strategy_done: Optional[Callable[[str, ScreenResult], None]] = None,
        skip_download: bool = False,
    ) -> Dict:
        """
        一键获取推荐：执行策略 → 合并 → 排序
        策略内部已并行（BaseStrategy 模板方法），Engine 层串行调度即可。
        """
        if strategies is None:
            strategies = [k for k in STRATEGY_REGISTRY if k != "limit_up_gene"]

        # 阶段1：预加载K线数据（可跳过）
        _t0 = datetime.now()
        if not skip_download:
            dl_result = self.download_data(force_refresh=force_refresh, progress_callback=progress_callback)
            if dl_result.get("status") == "stopped":
                logger.info("用户已停止下载，提前终止筛选")
                self._set_progress("done", "已停止", 0, 100)
                self._notify_progress(progress_callback, "done", "已停止", 0, 100)
                return {"status": "stopped", "comprehensive_picks": []}
        else:
            self._set_progress("prefetch", "使用缓存数据，跳过下载", 30, 100)
            self._notify_progress(progress_callback, "prefetch", "使用缓存数据，跳过下载", 30, 100)
        _t1 = datetime.now()
        logger.info(f"阶段1完成, 耗时: {_t1 - _t0}")

        # 阶段1.5：预计算指标（按需，非阻塞）
        # 若开启实时数据，先批量获取全市场实时行情存入临时缓存，
        # 避免预计算阶段逐只请求触发限流
        _orig_include = market_scanner._include_realtime
        if _orig_include and not skip_download:
            stock_list = self._load_stock_list()
            code_col, _ = self._get_code_name_cols(stock_list)
            all_codes = stock_list[code_col].astype(str).tolist()
            logger.info(f"批量获取实时行情: {len(all_codes)}只")
            realtime_map = market_scanner.get_realtime_batch(all_codes)
            market_scanner._realtime_batch = realtime_map
            logger.info(f"实时行情获取完成: {len(realtime_map)}只")

        market_scanner._include_realtime = True
        try:
            stock_list = self._load_stock_list()
            _t2 = datetime.now()
            self._precalc(stock_list, progress_callback=progress_callback)
            logger.info(f"阶段1.5完成, 耗时: {_t2 - _t0}")
        finally:
            market_scanner._include_realtime = _orig_include
            # 清空批量实时行情临时缓存
            market_scanner._realtime_batch.clear()

        # 阶段2：串行运行策略（每个策略内部已并行）
        _wrapped_cb = None
        if progress_callback:
            def _wrapped_cb(phase, message, idx, total):
                if phase != "done":
                    progress_callback(phase, message, idx, total)
        results = self.screen_strategies(
            strategies,
            progress_callback=_wrapped_cb,
            on_strategy_done=on_strategy_done,
        )["results"]

        # 阶段3: 结果合并中
        self._set_progress("merging", "正在合并结果...", 0, 100)
        self._notify_progress(progress_callback, "merging", "正在合并结果...", 0, 100)

        # 获取大盘趋势
        logger.info("[大盘过滤器] 正在判断大盘趋势...")
        market_trend = get_market_trend(market_scanner)
        market_strength = get_market_trend_strength(market_scanner)
        logger.info(f"[大盘过滤器] 大盘趋势: {market_trend}, 强度: {market_strength:.2f}")

        merged = self.merge_results(
            results,
            top_n=top_n,
            market_trend=market_trend,
            market_strength=market_strength,
        )
        self._set_progress("merging", "结果合并完成", 100, 100)
        self._notify_progress(progress_callback, "merging", "结果合并完成", 100, 100)
        # 最终 done：整个流程真正完成
        self._notify_progress(progress_callback, "done", "筛选完成", len(strategies), len(strategies))

        strategy_summaries = self._build_strategy_summaries(results)

        return {
            "trade_date": get_latest_trade_date(),
            "strategies_run": strategies,
            "comprehensive_picks": merged,
            "strategy_details": strategy_summaries,
        }

    def evaluate_positions(
        self,
        codes: List[str],
        strategies: Optional[List[str]] = None,
    ) -> List[Dict]:
        """
        对持仓股票运行全部策略评估，返回每只股票的综合评分和各策略命中详情。
        与 get_recommendation 的区别：只评估指定股票，不扫描全市场。
        """
        if strategies is None:
            strategies = [k for k in STRATEGY_REGISTRY.keys() if k != "limit_up_gene"]

        market_scanner.load()
        trade_date = get_latest_trade_date()

        # 构建代码 -> 名称 映射
        stock_list = self._load_stock_list()
        name_map = {}
        code_col, name_col = self._get_code_name_cols(stock_list)
        if code_col in stock_list.columns and name_col in stock_list.columns:
            for _, row in stock_list.iterrows():
                name_map[str(row[code_col])] = str(row[name_col])

        # 逐策略评估持仓股票
        per_strategy_results: Dict[str, ScreenResult] = {}
        for strategy_name in strategies:
            if self._stop_requested():
                logger.info("收到停止信号，提前终止持仓评估")
                break

            strategy = get_strategy(strategy_name, top_n=self.top_n)
            signals = []

            # 并行评估所有代码
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=20) as pool:
                futures = {
                    pool.submit(strategy._evaluate_single_stock,
                                code, market_scanner, name_map, trade_date): code
                    for code in codes
                }
                for future in as_completed(futures):
                    if self._stop_requested():
                        break
                    try:
                        sig = future.result()
                        if sig is not None:
                            signals.append(sig)
                    except Exception as e:
                        logger.debug(f"[持仓评估] {strategy_name} {futures[future]} 失败: {e}")

            per_strategy_results[strategy_name] = ScreenResult(
                strategy_name=strategy_name,
                strategy_desc=STRATEGY_REGISTRY.get(strategy_name, {}).get("description", ""),
                signals=signals,
                trade_date=trade_date,
                total_scanned=len(codes),
                all_signals=signals[:],
            )

        # 合并结果生成综合评分（复用 merge_results）
        market_trend = get_market_trend(market_scanner)
        market_strength = get_market_trend_strength(market_scanner)
        merged = self.merge_results(
            per_strategy_results,
            top_n=len(codes),
            market_trend=market_trend,
            market_strength=market_strength,
        )

        # 构建 code -> result 映射
        result_map = {m["ts_code"]: m for m in merged}

        # 为未命中任何策略的股票也填充基础信息
        for code in codes:
            if code not in result_map:
                result_map[code] = {**_EMPTY_POSITION_RESULT, "ts_code": code,
                                    "name": name_map.get(code, code), "trade_date": trade_date}

        return list(result_map.values())

    @staticmethod
    def _notify_progress(callback: Optional[Callable], phase: str, message: str,
                         current: int, total: int):
        """安全调用进度回调"""
        if callback:
            callback(phase, message, current, total)

    @staticmethod
    def _build_strategy_summaries(results: Dict[str, ScreenResult]) -> dict:
        """构建策略摘要，供前端展示用"""
        summaries = {}
        for name, result in results.items():
            summaries[name] = {
                "strategy_name": result.strategy_name,
                "strategy_desc": result.strategy_desc,
                "trade_date": result.trade_date,
                "total_scanned": result.total_scanned,
                "hit_count": len(result.all_signals),
                "top_stocks": [
                    {
                        "ts_code": s.ts_code,
                        "name": s.name,
                        "score": s.score,
                        "signals": s.signals,
                        "latest_price": s.latest_price,
                        "pct_chg": s.pct_chg,
                        "volume_ratio": s.volume_ratio,
                        "extra": s.extra,
                    }
                    for s in result.signals
                ]
            }
        return summaries
