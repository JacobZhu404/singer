# -*- coding: utf-8 -*-
"""
回测引擎 - 支持所有策略和缠论策略的历史回测

功能：
- 从本地 K 线缓存读取数据（无需网络）
- 支持任意持有期（默认 2/5/10/30 天）
- 每日 Top-N 选股，统计胜率、平均收益、最大回撤
- 支持卖出信号过滤（danger 级别风险标志）
"""

import os
import sys
import json
import glob
import logging
import threading
import multiprocessing as mp
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from stock_screener.strategies.registry import STRATEGY_REGISTRY, get_strategy
from stock_screener.backtest.pit_scanner import PointInTimeScanner

logger = logging.getLogger(__name__)

# 使用 constants.py 中的配置（如果可用，否则回退到默认值）
try:
    from stock_screener.core.constants import (
        HOLD_PERIODS as _HP,
        BACKTEST_SCORE_THRESHOLD as _ST,
        BACKTEST_SLIPPAGE_PCT as _SL,
        BACKTEST_COMMISSION_PCT as _CM,
        BACKTEST_BENCHMARK_CODE as _BC,
    )
    HOLD_PERIODS = _HP
    SCORE_THRESHOLD = _ST
    SLIPPAGE_PCT = _SL
    COMMISSION_PCT = _CM
    BENCHMARK_CODE = _BC
except ImportError:
    HOLD_PERIODS = [2, 5, 10, 30]
    SCORE_THRESHOLD = 40
    SLIPPAGE_PCT = 0.0010
    COMMISSION_PCT = 0.0003
    BENCHMARK_CODE = "000001"

# 双边交易成本：买入 +slip +comm，卖出 -slip -comm
ROUND_TRIP_COST_PCT = (SLIPPAGE_PCT + COMMISSION_PCT) * 2 * 100

# 缓存目录（本地 CSV 文件）
_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "cache", "klines"
)


# ─── 数据类 ──────────────────────────────────────────────────────────────────

@dataclass
class BacktestTrade:
    """单次回测交易记录"""
    buy_date: str
    code: str
    name: str
    strategy: str
    buy_price: float
    score: float
    signals: List[str]
    has_risk: bool = False
    returns: Dict[int, float] = field(default_factory=dict)        # {period: return_pct}
    exit_prices: Dict[int, float] = field(default_factory=dict)
    max_drawdowns: Dict[int, float] = field(default_factory=dict)


@dataclass
class PeriodStats:
    """单个持有期的统计结果"""
    period: int
    total: int = 0
    wins: int = 0
    avg_return: float = 0.0
    avg_drawdown: float = 0.0
    win_rate: float = 0.0
    benchmark_return: float = 0.0  # 同期 benchmark 平均收益（已扣成本）
    alpha: float = 0.0              # avg_return - benchmark_return


@dataclass
class BacktestResult:
    """策略回测汇总"""
    strategy: str
    total_trades: int = 0
    period_stats: Dict[int, PeriodStats] = field(default_factory=dict)
    trades: List[BacktestTrade] = field(default_factory=list)


def _benchmark_period_returns(trade_dates: List[str]) -> Dict[int, Dict[str, float]]:
    """
    为每个 trade_date 计算基准（沪深300 ETF）在各持有期的收益（已扣成本）。
    Returns: {period: {trade_date: pct_return}}
    """
    df = _load_cached_df(BENCHMARK_CODE)
    if df.empty:
        # 尝试通过 data_layer 下载并入缓存
        try:
            from stock_screener.data.data_layer import data_fetcher
            df = data_fetcher.get_kline(BENCHMARK_CODE, days=400)
            if df.empty:
                from stock_screener.data.fetcher import get_stock_history
                df = get_stock_history(BENCHMARK_CODE, days=400)
        except Exception as e:
            logger.warning(f"基准 {BENCHMARK_CODE} 下载失败: {e}")
            return {p: {} for p in HOLD_PERIODS}
    if df.empty:
        logger.warning(f"基准 {BENCHMARK_CODE} 无数据，alpha 将退化为 avg_return")
        return {p: {} for p in HOLD_PERIODS}

    df["date_str"] = df["date"].dt.strftime("%Y%m%d")
    out = {p: {} for p in HOLD_PERIODS}
    for td in trade_dates:
        if td not in df["date_str"].values:
            continue
        idx = df[df["date_str"] == td].index[0]
        entry = float(df.iloc[idx]["close"])
        for period in HOLD_PERIODS:
            end = idx + period
            if end >= len(df):
                continue
            exit_p = float(df.iloc[end]["close"])
            gross = (exit_p - entry) / entry * 100
            out[period][td] = gross - ROUND_TRIP_COST_PCT
    return out


