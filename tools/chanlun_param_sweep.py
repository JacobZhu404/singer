#!/usr/bin/env python3
"""chanlun_strict 参数扫描 — 决定重构/弃用

背景：52 周回测里 chanlun_strict 是全场最弱（α(10d)=-0.09%、α(30d)=+1.49%、
胜率 42.2%）。在投票"重构/弃用"前，先证伪一个简单假设——是不是参数收得太死
（score>=65, divergence DIF tolerance=0.90）卡掉了真信号？

方法：扫 min_score ∈ {50,60,65,70,75,80}，divergence tolerance 保持默认 0.90。
每个 variant 跑独立 52 周单策略回测，对比 per-horizon α 矩阵。

判定准则：
  - 若某 variant 把 α(30d) 拉到 +3.5% 以上（接近全策略中位数 +4.6%），保留并调参；
  - 若全部 variant 的 α 曲线形状一致（仅笔数变），说明问题在选股逻辑而非阈值，
    弃用走向确认。

进程池注意：用 env CHANLUN_MIN_SCORE 而非类属性，spawn 子进程会重新 import
模块时读取 env；类属性 mutation 不跨进程传播。
"""

from __future__ import annotations
import os
import sys
import json
import time
import argparse
import logging
from datetime import datetime
from typing import Dict, List

# 顶端注入路径，确保 stock_screener.* 可 import（与 backtest_quick.py 一致）
sys.path.insert(0, "/Users/jacob/personal")
sys.path.insert(0, "/Users/jacob/personal/stock_screener")


def run_variant(min_score: int, weeks: int, top_n: int, max_workers: int,
                use_processes: bool) -> Dict:
    """跑一个 variant，返回该 variant 的 period_stats dict。"""
    # 设 env 让 spawn 子进程 import chanlun_strict 时读到
    os.environ["CHANLUN_MIN_SCORE"] = str(min_score)

    # 父进程已 import 的模块也同步 patch（在主进程做 sanity 检查的代码会用到）
    from stock_screener.strategies import chanlun_strict as cs
    cs._MIN_SCORE = min_score

    # 延迟 import，避免顶层 import 时 _MIN_SCORE 被默认值锁住影响后续 worker
    from stock_screener.backtest.backtest_engine import BacktestEngine

    print(f"\n{'='*60}")
    print(f"variant min_score={min_score}  weeks={weeks}  top_n={top_n}")
    print(f"{'='*60}")
    t0 = time.time()
    engine = BacktestEngine(weeks=weeks)
    results = engine.run(
        strategy_names=["chanlun_strict"],
        top_n=top_n,
        filter_sell=True,
        max_workers=max_workers,
        use_processes=use_processes,
        niceness=10,
        resume=False,
    )
    elapsed = time.time() - t0
    r = results["chanlun_strict"]
    payload = {
        "min_score": min_score,
        "total_trades": r.total_trades,
        "period_stats": {
            str(p): {
                "win_rate": st.win_rate,
                "avg_return": st.avg_return,
                "alpha": st.alpha,
                "benchmark_return": st.benchmark_return,
                "total": st.total,
            }
            for p, st in r.period_stats.items()
        },
        "elapsed_sec": round(elapsed, 1),
    }
    print(f"min_score={min_score} 完成: total_trades={r.total_trades}, "
          f"耗时 {elapsed:.0f}s")
    for p, st in sorted(r.period_stats.items()):
        print(f"  {p:>3}d: win={st.win_rate*100:>5.1f}%  ret={st.avg_return:>+6.2f}%  "
              f"alpha={st.alpha:>+6.2f}%  n={st.total}")
    return payload


def print_summary(variants: List[Dict]):
    """打印 variant 对比表。"""
    print("\n\n" + "="*78)
    print("CHANLUN_STRICT 参数扫描汇总")
    print("="*78)
    header = f"{'min_score':>10} {'trades':>8}"
    for p in (2, 5, 10, 30):
        header += f" {'α('+str(p)+'d)':>9}"
    for p in (2, 5, 10, 30):
        header += f" {'win('+str(p)+'d)':>9}"
    print(header)
    print("-" * len(header))
    for v in variants:
        line = f"{v['min_score']:>10} {v['total_trades']:>8}"
        for p in ("2", "5", "10", "30"):
            st = v["period_stats"].get(p, {})
            a = st.get("alpha")
            line += f" {a:>+8.2f}%" if a is not None else f" {'—':>9}"
        for p in ("2", "5", "10", "30"):
            st = v["period_stats"].get(p, {})
            w = st.get("win_rate")
            line += f" {w*100:>8.1f}%" if w is not None else f" {'—':>9}"
        print(line)
    print("="*78)
    print("基线（生产 min_score=65 @ 52 周）: α(2d)=+0.70 α(5d)=+0.16 "
          "α(10d)=-0.09 α(30d)=+1.49 win(30d)=42.2%")
    print("="*78)


def main():
    parser = argparse.ArgumentParser(description="chanlun_strict 参数扫描")
    parser.add_argument("--min-scores", type=str, default="50,60,65,70,75,80",
                        help="逗号分隔的 min_score 列表")
    parser.add_argument("--weeks", type=int, default=52, help="回测周数")
    parser.add_argument("--top-n", type=int, default=10, help="每日 topN")
    parser.add_argument("--max-workers", type=int, default=None,
                        help="进程数；默认 ~60%% 内核")
    parser.add_argument("--threads", action="store_true",
                        help="用线程而非进程（调试用，慢）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    min_scores = [int(x) for x in args.min_scores.split(",")]

    all_results: List[Dict] = []
    for ms in min_scores:
        try:
            payload = run_variant(
                min_score=ms,
                weeks=args.weeks,
                top_n=args.top_n,
                max_workers=args.max_workers,
                use_processes=not args.threads,
            )
            all_results.append(payload)
        except KeyboardInterrupt:
            print(f"\n[INTERRUPTED] 已完成 {len(all_results)} 个 variant，"
                  f"保存部分结果后退出")
            break

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "backtest", "results")
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"chanlun_sweep_{ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"weeks": args.weeks, "top_n": args.top_n,
                   "variants": all_results}, f, ensure_ascii=False, indent=2)
    print(f"\n落盘: {out_path}")

    print_summary(all_results)


if __name__ == "__main__":
    main()
