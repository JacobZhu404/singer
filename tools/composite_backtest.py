"""组合（加权排名）回测——按"最终使用场景"评价系统，而非孤立评价单策略。

动机
----
`tools/full_backtest.py` 是**逐策略**回测：每个交易日把每个策略各自的 Top-N 选股
拿去算未来收益。但线上真正交付给用户的不是某个策略的 Top-N，而是
`ScreenEngine.merge_results` 把全部策略的命中**加权合并后**的综合 Top-N。本工具回测
的就是这条综合链路：每个历史交易日按线上排名口径选出综合 Top-N，再看它们的未来
收益/α，从而回答"系统按真实用法选出来的票，到底赚不赚钱、跑没跑赢基准"。

与 merge_results 的口径差异（重要，PIT 安全性所致）
------------------------------------------------
`merge_results` 的综合分里掺了三类**非时点(non-PIT)**修正项：轻量风险扫描、卖出
信号、基本面(PE/PB)、大盘强度——它们都走**实时** `market_scanner`/最新基本面，
在历史某天回测时拿不到当时的值（会引入未来信息且极慢）。本工具因此只复刻
merge_results 里**决定选股的确定性核心**，逐项对应：

  1. 单策略内 `_build_result` 重打分： score = raw*0.6 + (1-rank_pct)*40，截断 Top-300。
  2. 单策略贡献： raw_contrib = score*weight*win_factor + (1-rank_pct)*RANK_BONUS_MAX，
     其中 win_factor = WIN_FACTOR_BASE + WIN_FACTOR_SLOPE*base_win_rate（实测胜率加权）。
  3. 组内去重：按策略 group 收集贡献，每组「头部(最高)全额 + 其余 ×INTRA_GROUP_DECAY」，
     再跨组累加得 weighted_score（避免同源雷同策略重复加分）。
  4. 综合分（仅 PIT 安全子集）：
        composite = weighted_score*0.5 + (1-avg_rank_pct)*20 + n_groups*5
     —— 丢弃 risk_adjustment / fundamental_adj / market_strength_adj（非 PIT 或中性=0）。
  5. 质量门槛 min_single_score / min_weighted_score 与线上一致。
  6. 排序键 (n_groups, composite_score) 降序，取 Top-N——与 merge_results 完全一致。

权重取自 `STRATEGY_REGISTRY[*]["weight"]`（即 derive_weights.py 推导并写回的那套）。
未来收益与基准 α 完全复用 backtest_engine 的口径（T+1 开盘入场、双边成本、跌停顺延、
中证1000 基准）——所以个股层面与 full_backtest 完全可比，唯一变量是"选股口径"。

用法
----
  # 短窗口先验证（便宜，几分钟）：
  PYTHONPATH=/Users/jacob/personal .venv/bin/python tools/composite_backtest.py --weeks 8
  # 全量 52 周（与 full_backtest 同量级，较慢）：
  PYTHONPATH=/Users/jacob/personal .venv/bin/python tools/composite_backtest.py --weeks 52
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from typing import Dict, List, Tuple

import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor

from stock_screener.strategies.registry import STRATEGY_REGISTRY
from stock_screener.core.constants import (
    MAX_HITS_PER_STRATEGY,
    MIN_SINGLE_SCORE,
    MIN_WEIGHTED_SCORE,
    RANK_BONUS_MAX,
    HOLD_PERIODS,
    INTRA_GROUP_DECAY,
    WIN_FACTOR_BASE,
    WIN_FACTOR_SLOPE,
)
from stock_screener.backtest import backtest_engine as bt
from stock_screener.backtest.backtest_engine import (
    BacktestEngine,
    BacktestResult,
    BacktestTrade,
    _pool_init,
    _pool_worker,
    _benchmark_period_returns,
    _calc_stats,
    _load_stock_names,
    _CACHE_DIR,
)

# merge_results 综合分权重（PIT 安全子集，见模块头）。与 engine._ScoreWeights 对齐。
W_WEIGHTED = 0.5
W_RANK = 20.0
W_CONSENSUS = 5.0

COMPOSITE_KEY = "__composite__"

_RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "backtest", "results")


def _rescore_strategy(hits: List[Tuple[str, float]]) -> List[Tuple[str, float]]:
    """复刻 BaseStrategy._build_result 的排名百分位重打分。

    hits: 该策略当日全部命中 (code, raw_score)。raw_score 已是 _eval_trade 里
    min(sig.score, 100) 的原始分。返回 (code, rescored_score)，按新分降序，
    并按 MAX_HITS_PER_STRATEGY 截断（与线上一致）。
    """
    ranked = sorted(hits, key=lambda x: x[1], reverse=True)[:MAX_HITS_PER_STRATEGY]
    total = len(ranked)
    out: List[Tuple[str, float]] = []
    for rank, (code, raw) in enumerate(ranked, 1):
        rank_pct = (rank - 1) / total if total > 1 else 0.0
        out.append((code, round(raw * 0.6 + (1 - rank_pct) * 40, 1)))
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def _compose_one_date(
    per_strategy_hits: Dict[str, List[Tuple[str, float]]],
    weights: Dict[str, float],
    win_factors: Dict[str, float],
    groups: Dict[str, str],
    min_single: int,
    min_weighted: int,
    top_n: int,
) -> List[Tuple[str, int, float]]:
    """复刻 merge_results 的确定性核心，返回当日综合 Top-N： (code, n_groups, composite)。

    与 engine.merge_results 严格对齐（2026-06-30 同步）：
      - 单策略贡献 raw_contrib = score*weight*win_factor + rank_bonus
      - 组内去重：每组「头部(最高分)全额 + 其余 ×INTRA_GROUP_DECAY」（与命中顺序无关）
      - 综合分共识项与排序键均用跨组命中数 n_groups
    """
    # 1+2: 各策略内重打分 → 按 (code, group) 收集 raw_contrib
    merged: Dict[str, dict] = {}
    for strat, hits in per_strategy_hits.items():
        if not hits:
            continue
        weight = float(weights.get(strat, 1.0))
        win_factor = float(win_factors.get(strat, 1.0))
        group = groups.get(strat, "其他")
        scored = _rescore_strategy(hits)
        total = len(scored)
        for rank, (code, score) in enumerate(scored, 1):
            if score < min_single:          # 质量门槛1：单策略原始分
                continue
            rank_pct = rank / total if total > 0 else 1.0
            rank_bonus = (1 - rank_pct) * RANK_BONUS_MAX
            raw_contrib = score * weight * win_factor + rank_bonus
            e = merged.setdefault(code, {"group_contribs": {}, "rank_sum": 0.0, "n": 0})
            e["group_contribs"].setdefault(group, []).append(raw_contrib)
            e["rank_sum"] += rank_pct
            e["n"] += 1

    # 3: 组内去重结算 → 综合分（PIT 子集）+ 质量门槛2
    final: List[Tuple[str, int, float]] = []
    for code, e in merged.items():
        weighted = 0.0
        for contribs in e["group_contribs"].values():
            contribs.sort(reverse=True)
            weighted += contribs[0] + INTRA_GROUP_DECAY * sum(contribs[1:])
        if weighted < min_weighted:          # 质量门槛2：加权总分
            continue
        n = e["n"]
        n_groups = len(e["group_contribs"])
        avg_rank_pct = e["rank_sum"] / n if n else 1.0
        composite = weighted * W_WEIGHTED + (1 - avg_rank_pct) * W_RANK + n_groups * W_CONSENSUS
        final.append((code, n_groups, max(composite, 0.0)))

    # 5: 排序键 (n_groups, composite) 降序，取 Top-N
    final.sort(key=lambda x: (x[1], x[2]), reverse=True)
    return final[:top_n]


def run_composite(
    weeks: int,
    top_n: int = 10,
    filter_sell: bool = True,
    max_workers: int | None = None,
    niceness: int = 10,
    min_single: int = MIN_SINGLE_SCORE,
    min_weighted: int = MIN_WEIGHTED_SCORE,
) -> Tuple[Dict[str, BacktestResult], dict]:
    """跑组合回测。返回 (results, meta)。

    results 含 COMPOSITE_KEY 的组合结果 + 每个策略 filter 后 Top-N 的逐策略结果
    （同一次扫描产出，口径一致，便于并排对比）。
    """
    strategy_names = list(STRATEGY_REGISTRY.keys())
    weights = {s: float(STRATEGY_REGISTRY[s].get("weight", 1.0)) for s in strategy_names}
    groups = {s: STRATEGY_REGISTRY[s].get("group", "其他") for s in strategy_names}
    # win_factor 与 engine.merge_results 同口径：base + slope*base_win_rate（取自策略类）
    win_factors = {}
    for s in strategy_names:
        cls = STRATEGY_REGISTRY[s].get("cls")
        base_wr = getattr(cls, "base_win_rate", 0.5) if cls else 0.5
        win_factors[s] = WIN_FACTOR_BASE + WIN_FACTOR_SLOPE * base_wr

    engine = BacktestEngine(weeks=weeks)
    if max_workers is None:
        max_workers = engine._default_workers()

    trade_dates = engine._get_trade_dates()
    name_map = _load_stock_names()

    import glob as _glob
    csv_files = _glob.glob(os.path.join(_CACHE_DIR, "*.csv"))
    cached = {os.path.splitext(os.path.basename(f))[0] for f in csv_files}
    if name_map:
        all_codes = [(c, n) for c, n in name_map.items()
                     if c in cached and "ST" not in n and "退" not in n]
    else:
        all_codes = [(c, c) for c in sorted(cached)]

    print(f"📊 组合回测: {len(trade_dates)} 个交易日 × {len(strategy_names)} 个策略 × "
          f"{len(all_codes)} 只股票 | workers={max_workers} | top_n={top_n} "
          f"| filter_sell={filter_sell}")
    if not trade_dates or not all_codes:
        raise SystemExit("无交易日或无 universe，检查 data/cache 与 stocks.json")

    results = {s: BacktestResult(strategy=s) for s in strategy_names}
    results[COMPOSITE_KEY] = BacktestResult(strategy=COMPOSITE_KEY)

    chunk = max(100, len(all_codes) // (max_workers * 4))
    code_chunks = [all_codes[i:i + chunk] for i in range(0, len(all_codes), chunk)]
    ctx = mp.get_context("spawn")
    full_codes = [c for c, _ in all_codes]

    with ProcessPoolExecutor(
        max_workers=max_workers,
        mp_context=ctx,
        initializer=_pool_init,
        initargs=(strategy_names, top_n, niceness),
    ) as pool:
        for di, trade_date in enumerate(trade_dates):
            tasks = [(trade_date, s, ch, full_codes)
                     for s in strategy_names for ch in code_chunks]
            per_strategy_trades: Dict[str, List[BacktestTrade]] = {s: [] for s in strategy_names}
            for trades in pool.map(_pool_worker, tasks):
                for t in trades:
                    per_strategy_trades[t.strategy].append(t)

            # 当日 (code) -> 任一 trade，用于查未来收益（收益只依赖 code+date，跨策略一致）
            returns_by_code: Dict[str, BacktestTrade] = {}
            per_strategy_hits: Dict[str, List[Tuple[str, float]]] = {}
            for s in strategy_names:
                tl = per_strategy_trades[s]
                if filter_sell:
                    tl = [t for t in tl if not t.has_risk]
                # 逐策略 Top-N（对照组，复刻 full_backtest 口径）
                tl_sorted = sorted(tl, key=lambda t: t.score, reverse=True)
                results[s].trades.extend(tl_sorted[:top_n])
                # 组合候选：保留全部命中（线上 merge 不在合并前按 Top-N 截断，仅 _build_result 截 300）
                per_strategy_hits[s] = [(t.code, t.score) for t in tl]
                for t in tl:
                    returns_by_code.setdefault(t.code, t)

            picks = _compose_one_date(per_strategy_hits, weights, win_factors, groups,
                                      min_single, min_weighted, top_n)
            for code, n_strat, composite in picks:
                base = returns_by_code.get(code)
                if base is None:
                    continue
                results[COMPOSITE_KEY].trades.append(BacktestTrade(
                    buy_date=trade_date,
                    code=code,
                    name=base.name,
                    strategy=COMPOSITE_KEY,
                    buy_price=base.buy_price,
                    score=round(composite, 1),
                    signals=[f"n_strat={n_strat}"],
                    has_risk=base.has_risk,
                    returns=base.returns,
                    exit_prices=base.exit_prices,
                    max_drawdowns=base.max_drawdowns,
                ))
            print(f"  {di+1}/{len(trade_dates)} {trade_date} | "
                  f"组合命中 {len(picks)} | 候选池 {len(returns_by_code)}")

    bench = _benchmark_period_returns(trade_dates)
    for r in results.values():
        _calc_stats(r, bench)

    meta = {
        "kind": "composite_backtest",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "weeks": weeks,
        "trade_dates": len(trade_dates),
        "universe": len(all_codes),
        "top_n": top_n,
        "filter_sell": filter_sell,
        "min_single_score": min_single,
        "min_weighted_score": min_weighted,
        "round_trip_cost_pct": round(bt.ROUND_TRIP_COST_PCT, 5),
        "benchmark_code": bt.BENCHMARK_CODE,
        "weights": weights,
    }
    return results, meta


def _print_table(results: Dict[str, BacktestResult], period: int) -> None:
    print(f"\n══ 持有期 {period} 日 | 组合 vs 逐策略（α 已扣 {bt.ROUND_TRIP_COST_PCT:.3f}% 双边成本）══")
    print(f"{'strategy':22s}{'n':>6}{'win%':>8}{'avgRet':>9}{'bench':>8}{'alpha':>8}")
    print("─" * 61)

    def row(name: str):
        r = results.get(name)
        ps = r.period_stats.get(period) if r else None
        if not ps or ps.total == 0:
            print(f"{name:22s}{'-':>6}{'-':>8}{'-':>9}{'-':>8}{'-':>8}")
            return
        print(f"{name:22s}{ps.total:6d}{ps.win_rate*100:7.1f}%"
              f"{ps.avg_return:8.2f}%{ps.benchmark_return:7.2f}%{ps.alpha:+7.2f}%")

    row(COMPOSITE_KEY)
    print("─" * 61)
    indiv = sorted(
        (s for s in results if s != COMPOSITE_KEY),
        key=lambda s: -(results[s].period_stats.get(period).alpha
                        if results[s].period_stats.get(period) else -999),
    )
    for s in indiv:
        row(s)


def _save(results: Dict[str, BacktestResult], meta: dict) -> str:
    os.makedirs(_RESULTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(_RESULTS_DIR, f"composite_backtest_{stamp}.json")
    payload = {"__meta__": meta}
    for name, r in results.items():
        payload[name] = {
            "total_trades": r.total_trades,
            "period_stats": {
                str(p): {
                    "total": ps.total,
                    "win_rate": round(ps.win_rate, 4),
                    "avg_return": round(ps.avg_return, 4),
                    "avg_drawdown": round(ps.avg_drawdown, 4),
                    "benchmark_return": round(ps.benchmark_return, 4),
                    "alpha": round(ps.alpha, 4),
                }
                for p, ps in r.period_stats.items()
            },
        }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def main():
    ap = argparse.ArgumentParser(description="组合(加权排名)回测：按线上 merge_results 口径评价系统")
    ap.add_argument("--weeks", type=int, default=8, help="回测周数（先用 8 周验证，再上 52）")
    ap.add_argument("--top-n", type=int, default=10, help="每日综合 Top-N（也用于逐策略对照）")
    ap.add_argument("--workers", type=int, default=None, help="并行进程数（默认保守 ~60%% 内核）")
    ap.add_argument("--no-filter-sell", action="store_true", help="不过滤 danger 卖出信号")
    args = ap.parse_args()

    results, meta = run_composite(
        weeks=args.weeks,
        top_n=args.top_n,
        filter_sell=not args.no_filter_sell,
        max_workers=args.workers,
    )

    for p in HOLD_PERIODS:
        _print_table(results, p)

    path = _save(results, meta)
    print(f"\n✅ 已保存: {os.path.relpath(path)}")
    c = results[COMPOSITE_KEY]
    print(f"   组合总交易 {c.total_trades} 笔；逐持有期 α 见上表 __composite__ 行。")


if __name__ == "__main__":
    main()