# ─── 工具函数 ─────────────────────────────────────────────────────────────────

def _load_stock_names() -> Dict[str, str]:
    """从 stocks.json 加载 代码→名称 映射

    兼容新（ts_code/name）和旧（代码/名称）两种 schema。
    """
    path = os.path.join(os.path.dirname(_CACHE_DIR), "stocks.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        out: Dict[str, str] = {}
        for item in data:
            code = item.get("ts_code") or item.get("代码")
            name = item.get("name") or item.get("名称")
            if code:
                out[str(code)] = str(name or "")
        return out
    except Exception as e:
        logger.warning(f"加载 stocks.json 失败: {e}")
        return {}


def _load_cached_df(code: str) -> pd.DataFrame:
    """从本地缓存读取 K 线"""
    path = os.path.join(_CACHE_DIR, f"{code}.csv")
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, encoding="utf-8")
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)
    except Exception as e:
        logger.warning(f"读取缓存 {code}.csv 失败: {e}")
        return pd.DataFrame()


def _calc_future_returns(df: pd.DataFrame, entry_idx: int) -> tuple:
    """
    计算未来各持有期收益和最大回撤
    Returns: (returns_dict, exit_prices_dict, drawdowns_dict)
    """
    entry_price = float(df.iloc[entry_idx]["close"])
    returns, exits, drawdowns = {}, {}, {}

    for period in HOLD_PERIODS:
        end_idx = entry_idx + period
        if end_idx >= len(df):
            continue
        prices = df.iloc[entry_idx + 1: end_idx + 1]["close"].values.astype(float)
        if len(prices) == 0:
            continue
        exit_p = prices[-1]
        exits[period] = exit_p
        gross_pct = (exit_p - entry_price) / entry_price * 100
        # 扣除双边滑点 + 手续费
        returns[period] = gross_pct - ROUND_TRIP_COST_PCT
        # 最大回撤
        peak = entry_price
        max_dd = 0.0
        for p in prices:
            if p > peak:
                peak = p
            dd = (peak - p) / peak * 100
            if dd > max_dd:
                max_dd = dd
        drawdowns[period] = max_dd

    return returns, exits, drawdowns


def _check_sell_signal(risk_flags: list) -> bool:
    """是否有 danger 级别卖出信号"""
    danger_types = {
        "rsi_overbought", "macd_death_cross", "macd_top_div",
        "bollinger_upper", "td_sell", "ma_empty",
    }
    return any(
        f.get("level") == "danger" or f.get("type") in danger_types
        for f in risk_flags
    )


# ─── 单股评估（线程/进程路径共用，单一事实来源）──────────────────────────────

def _eval_trade(code: str, name: str, strategy_obj, trade_date: str,
                scanner: PointInTimeScanner, df: pd.DataFrame) -> Optional[BacktestTrade]:
    """用 as_of 时点 scanner 跑真实策略，再算未来收益。df 为该股完整 K 线。"""
    if df is None or df.empty or len(df) < 60:
        return None

    date_str = df["date"].dt.strftime("%Y%m%d")
    match = df.index[date_str == trade_date]
    if len(match) == 0:
        return None
    full_idx = int(match[0])
    if full_idx < 30:
        return None

    try:
        sig = strategy_obj._evaluate_single_stock(code, scanner, {code: name}, trade_date)
    except strategy_obj._SkipStock:
        return None
    except Exception as e:
        logger.debug(f"[{strategy_obj.name}] 回测评估失败 {code}: {e}")
        return None
    if sig is None or sig.score < SCORE_THRESHOLD:
        return None

    has_risk = _check_sell_signal(sig.risk_flags or [])
    rets, exits, dds = _calc_future_returns(df, full_idx)

    return BacktestTrade(
        buy_date=trade_date,
        code=code,
        name=name,
        strategy=strategy_obj.name,
        buy_price=float(df.iloc[full_idx]["close"]),
        score=min(sig.score, 100),
        signals=sig.signals,
        has_risk=has_risk,
        returns=rets,
        exit_prices=exits,
        max_drawdowns=dds,
    )


