"""
阶段 C 改动端到端验证

不依赖 web、不发新网络请求，直接读 data/cache/klines/*.csv 跑：
  1. right_side  命中数（旧 60 阈值 + 未突破基础分 vs 新 80 阈值 + 必须突破）
  2. chanlun_strict 单只耗时（lfilter 优化已生效）
  3. bollinger 双策略命中数（拆分后 lower_bounce vs breakout 各自独立）

用法： python3 -m stock_screener.tools.bench_phase_c [N]
N: 抽样股票数（默认 500，None 时全 5850）
"""

import os
import sys
import time
import random
import logging
from pathlib import Path

import pandas as pd

# 用 INFO 起步，但策略内部很多 logger.warning 噪声，过滤
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("stock_screener.utils.indicators").setLevel(logging.ERROR)

from stock_screener.utils.indicators import compute_indicator_bundle
from stock_screener.strategies.right_side import RightSideTradingStrategy
from stock_screener.strategies.chanlun_strict import ChanlunStrictStrategy
from stock_screener.strategies.bollinger_lower_bounce import BollingerLowerBounceStrategy
from stock_screener.strategies.bollinger_breakout import BollingerBreakoutStrategy
from stock_screener.strategies.volume_breakout import VolumeBreakoutStrategy


CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "cache" / "klines"


class _MockScanner:
    """最小化 scanner stub：满足策略所需的 get_indicators / get_history / get_realtime"""

    def __init__(self):
        self._indicator_cache = {}
        self._kline_cache = {}

    def get_history(self, code: str, days: int = 120) -> pd.DataFrame:
        path = CACHE_DIR / f"{code}.csv"
        if not path.exists():
            return None
        df = pd.read_csv(path)
        # 兼容列名差异
        if "trade_date" not in df.columns and "date" in df.columns:
            df = df.rename(columns={"date": "trade_date"})
        df = df.tail(days).reset_index(drop=True)
        return df

    def get_indicators(self, code: str, days: int = 120) -> dict:
        cache_key = f"{code}_{days}_False"
        if cache_key in self._indicator_cache:
            return self._indicator_cache[cache_key]
        df = self.get_history(code, days=days)
        if df is None or len(df) < 20:
            return {}
        bundle = compute_indicator_bundle(df)
        self._indicator_cache[cache_key] = bundle
        return bundle

    def get_realtime(self, code: str) -> dict:
        # 离线场景：用最后一根 K 线伪造 quote
        df = self.get_history(code, days=2)
        if df is None or df.empty:
            return {"涨跌幅": 0.0, "最新价": 0.0, "换手率": 0.0}
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else last
        prev_close = float(prev.get("close", 0)) or 1.0
        last_close = float(last.get("close", 0))
        pct = (last_close - prev_close) / prev_close * 100 if prev_close > 0 else 0.0
        return {"涨跌幅": pct, "最新价": last_close, "换手率": 0.0}


def main():
    sample_n = int(sys.argv[1]) if len(sys.argv) > 1 else 500

    all_codes = sorted(p.stem for p in CACHE_DIR.glob("*.csv"))
    print(f"💾 disk cache: {len(all_codes)} stocks")
    if sample_n < len(all_codes):
        random.seed(42)
        codes = random.sample(all_codes, sample_n)
        print(f"🎯 sampling {sample_n}")
    else:
        codes = all_codes
        print(f"🎯 full universe ({len(codes)})")

    scanner = _MockScanner()
    name_map = {c: c for c in codes}
    trade_date = "2026-06-23"

    bench_targets = [
        ("right_side", RightSideTradingStrategy()),
        ("chanlun_strict", ChanlunStrictStrategy()),
        ("bollinger_lower_bounce", BollingerLowerBounceStrategy()),
        ("bollinger_breakout", BollingerBreakoutStrategy()),
        ("volume_breakout", VolumeBreakoutStrategy()),
    ]

    results = {}
    for name, strat in bench_targets:
        hit = 0
        skipped = 0
        errored = 0
        t0 = time.perf_counter()
        for code in codes:
            try:
                sig = strat._evaluate_single_stock(code, scanner, name_map, trade_date)
                if sig is not None:
                    hit += 1
                else:
                    skipped += 1
            except strat._SkipStock:
                skipped += 1
            except Exception as e:
                errored += 1
                if errored <= 3:  # 前 3 个错误打印出来诊断
                    print(f"  ⚠ {name} {code}: {type(e).__name__}: {e}")
        elapsed = time.perf_counter() - t0
        per_stock_us = elapsed / len(codes) * 1e6
        results[name] = {
            "hit": hit,
            "skipped": skipped,
            "errored": errored,
            "elapsed_s": elapsed,
            "per_stock_us": per_stock_us,
        }
        print(f"  {name:24s}: hit={hit:4d}  skip={skipped:4d}  err={errored:3d}  total={elapsed:6.2f}s  per={per_stock_us:7.0f}μs")

    print()
    print("📊 解读：")
    rs = results["right_side"]
    print(f"  • right_side hit={rs['hit']} (旧逻辑常 ≥300 被 top_n 截断)")
    cl = results["chanlun_strict"]
    print(f"  • chanlun_strict {cl['per_stock_us']/1000:.1f}ms/只 (lfilter 优化前 typical ≥10ms)")
    lb = results["bollinger_lower_bounce"]; bk = results["bollinger_breakout"]
    print(f"  • bollinger 拆分: lower_bounce={lb['hit']} / breakout={bk['hit']}（拆分前合并命中 ≈ 二者之和，互斥）")


if __name__ == "__main__":
    main()
