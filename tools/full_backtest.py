"""全 universe 全策略 52 周回测（权威口径）

新成本（0.127%）+ 满历史 cache 下跑全部注册策略、全市场股票池、52 周窗口，
产出权威 α 矩阵，用于定夺稀疏策略去留 + bollinger_lower_bounce 优化/弃用。

与 `quick_backtest_sample.py` 的区别：不抽样、不 monkeypatch universe。
开启 resume=True（按交易日 checkpoint），中断可续跑。

用法： PYTHONPATH=/Users/jacob/personal .venv/bin/python tools/full_backtest.py
"""

from __future__ import annotations
import time

from stock_screener.backtest import backtest_engine as bt
from stock_screener.strategies.registry import STRATEGY_REGISTRY


def main():
    print(f"💾 全 universe | 成本: 双边 {bt.ROUND_TRIP_COST_PCT:.3f}% | "
          f"策略: {len(STRATEGY_REGISTRY)} 个 | 窗口: 52 周")
    t0 = time.perf_counter()
    engine = bt.BacktestEngine(weeks=52)
    results = engine.run(top_n=10, filter_sell=True, resume=True)
    bt.print_report(results)
    path = bt.save_results(results)
    print(f"\n📝 JSON → {path}")
    print(f"   total: {time.perf_counter()-t0:.1f}s")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
