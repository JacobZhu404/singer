"""全策略快验回测（抽样股票池）

新成本（0.127%）+ 满历史 cache 下，先用股票子样本跑全 16 策略 × 52 周，
确认 α 排名结构方向（尤其 chanlun_strict 是否仍垫底），再决定要不要全量重跑。

抽样只缩小 universe、保留完整时间窗口与全部策略，因此跨期 α 画像可代表性地比较；
横截面策略（momentum/reversal/rps）在子样本内排名，方向仍可读，绝对值略有偏差。

用法： PYTHONPATH=/Users/jacob/personal .venv/bin/python tools/quick_backtest_sample.py [sample_n]
"""

from __future__ import annotations
import random
import sys
import time

from stock_screener.backtest import backtest_engine as bt
from stock_screener.strategies.registry import STRATEGY_REGISTRY


def main():
    sample_n = int(sys.argv[1]) if len(sys.argv) > 1 else 800

    full_names = bt._load_stock_names()
    codes = sorted(c for c in full_names if "ST" not in full_names[c] and "退" not in full_names[c])
    random.seed(42)
    sample = set(random.sample(codes, min(sample_n, len(codes))))
    sampled_map = {c: full_names[c] for c in sample}

    # monkeypatch：把 universe 限制到子样本（run() 内部从 _load_stock_names 取全集）
    bt._load_stock_names = lambda: sampled_map

    print(f"💾 抽样 universe: {len(sampled_map)} 只（seed=42）| 成本: 双边 {bt.ROUND_TRIP_COST_PCT:.3f}%")
    print(f"🎯 策略: {len(STRATEGY_REGISTRY)} 个 | 窗口: 52 周")

    t0 = time.perf_counter()
    engine = bt.BacktestEngine(weeks=52)
    results = engine.run(top_n=10, filter_sell=True, resume=False)
    bt.print_report(results)
    path = bt.save_results(results)
    print(f"\n📝 JSON → {path}")
    print(f"   total: {time.perf_counter()-t0:.1f}s")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