# ─── 进程池工作单元 ───────────────────────────────────────────────────────────
# 回测对每只股票的评估彼此独立（embarrassingly parallel），但策略逻辑是 CPU 密集
# 的纯 Python，受 GIL 限制下线程几乎无法并行。改用进程池实现真并行。
#
# 设计要点（为「不拖垮前台」服务）：
#   - 每进程一份 scanner/策略实例（threading.Lock 不可 pickle，故用 initializer 在
#     子进程内构造，而非跨进程传递）。
#   - 子进程 os.nice(+N) 降优先级：前台任务永远抢占 CPU，回测只吃空闲算力。
#   - 任务按「(日期, 策略, 一批股票)」分块，大幅降低 IPC（只回传命中的交易）。
#   - 用 spawn 上下文，规避 macOS 上 fork + numpy/Accelerate 的已知崩溃。

_WK: dict = {}  # 每个子进程的私有状态：scanner / 策略实例 / 当前 as_of 日期


def _pool_init(strategy_names: List[str], top_n: int, niceness: int):
    try:
        os.nice(niceness)  # 降低子进程优先级，保证前台交互流畅
    except Exception:
        pass
    logging.disable(logging.CRITICAL)  # 子进程内静音日志，避免多进程抢 stderr
    _WK["scanner"] = PointInTimeScanner()
    _WK["strats"] = {s: get_strategy(s, top_n=top_n) for s in strategy_names}
    _WK["cur_date"] = None


def _pool_worker(args: Tuple[str, str, list]) -> List[BacktestTrade]:
    trade_date, strategy_name, code_chunk = args
    scanner: PointInTimeScanner = _WK["scanner"]
    # 同一进程内换日才 set_as_of（清指标缓存）；同日多策略/多块共享缓存
    if _WK["cur_date"] != trade_date:
        scanner.set_as_of(trade_date)
        _WK["cur_date"] = trade_date
    strat = _WK["strats"][strategy_name]
    out: List[BacktestTrade] = []
    for code, name in code_chunk:
        t = _eval_trade(code, name, strat, trade_date, scanner, scanner._full_df(code))
        if t is not None:
            out.append(t)
    return out


# ─── 回测引擎 ─────────────────────────────────────────────────────────────────

