"""
核心引擎：策略调度 + 胜率计算 + 综合推荐
"""

import json
import logging
import threading
from dataclasses import asdict
from typing import List, Optional, Dict, Callable

import pandas as pd

from ..strategies.base import ScreenResult, StockSignal
from ..strategies.registry import get_strategy, list_strategies, STRATEGY_REGISTRY
from ..data.fetcher import get_stock_list, market_scanner, get_latest_trade_date
from ..utils.indicators import calc_macd, calc_rsi, calc_bollinger, calc_ma, calc_risk_flags
from ..utils.market_trend import get_market_trend, get_market_trend_strength
from ..utils.sell_signals import detect_sell_signals, assess_buy_risk

from datetime import datetime

logger = logging.getLogger(__name__)


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
            "phase": "idle",   # idle | prefetch | running | merging | done
            "current": "",
            "current_index": 0,
            "total": 0,
            "pct": 0,
            "strategies": {},  # name -> {status, phase, pct, hits, scanned, total_stocks}
        }
        self._progress_lock = threading.Lock()

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

    def _load_stock_list(self) -> pd.DataFrame:
        if self._stock_list is None:
            logger.info(f"加载股票列表")
            self._stock_list = get_stock_list()
        return self._stock_list

    def _precalc(self, stock_list: pd.DataFrame,
                 progress_callback: Optional[Callable[[str, str, int, int], None]] = None):
        """预计算指标并缓存，避免各策略重复计算"""
        from ..utils.precalc import precalc_indicators
        code_col = "代码" if "代码" in stock_list.columns else "ts_code"
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
        strategy = get_strategy(strategy_name, top_n=self.top_n)
        market_scanner.load()
        logger.info(f"执行策略: {strategy_name}")
        result = strategy.screen(stock_list, scanner=market_scanner)
        return result

    def download_data(
        self,
        force_refresh: bool = False,
        progress_callback: Optional[Callable[[str, int, int, str], None]] = None,
    ) -> dict:
        """
        阶段1：下载/更新K线数据到缓存。
        拆分出来让用户可以先更新数据、再跑策略。

        Args:
            force_refresh: True=强制重新下载所有股票（忽略缓存）
                          False=只补充未缓存的股票
            progress_callback: 进度回调 (phase, message, current, total)
        Returns:
            {"status": "ok", "cached_count": int, "downloaded": int}
        """
        stock_list = self._load_stock_list()
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
        top_n: int = 20,
        min_single_score: int = 20,          # 从30降至20，降低单策略门槛
        min_weighted_score: int = 30,        # 从45降至30，降低加权总分门槛
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
            # 熊市：提高门槛，只保留最强信号
            adjusted_min_single = int(min_single_score * 1.3)
            adjusted_min_weighted = int(min_weighted_score * 1.3)
            logger.info(f"[大盘过滤器] 熊市模式：提高门槛至 single={adjusted_min_single}, weighted={adjusted_min_weighted}")
        elif market_trend == "bull":
            # 牛市：适当放宽门槛
            adjusted_min_single = int(min_single_score * 0.9)
            adjusted_min_weighted = int(min_weighted_score * 0.9)
            logger.info(f"[大盘过滤器] 牛市模式：降低门槛至 single={adjusted_min_single}, weighted={adjusted_min_weighted}")
        else:
            logger.info(f"[大盘过滤器] 中性模式：使用默认门槛 single={min_single_score}, weighted={min_weighted_score}")
        
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
                rank_bonus = (1 - rank_pct) * 15  # 第1名得15分，最后1名得0分

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

            # ── 轻量风险扫描（利用已缓存K线，不发新请求）────────────
            risk_tag, risk_reasons, risk_score, calc_flags = self._quick_risk_scan(code)

            # ── 卖出信号扫描 ──
            sell_info = self._quick_sell_scan(code)
            
            # ── 买入风险评估（基于卖出信号）──
            buy_risk_info = self._quick_buy_risk_assess(code)

            # 合并策略计算的 risk_flags 和 K线计算的
            all_flags = entry.pop("_risk_flags", [])
            existing_types = {f["type"] for f in all_flags}
            for f in calc_flags:
                if f["type"] not in existing_types:
                    all_flags.append(f)

            # 计算平均排名百分比（越低越好）
            avg_rank_pct = entry["strategy_rank_sum"] / entry["strategy_count"] if entry["strategy_count"] > 0 else 1.0

            # 综合评分 = 加权得分50% + 排名加成20% + 多策略共识10% + 风险调整20%
            # 基于回测负收益调整：增加风险权重，降低纯评分权重
            risk_adjustment = buy_risk_info["adjustment"] + (risk_score / 100.0) * 20  # 风险评分也影响总分
            composite_score = (
                entry["weighted_score"] * 0.5 +
                (1 - avg_rank_pct) * 20 +
                n_strategies * 5 +
                risk_adjustment
            )
            
            # ── 根据买入风险调整综合评分 ──
            composite_score += buy_risk_info["adjustment"]
            composite_score = max(composite_score, 0)  # 不低于0

            final_list.append({
                **entry,
                "n_strategies": n_strategies,
                "all_signals": list(set(entry["all_signals"])),
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

    def _quick_sell_scan(self, code: str) -> dict:
        """
        卖出信号快速扫描：利用已缓存的K线检测卖出信号
        不发任何新网络请求——仅读取内存缓存
        
        Returns:
            dict: detect_sell_signals() 的返回值
        """
        try:
            df = market_scanner.get_history(code, days=60)
            if df is None or df.empty or len(df) < 20:
                return {
                    "has_sell_signal": False,
                    "sell_signals": [],
                    "sell_score": 0,
                    "stop_loss_price": None,
                    "take_profit_price": None,
                    "risk_level": "unknown",
                }
            
            sell_info = detect_sell_signals(df)
            return sell_info
            
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

    def _quick_buy_risk_assess(self, code: str) -> dict:
        """
        买入风险评估：基于卖出信号评估买入风险
        
        Returns:
            dict: assess_buy_risk() 的返回值
        """
        try:
            df = market_scanner.get_history(code, days=60)
            if df is None or df.empty or len(df) < 20:
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
            self.download_data(force_refresh=force_refresh, progress_callback=progress_callback)
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
            stock_list = self._load_stock_list()
            _t1 = datetime.now()
            self._precalc(stock_list, progress_callback=progress_callback)
            logger.info(f"[{datetime.now()}] 阶段1.5完成, 耗时: {datetime.now()-_t1}")
        finally:
            market_scanner._include_realtime = _orig_include

        # 阶段2：串行运行策略（每个策略内部已并行）
        _t2 = datetime.now()
        # 包装回调：screen_strategies 内部的 done 不代表整个流程完成
        _wrapped_cb = None
        if progress_callback:
            def _wrapped_cb(phase, current, idx, total):
                if phase != "done":
                    progress_callback(phase, current, idx, total)
        results = self.screen_strategies(
            strategies,
            progress_callback=_wrapped_cb,
            on_strategy_done=on_strategy_done,
        )["results"]

        # 阶段3: 结果合并中
        self._set_progress("merging", "正在合并结果...", 0, 100)
        if progress_callback:
            progress_callback("merging", "正在合并结果...", 0, 100)
        
        # 获取大盘趋势
        logger.info("[大盘过滤器] 正在判断大盘趋势...")
        market_trend = get_market_trend(market_scanner)
        market_strength = get_market_trend_strength(market_scanner)
        logger.info(f"[大盘过滤器] 大盘趋势: {market_trend}, 强度: {market_strength:.2f}")
        
        merged = self.merge_results(
            results, 
            top_n=top_n,
            market_trend=market_trend,
            market_strength=market_strength
        )
        self._set_progress("merging", "结果合并完成", 100, 100)
        if progress_callback:
            progress_callback("merging", "结果合并完成", 100, 100)
        # 最终 done：整个流程真正完成
        if progress_callback:
            progress_callback("done", "筛选完成", len(strategies), len(strategies))

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
