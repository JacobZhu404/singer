"""
101 Alphas 纯时序因子的 IC 验证（离线，读 data/cache/klines/*.csv，不发网络请求）

IC（信息系数）本质是横截面概念：对每个交易日，把全市场所有股票的因子值与
其未来 N 日收益做秩相关（Spearman），得到一条「日度 IC 序列」，再统计：
  - IC 均值：> 0.03 通常认为有选股能力（绝对值）
  - IR = IC均值 / IC标准差：稳定性，> 0.5 较好
  - IC>0 占比：方向一致性
  - t-stat = IR * sqrt(交易日数)：显著性，|t| > 2 显著

用法： python3 -m stock_screener.tools.ic_validation [N]
N: 抽样股票数（默认全 5850）
"""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "cache" / "klines"
MIN_STOCKS_PER_DAY = 100   # 单日参与横截面的最少股票数
FORWARD_HORIZONS = [1, 5]  # 未来收益周期


def load_panels(codes):
    """把每只股票的 OHLCV 读入，按日期对齐成宽表 panel（行=日期，列=代码）。"""
    cols = {"open": {}, "close": {}, "high": {}, "low": {}, "vol": {}}
    for code in codes:
        path = CACHE_DIR / f"{code}.csv"
        try:
            df = pd.read_csv(path, usecols=["date", "open", "close", "high", "low", "vol"])
        except Exception:
            continue
        if len(df) < 30:
            continue
        df = df.drop_duplicates("date").set_index("date").sort_index()
        for c in cols:
            cols[c][code] = df[c]
    panels = {c: pd.DataFrame(cols[c]).sort_index() for c in cols}
    return panels


def compute_factors(p):
    """返回 {因子名: 宽表(日期×代码)}。仅纯时序因子（每只股票用自身历史算）。"""
    o, c, h, l, v = p["open"], p["close"], p["high"], p["low"], p["vol"]
    eps = 1e-9
    factors = {}

    # Alpha#101: (close-open)/((high-low)+.001) —— 当日收盘在日内区间的位置
    factors["alpha101"] = (c - o) / ((h - l) + 0.001)

    # Alpha#12: sign(delta(vol,1)) * (-delta(close,1)) —— 放量下跌看多 / 缩量上涨看空
    factors["alpha12"] = np.sign(v.diff()) * (-c.diff())

    # Alpha#54: -((low-close)*open^5) / ((low-high)*close^5) —— 收盘靠近高点给高分
    factors["alpha54"] = -((l - c) * (o ** 5)) / (((l - h) * (c ** 5)) + eps)

    # Alpha#53: -delta( ((close-low)-(high-close))/(close-low) , 9 )
    rng = ((c - l) - (h - c)) / ((c - l).replace(0, np.nan))
    factors["alpha53"] = -rng.diff(9)

    # 对照基线：5日动量 & 1日反转，用来看 A 股横截面方向
    factors["mom5(基线)"] = c / c.shift(5) - 1
    factors["rev1(基线)"] = -(c / c.shift(1) - 1)

    return factors


def daily_ic(factor: pd.DataFrame, fwd_ret: pd.DataFrame) -> pd.Series:
    """逐日横截面 Spearman IC。factor 与 fwd_ret 同形（日期×代码）。"""
    # 对齐
    f = factor.reindex_like(fwd_ret)
    ics = {}
    for date in f.index:
        fr = f.loc[date]
        rr = fwd_ret.loc[date]
        mask = fr.notna() & rr.notna() & np.isfinite(fr) & np.isfinite(rr)
        if mask.sum() < MIN_STOCKS_PER_DAY:
            continue
        # Spearman = Pearson on ranks
        ic = fr[mask].rank().corr(rr[mask].rank())
        if pd.notna(ic):
            ics[date] = ic
    return pd.Series(ics).sort_index()


def summarize(ic: pd.Series) -> dict:
    n = len(ic)
    if n == 0:
        return {"days": 0}
    mean, std = ic.mean(), ic.std()
    ir = mean / std if std > 0 else 0.0
    t = ir * np.sqrt(n)
    return {
        "days": n, "IC均值": mean, "IC标准差": std,
        "IR": ir, "IC>0占比": (ic > 0).mean(), "t-stat": t,
    }


def main():
    sample_n = int(sys.argv[1]) if len(sys.argv) > 1 else None
    all_codes = sorted(p.stem for p in CACHE_DIR.glob("*.csv"))
    if sample_n and sample_n < len(all_codes):
        import random
        random.seed(42)
        codes = random.sample(all_codes, sample_n)
    else:
        codes = all_codes
    print(f"💾 universe: {len(codes)} stocks")

    t0 = time.perf_counter()
    panels = load_panels(codes)
    close = panels["close"]
    print(f"📐 panel: {close.shape[0]} dates × {close.shape[1]} stocks  ({time.perf_counter()-t0:.1f}s)")

    factors = compute_factors(panels)

    # 未来收益（按 close 对齐到当日因子值）
    fwd = {d: close.shift(-d) / close - 1 for d in FORWARD_HORIZONS}

    print()
    for d in FORWARD_HORIZONS:
        print(f"══════ 未来 {d} 日收益 IC ══════")
        print(f"  {'因子':14s} {'天数':>4s} {'IC均值':>9s} {'IR':>7s} {'IC>0':>7s} {'t-stat':>8s}")
        rows = []
        for name, fac in factors.items():
            ic = daily_ic(fac, fwd[d])
            s = summarize(ic)
            if s["days"] == 0:
                print(f"  {name:14s}  无足够横截面")
                continue
            rows.append((name, s))
            flag = "  ✅" if abs(s["IC均值"]) >= 0.03 and abs(s["t-stat"]) >= 2 else ""
            print(f"  {name:14s} {s['days']:4d} {s['IC均值']:+9.4f} {s['IR']:+7.3f} "
                  f"{s['IC>0占比']:6.0%} {s['t-stat']:+8.2f}{flag}")
        print()

    print("📊 读法：|IC均值|≥0.03 且 |t|≥2 视为有选股能力；IR 看稳定性；")
    print("        IC 符号代表方向（正=因子高→未来涨，负=因子高→未来跌，可反向用）。")


if __name__ == "__main__":
    main()