class BacktestEngine:
    """
    历史回测引擎

    特点：
    - 直接从本地 CSV 缓存读取，无网络延迟
    - 支持所有策略（含缠论）
    - 每日 Top-N 选股，可选是否过滤卖出信号
    - 并发处理，速度快
    """

    def __init__(self, start_date: Optional[str] = None, end_date: Optional[str] = None,
                 weeks: Optional[int] = None):
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        if weeks is not None:
            start_date = (datetime.now() - timedelta(weeks=weeks)).strftime("%Y%m%d")
        elif start_date is None:
            start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")

        self.start_date = start_date
        self.end_date   = end_date
        self._cache: Dict[str, pd.DataFrame] = {}
        self._lock = threading.Lock()

    def _get_df(self, code: str) -> pd.DataFrame:
        with self._lock:
            if code in self._cache:
                return self._cache[code]
        df = _load_cached_df(code)
        if not df.empty:
            with self._lock:
                self._cache[code] = df
        return df

    def _get_trade_dates(self) -> List[str]:
        """从上证000001获取交易日（每周取周三）"""
        df = _load_cached_df("000001")
        if df.empty:
            # 尝试网络获取
            from stock_screener.data.fetcher import get_stock_history
            df = get_stock_history("000001", days=400)
        if df.empty:
            logger.error("无法获取交易日数据")
            return []

        df["date"] = pd.to_datetime(df["date"])
        start_dt = pd.to_datetime(self.start_date)
        end_dt   = pd.to_datetime(self.end_date)
        df = df[(df["date"] >= start_dt) & (df["date"] <= end_dt)]
        df["year_week"] = (df["date"].dt.isocalendar().year.astype(str) + "_" +
                           df["date"].dt.isocalendar().week.astype(str))
        df["weekday"] = df["date"].dt.weekday

        dates = []
        for _, grp in df.groupby("year_week"):
            wed = grp[grp["weekday"] == 2]
            if not wed.empty:
                dates.append(wed.iloc[0]["date"].strftime("%Y%m%d"))
            else:
                dates.append(grp.iloc[-1]["date"].strftime("%Y%m%d"))
        return sorted(dates)

    def _process_one(self, code: str, name: str, strategy_obj, trade_date: str,
                     scanner: PointInTimeScanner) -> Optional[BacktestTrade]:
        """处理单只股票（线程路径）：委托给共用的 _eval_trade。"""
        return _eval_trade(code, name, strategy_obj, trade_date, scanner, self._get_df(code))

    @staticmethod
    def _default_workers() -> int:
        """保守默认：约 60% 内核，至少留 2 个给前台任务，保证机器不卡。"""
        cpu = os.cpu_count() or 4
        return max(1, min(cpu - 2, int(cpu * 0.6)))

    def run(
        self,
        strategy_names: Optional[List[str]] = None,
        top_n: int = 10,
        filter_sell: bool = True,
        max_workers: Optional[int] = None,
        use_processes: bool = True,
        niceness: int = 10,
    ) -> Dict[str, BacktestResult]:
        """
        执行回测

        Args:
            strategy_names: 策略列表，None = 所有注册策略
            top_n: 每日每策略最多选股数
            filter_sell: 是否过滤危险风险标志
            max_workers: 并行度；None = 保守默认（约 60% 内核，至少留 2 个给前台）
            use_processes: True = 进程池真并行（推荐）；False/1 worker = 退回线程
            niceness: 子进程 nice 增量（越大优先级越低，越不抢前台 CPU）
        """
        if strategy_names is None:
            strategy_names = list(STRATEGY_REGISTRY.keys())
        if max_workers is None:
            max_workers = self._default_workers()

        trade_dates = self._get_trade_dates()
        name_map    = _load_stock_names()

        # 获取所有有缓存文件的股票代码
        csv_files = glob.glob(os.path.join(_CACHE_DIR, "*.csv"))
        all_codes = [(os.path.splitext(os.path.basename(f))[0]) for f in csv_files]
        all_codes = [(c, name_map.get(c, c)) for c in all_codes]

        logger.info(f"回测: {len(trade_dates)}个交易日 × {len(strategy_names)}个策略 × "
                    f"{len(all_codes)}只股票 | workers={max_workers} "
                    f"({'进程' if use_processes and max_workers > 1 else '线程'})")

        results = {s: BacktestResult(strategy=s) for s in strategy_names}

        if use_processes and max_workers > 1:
            self._run_processes(strategy_names, all_codes, trade_dates, top_n,
                                filter_sell, max_workers, niceness, results)
        else:
            self._run_threads(strategy_names, all_codes, trade_dates, top_n,
                              filter_sell, max_workers, results)

        bench = _benchmark_period_returns(trade_dates)
        for r in results.values():
            _calc_stats(r, bench)
        return results

    def _run_threads(self, strategy_names, all_codes, trade_dates, top_n,
                     filter_sell, max_workers, results):
        """线程路径（兼容/回退；CPU 密集下不真并行）。"""
        strategy_objs = {s: get_strategy(s, top_n=top_n) for s in strategy_names}
        scanner = PointInTimeScanner()
        for date_idx, trade_date in enumerate(trade_dates):
            logger.info(f"进度 {date_idx+1}/{len(trade_dates)} - {trade_date}")
            scanner.set_as_of(trade_date)
            for strategy in strategy_names:
                strat = strategy_objs[strategy]
                tasks = [(code, name, strat, trade_date, scanner) for code, name in all_codes]
                all_trades = []
                with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
                    for t in pool.map(lambda a: self._process_one(*a), tasks):
                        if t:
                            all_trades.append(t)
                if filter_sell:
                    all_trades = [t for t in all_trades if not t.has_risk]
                all_trades.sort(key=lambda t: t.score, reverse=True)
                results[strategy].trades.extend(all_trades[:top_n])

    def _run_processes(self, strategy_names, all_codes, trade_dates, top_n,
                       filter_sell, max_workers, niceness, results):
        """进程池路径：真并行 + 低优先级 + 分块降 IPC。"""
        # 按股票分块：任务数 = 日期 × 策略 × 块数；块越大 IPC 越少，内存峰值越高
        chunk = max(100, len(all_codes) // (max_workers * 4))
        code_chunks = [all_codes[i:i + chunk] for i in range(0, len(all_codes), chunk)]
        ctx = mp.get_context("spawn")  # 规避 macOS fork + numpy 崩溃

        with ProcessPoolExecutor(
            max_workers=max_workers,
            mp_context=ctx,
            initializer=_pool_init,
            initargs=(strategy_names, top_n, niceness),
        ) as pool:
            for date_idx, trade_date in enumerate(trade_dates):
                logger.info(f"进度 {date_idx+1}/{len(trade_dates)} - {trade_date}")
                tasks = [(trade_date, s, ch) for s in strategy_names for ch in code_chunks]
                per_strategy = {s: [] for s in strategy_names}
                for trades in pool.map(_pool_worker, tasks):
                    for t in trades:
                        per_strategy[t.strategy].append(t)
                for s in strategy_names:
                    tl = per_strategy[s]
                    if filter_sell:
                        tl = [t for t in tl if not t.has_risk]
                    tl.sort(key=lambda t: t.score, reverse=True)
                    results[s].trades.extend(tl[:top_n])


def _calc_stats(result: BacktestResult, benchmark: Optional[Dict[int, Dict[str, float]]] = None):
    """计算回测统计指标，附带 benchmark 同期收益与 alpha"""
    trades = result.trades
    result.total_trades = len(trades)
    if not trades:
        return

    benchmark = benchmark or {}
    for period in HOLD_PERIODS:
        period_trades = [t for t in trades if period in t.returns]
        rets = [t.returns[period] for t in period_trades]
        dds  = [t.max_drawdowns[period] for t in period_trades if period in t.max_drawdowns]
        if not rets:
            continue
        wins = sum(1 for r in rets if r > 0)
        avg_ret = float(np.mean(rets))
        # 与 benchmark 同期对照（按交易日匹配）
        bench_p = benchmark.get(period, {})
        bench_aligned = [bench_p[t.buy_date] for t in period_trades if t.buy_date in bench_p]
        bench_avg = float(np.mean(bench_aligned)) if bench_aligned else 0.0
        result.period_stats[period] = PeriodStats(
            period=period,
            total=len(rets),
            wins=wins,
            avg_return=avg_ret,
            avg_drawdown=float(np.mean(dds)) if dds else 0.0,
            win_rate=wins / len(rets) if rets else 0.0,
            benchmark_return=bench_avg,
            alpha=avg_ret - bench_avg,
        )


def print_report(results: Dict[str, BacktestResult]):
    """打印回测报告（已扣双边滑点+手续费 ≈ {:.2f}%，含 benchmark/alpha）""".format(ROUND_TRIP_COST_PCT)
    width = 24 + 9 + len(HOLD_PERIODS) * 36
    print("\n" + "=" * width)
    print(" " * (width // 2 - 12) + "📊 歌者策略回测报告")
    print(f"成本: 单边 slip={SLIPPAGE_PCT*100:.2f}% + comm={COMMISSION_PCT*100:.2f}% (双边 {ROUND_TRIP_COST_PCT:.2f}%)")
    print(f"基准: {BENCHMARK_CODE}（已扣同等成本）")
    print("=" * width)
    print(f"\n{'策略':<18} {'交易数':>7}", end="")
    for p in HOLD_PERIODS:
        print(f"  {p}日胜率  {p}日策略  {p}日基准  {p}日 α", end="")
    print()
    print("-" * width)

    for name, r in sorted(
        results.items(),
        key=lambda x: x[1].period_stats.get(10, PeriodStats(10)).alpha,
        reverse=True,
    ):
        meta = STRATEGY_REGISTRY.get(name, {})
        label = meta.get("name", name)
        print(f"{label:<18} {r.total_trades:>7}", end="")
        for p in HOLD_PERIODS:
            st = r.period_stats.get(p)
            if st:
                print(f"  {st.win_rate*100:>6.1f}%  {st.avg_return:>+7.2f}%  {st.benchmark_return:>+7.2f}%  {st.alpha:>+6.2f}%", end="")
            else:
                print(f"  {'N/A':>7}  {'N/A':>8}  {'N/A':>8}  {'N/A':>7}", end="")
        print()

    print("=" * width)


def save_results(results: Dict[str, BacktestResult], output_dir: str = None):
    """保存回测结果到 JSON 文件"""
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    os.makedirs(output_dir, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    data = {}
    for name, r in results.items():
        data[name] = {
            "total_trades": r.total_trades,
            "period_stats": {
                str(p): {
                    "win_rate": st.win_rate,
                    "avg_return": st.avg_return,
                    "avg_drawdown": st.avg_drawdown,
                    "benchmark_return": st.benchmark_return,
                    "alpha": st.alpha,
                    "total": st.total,
                }
                for p, st in r.period_stats.items()
            },
        }
    data["__meta__"] = {
        "slippage_pct": SLIPPAGE_PCT,
        "commission_pct": COMMISSION_PCT,
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
        "benchmark_code": BENCHMARK_CODE,
    }
    path = os.path.join(output_dir, f"backtest_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"结果已保存: {path}")
    return path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    engine = BacktestEngine(weeks=12)
    results = engine.run(top_n=10, filter_sell=True)
    print_report(results)
    save_results(results)
