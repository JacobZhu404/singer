"""
核心引擎：策略调度 + 胜率计算 + 综合推荐
"""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from typing import List, Optional, Dict, Callable

import pandas as pd

from ..strategies.base import ScreenResult, StockSignal
from ..strategies.registry import get_strategy, list_strategies, STRATEGY_REGISTRY
from ..data.fetcher import get_stock_list, market_scanner, get_latest_trade_date
from ..utils.indicators import calc_macd, calc_rsi, calc_bollinger, calc_ma, calc_risk_flags

from datetime import datetime

logger = logging.getLogger(__name__)


class ScreenEngine:
    """
    选股引擎：
    - 支持单策略 / 多策略并行执行
    - 多策略结果合并：同一只股票被多策略命中则评分叠加
    - 统一胜率输出
    """

    def __init__(self, market: str = "主板", top_n: int = 10):
        self.market = market
        self.top_n = top_n
        self._stock_list: Optional[pd.DataFrame] = None
        self._stop_event: Optional[threading.Event] = None
        # 进度状态（供外部轮询）
        self._progress: Dict = {
            "phase": "idle",   # idle | prefetch | running | merging | done
            "current": "",
            "current_index": 0,
            "total": 0,
            "pct": 0,
            "strategies": {},  # name -> {status, phase, pct, hits, scanned, total_stocks}
        }
        self._progress_lock = threading.Lock()
        self._running_count = 0  # 当前正在运行的策略数（并行用）

    def _stop_requested(self) -> bool:
        if self._stop_event is None:
            return False
        return self._stop_event.is_set()

    def _set_progress(self, phase: str, current: str = "", current_index: int = 0, total: int = 0, pct: int = -1):
        with self._progress_lock:
            self._progress.update({
                "phase": phase,
                "current": current,
                "current_index": current_index,
                "total": total,
                "pct": pct if pct >= 0 else int(current_index / total * 100) if total > 0 else 0,
            })

    def _set_strategy_progress(self, name: str, status: str, pct: int = 0, hits: int = 0,
                                phase: str = "", scanned: int = 0, total_stocks: int = 0):
        """更新单个策略的进度状态，并同步跟踪 running 策略数量"""
        with self._progress_lock:
            old = self._progress["strategies"].get(name, {})
            old_status = old.get("status")
            if old_status != "running" and status == "running":
                self._running_count += 1
            elif old_status == "running" and status != "running":
                self._running_count = max(0, self._running_count - 1)
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

    def _calc_progress_with_running(self, completed: int, total: int) -> int:
        """计算总体进度：已完成 + running 中策略按 50% 计算"""
        if total <= 0:
            return 0
        with self._progress_lock:
            running = self._running_count
        raw = completed + running * 0.5
        return min(int(raw / total * 100), 99)

    def get_progress(self) -> Dict:
        with self._progress_lock:
            return dict(self._progress)

    def _load_stock_list(self, sample_ratio: float = 1.0) -> pd.DataFrame:
        if self._stock_list is None:
            logger.info(f"加载股票列表")
            self._stock_list = get_stock_list()
        if sample_ratio < 1.0 and not self._stock_list.empty:
            n = max(1, int(len(self._stock_list) * sample_ratio))
            logger.info(f"采样模式: {len(self._stock_list)} → {n} 只 (ratio={sample_ratio})")
            return self._stock_list.sample(n=n, random_state=42)
        return self._stock_list

    def _precalc(self, stock_list: pd.DataFrame,
                 progress_callback: Optional[Callable[[str, str, int, int], None]] = None,
                 sample_ratio: float = 1.0):
        """预计算指标并缓存，避免各策略重复计算"""
        from ..utils.precalc import precalc_indicators
        code_col = "代码" if "代码" in stock_list.columns else "ts_code"
        if stock_list.empty or code_col not in stock_list.columns:
            return
        codes = stock_list[code_col].astype(str).tolist()
        if sample_ratio < 1.0 and codes:
            n = max(1, int(len(codes) * sample_ratio))
            import random
            random.seed(42)
            codes = random.sample(codes, n)
            logger.info(f"precalc 采样: {len(stock_list)} → {n} 只")
        if not codes:
            return

        def _on_precalc_progress(done: int, total: int, code: str):
            if progress_callback:
                progress_callback("precalc", f"预计算指标 {done}/{total}...", done, total)

        precalc_indicators(codes, market_scanner, days=120, progress_callback=_on_precalc_progress)

    def run_single(self, strategy_name: str) -> ScreenResult:
        """执行单个策略"""
        stock_list = self._load_stock_list()
        strategy = get_strategy(strategy_name, top_n=self.top_n)
        market_scanner.load()
        logger.info(f"执行策略: {strategy_name}")
        result = strategy.screen(stock_list, scanner=market_scanner)
        return result

    def download_data(
        self,
        force_refresh: bool = False,
        progress_callback: Optional[Callable[[str, int, int, str], None]] = None,
        sample_ratio: float = 1.0,
    ) -> dict:
        """
        阶段1：下载/更新K线数据到缓存。
        拆分出来让用户可以先更新数据、再跑策略。

        Args:
            force_refresh: True=强制重新下载所有股票（忽略缓存）
                          False=只补充未缓存的股票
            progress_callback: 进度回调 (phase, message, current, total)
            sample_ratio: 采样比例，1.0=全量，0.1=1/10
        Returns:
            {"status": "ok", "cached_count": int, "downloaded": int}
        """
        stock_list = self._load_stock_list(sample_ratio=sample_ratio)
        market_scanner.load()

        code_col = "代码" if "代码" in stock_list.columns else "ts_code"
        codes = stock_list[code_col].astype(str).tolist()

        # 强制刷新：清空现有缓存（通过公共方法，线程安全）
        if force_refresh:
            market_scanner.clear_memory_cache()
            logger.info("强制刷新：已清空K线缓存")

        status_before = market_scanner.get_cache_status()
        before_count = status_before["memory_cached"]

        # 重置进度并清空策略子进度
        self._set_progress("prefetch", "正在加载行情数据...", 0, len(codes))
        with self._progress_lock:
            self._progress["strategies"] = {}

        if progress_callback:
            progress_callback("prefetch", "正在加载行情数据...", 0, len(codes))

        def _on_fetch(code: str, done: int, total_codes: int):
            self._set_progress("prefetch", f"加载中 {done}/{total_codes}...", done, total_codes)
            if progress_callback:
                progress_callback("prefetch", f"加载K线 {done}/{total_codes}...", done, total_codes)

        fetch_result = market_scanner.prefetch_batch(codes, days=120, max_workers=50,
                                       progress_callback=_on_fetch)

        status_after = market_scanner.get_cache_status()
        after_count = status_after["memory_cached"]
        downloaded = after_count - before_count
        total_codes = len(codes)
        failed_codes = fetch_result.get("failed", [])

        if failed_codes:
            self._set_progress("done",
                               f"数据更新完成，已缓存 {after_count}/{total_codes} 只"
                               f"（{len(failed_codes)}只获取失败）",
                               after_count, total_codes)
            if progress_callback:
                progress_callback("done",
                                  f"数据更新完成，已缓存 {after_count}/{total_codes} 只",
                                  after_count, total_codes)
        else:
            self._set_progress("done", f"数据更新完成，已缓存全部 {after_count} 只", after_count, total_codes)
            if progress_callback:
                progress_callback("done", f"数据更新完成，已缓存全部 {after_count} 只", after_count, total_codes)

        return {"status": "ok", "cached_count": after_count, "downloaded": downloaded,
                "failed_count": len(failed_codes)}

    def screen_strategies(
        self,
        strategy_names: List[str],
        progress_callback: Optional[Callable[[str, int, int, str], None]] = None,
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
            if progress_callback:
                progress_callback("running", name, idx - 1, total)

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
                self._set_strategy_progress(name, "done", 100, len(result.all_signals), "done", total_stocks=total_codes)
                if on_strategy_done:
                    on_strategy_done(name, result)
                self._set_progress("running", name, idx, total)
                if progress_callback:
                    progress_callback("running", name, idx, total)
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
        if progress_callback:
            progress_callback("done", "筛选完成", total, total)

        return {"status": "ok", "results": results}

    def screen_strategies_parallel(
        self,
        strategy_names: List[str],
        progress_callback: Optional[Callable[[str, str, int, int], None]] = None,
        on_strategy_done: Optional[Callable[[str, ScreenResult], None]] = None,
    ) -> Dict[str, ScreenResult]:
        """
        并行运行策略，每个策略完成后立即回调 on_strategy_done。
        所有策略共享同一份已缓存的K线数据，读取操作线程安全。
        """
        stock_list = self._load_stock_list()
        market_scanner.load()

        total = len(strategy_names)
        completed = 0
        results = {}
        completed_lock = threading.Lock()

        # 初始化各策略状态为 pending（前端会显示"等待中"）
        for name in strategy_names:
            self._set_strategy_progress(name, "pending")

        # 先广播 running 阶段开始，让前端有时间渲染策略卡片状态
        _t3 = datetime.now()
        self._set_progress("running", "策略启动中...", 0, total)
        if progress_callback:
            progress_callback("running", "策略启动中...", 0, total)
        logger.info(f"[{datetime.now()}] 设置'策略启动中'完成, 耗时: {datetime.now()-_t3}")

        def run_one(name: str) -> tuple:
            _t_run = datetime.now()
            import sys
            logger.info(f"[{datetime.now()}] [ENGINE] run_one 开始: {name}")
            if self._stop_requested():
                raise RuntimeError("stopped")
            self._set_strategy_progress(name, "running", 0, 0, "loading")
            logger.info(f"[{datetime.now()}] [ENGINE] {name} 已设置loading, 耗时: {datetime.now()-_t_run}")
            strategy = get_strategy(name, top_n=self.top_n)
            total_codes = len(strategy._get_codes(stock_list))
            logger.info(f"[{datetime.now()}] [ENGINE] {name}: total_codes={total_codes}, 耗时: {datetime.now()-_t_run}")

            def _on_strategy_progress(phase: str, scanned: int, total_stocks: int):
                if self._stop_requested():
                    raise RuntimeError("stopped")
                pct = self._calc_strategy_pct(scanned, total_stocks)
                self._set_strategy_progress(
                    name, "running", pct, 0, "executing", scanned, total_stocks
                )

            strategy.set_progress_callback(_on_strategy_progress)
            logger.info(f"[{datetime.now()}] [ENGINE] {name} 开始执行 strategy.screen(), 已耗时: {datetime.now()-_t_run}")
            result = strategy.screen(stock_list, scanner=market_scanner)
            logger.info(f"[{datetime.now()}] [ENGINE] {name} strategy.screen() 完成, 耗时: {datetime.now()-_t_run}")
            self._set_strategy_progress(name, "running", 99, 0, "writing", total_stocks=total_codes)
            self._set_strategy_progress(name, "done", 100, len(result.signals), "done", total_stocks=total_codes)
            return name, result

        with ThreadPoolExecutor(max_workers=min(8, total)) as executor:
            futures = {executor.submit(run_one, name): name for name in strategy_names}
            for future in as_completed(futures):
                if self._stop_requested():
                    for f in futures:
                        f.cancel()
                    break
                try:
                    name, result = future.result()
                    with completed_lock:
                        results[name] = result
                        completed += 1
                        current_completed = completed
                    logger.info(f"策略 {name} 完成，命中 {len(result.all_signals)} 只")
                    if on_strategy_done:
                        on_strategy_done(name, result)
                    overall_pct = self._calc_progress_with_running(current_completed, total)
                    self._set_progress("running", f"已完成 {current_completed}/{total}", current_completed, total, pct=overall_pct)
                    if progress_callback:
                        progress_callback("running", f"已完成 {current_completed}/{total}", current_completed, total)
                except Exception as e:
                    logger.error(f"策略执行失败: {e}")
                    self._set_strategy_progress(name, "done", 100, 0, "done")

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
        if progress_callback:
            progress_callback("done", "筛选完成", total, total)
        return results

    def run_multi(
        self,
        strategy_names: List[str],
        progress_callback: Optional[Callable[[str, int, int, str], None]] = None,
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
        top_n: int = 10
    ) -> List[Dict]:
        """
        多策略结果合并：
        同一只股票在多策略中命中 → 综合评分累加 → 胜率取最高值并叠加加成
        """
        merged: Dict[str, Dict] = {}

        for strategy_name, result in results.items():
            meta = STRATEGY_REGISTRY.get(strategy_name, {})
            for sig in result.all_signals:
                code = sig.ts_code
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
                        "max_win_rate": 0,
                        "all_signals": [],
                    }
                entry = merged[code]
                entry["strategies_hit"].append({
                    "id": strategy_name,
                    "name": meta.get("name", strategy_name),
                    "icon": meta.get("icon", ""),
                    "score": sig.score,
                    "signals": sig.signals,
                })
                entry["total_score"] += sig.score
                entry["max_win_rate"] = max(entry["max_win_rate"], sig.win_rate)
                entry["all_signals"].extend(sig.signals)
                # 收集风险标签（各策略可能返回相同flag，去重）
                if sig.risk_flags:
                    existing = {f["type"] for f in entry.get("_risk_flags", [])}
                    for f in sig.risk_flags:
                        if f["type"] not in existing:
                            entry.setdefault("_risk_flags", []).append(f)

        # 叠加加成：被多策略命中则胜率额外提升
        final_list = []
        for code, entry in merged.items():
            n_strategies = len(entry["strategies_hit"])
            overlap_bonus = min((n_strategies - 1) * 0.05, 0.15)
            final_win_rate = min(entry["max_win_rate"] + overlap_bonus, 0.90)

            # ── 轻量风险扫描（利用已缓存K线，不发新请求）────────────
            risk_tag, risk_reasons, risk_score, calc_flags = self._quick_risk_scan(code)

            # 合并策略计算的 risk_flags 和 K线计算的
            all_flags = entry.pop("_risk_flags", [])
            existing_types = {f["type"] for f in all_flags}
            for f in calc_flags:
                if f["type"] not in existing_types:
                    all_flags.append(f)

            final_list.append({
                **entry,
                "n_strategies": n_strategies,
                "final_win_rate": round(final_win_rate, 3),
                "win_rate_pct": f"{final_win_rate * 100:.1f}%",
                "all_signals": list(set(entry["all_signals"])),
                # 风险信息
                "risk_tag": risk_tag,
                "risk_reasons": risk_reasons,
                "risk_score": risk_score,
                "risk_flags": all_flags,   # [{type, label, level, desc}, ...]
            })

        # 按综合评分排序
        final_list.sort(key=lambda x: (x["n_strategies"], x["total_score"]), reverse=True)
        return final_list[:top_n]

    def _quick_risk_scan(self, code: str):
        """
        轻量风险扫描：利用已缓存的K线快速判断是否存在卖出风险。
        不发任何新网络请求——仅读取内存缓存。
        使用统一的 calc_risk_flags 计算。

        Returns:
            (risk_tag, risk_reasons, risk_score)
            risk_tag: "safe" | "watch" | "conflict" | "high_risk"
            risk_reasons: List[str]  具体风险描述
            risk_score: int  0-100，越高风险越大
        """
        try:
            df = market_scanner.get_history(code, days=60)
            if df is None or df.empty or len(df) < 15:
                return "unknown", [], 0, []

            close = df["close"].astype(float)
            high  = df["high"].astype(float)
            low   = df["low"].astype(float)
            vol   = df["vol"].astype(float)
            pct_col = "pct_chg" if "pct_chg" in df.columns else "daily_chg"
            pct_chg = df[pct_col].astype(float) if pct_col in df.columns else pd.Series(0, index=close.index)

            flags = calc_risk_flags(close, high, low, vol, pct_chg)

            # 计算风险评分：danger=25分/warn=12分
            score = sum(25 if f["level"] == "danger" else 12 for f in flags)
            score = min(score, 100)
            reasons = [f["desc"] for f in flags]

            # 标签
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

    def get_recommendation(
        self,
        strategies: Optional[List[str]] = None,
        top_n: int = 10,
        progress_callback: Optional[Callable[[str, str, int, int], None]] = None,
        force_refresh: bool = False,
        on_strategy_done: Optional[Callable[[str, ScreenResult], None]] = None,
        parallel: bool = True,
        skip_download: bool = False,
        sample_ratio: float = 1.0,
    ) -> Dict:
        """
        一键获取推荐：执行策略 → 合并 → 排序
        默认并行执行策略，每个策略完成后立即回调 on_strategy_done。
        sample_ratio: 采样比例，1.0=全量，0.1=1/10，用于测试
        """
        if strategies is None:
            strategies = [k for k in STRATEGY_REGISTRY if k != "limit_up_gene"]

        # 阶段1：预加载K线数据（可跳过）
        _t0 = datetime.now()
        if not skip_download:
            self.download_data(force_refresh=force_refresh, progress_callback=progress_callback,
                               sample_ratio=sample_ratio)
        else:
            self._set_progress("prefetch", "使用缓存数据，跳过下载", 30, 100)
            if progress_callback:
                progress_callback("prefetch", "使用缓存数据，跳过下载", 30, 100)
        logger.info(f"[{datetime.now()}] 阶段1完成, 耗时: {datetime.now()-_t0}")

        # 阶段1.5：预计算指标（按需，非阻塞）
        # 强制关闭实时行情，只用本地缓存，避免 precalc 发起网络请求
        _orig_include = market_scanner._include_realtime
        market_scanner._include_realtime = False
        try:
            stock_list = self._load_stock_list(sample_ratio=sample_ratio)
            _t1 = datetime.now()
            self._precalc(stock_list, progress_callback=progress_callback,
                          sample_ratio=sample_ratio)
            logger.info(f"[{datetime.now()}] 阶段1.5完成, 耗时: {datetime.now()-_t1}")
        finally:
            market_scanner._include_realtime = _orig_include

        # 阶段2：并行运行策略（流式回调）
        _t2 = datetime.now()
        if parallel:
            results = self.screen_strategies_parallel(
                strategies,
                progress_callback=progress_callback,
                on_strategy_done=on_strategy_done,
            )
        else:
            results = self.screen_strategies(
                strategies,
                progress_callback=progress_callback,
                on_strategy_done=on_strategy_done,
            )["results"]

        # 阶段3: 结果合并中
        self._set_progress("merging", "正在合并结果...", 0, 100)
        merged = self.merge_results(results, top_n=top_n)
        self._set_progress("merging", "结果合并完成", 100, 100)

        strategy_summaries = {}
        for name, result in results.items():
            strategy_summaries[name] = {
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
                        "win_rate": s.win_rate,
                        "win_rate_pct": f"{s.win_rate * 100:.1f}%",
                        "signals": s.signals,
                        "latest_price": s.latest_price,
                        "pct_chg": s.pct_chg,
                        "volume_ratio": s.volume_ratio,
                        "extra": s.extra,
                    }
                    for s in result.signals
                ]
            }

        return {
            "trade_date": get_latest_trade_date(),
            "strategies_run": strategies,
            "comprehensive_picks": merged,
            "strategy_details": strategy_summaries,
        }
