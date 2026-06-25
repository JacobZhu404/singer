"""
反转因子的「含成本 + 可成交性」组合回测（离线，读 data/cache/klines/*.csv）

为什么需要它：ic_validation.py 只算横截面 IC，留了两个会**高估反转因子**的偏差
（见 factor-ic-findings）：
  1. 没扣交易成本——0.023 的 1 日 IC 极可能被 T+1 + 印花税 + 滑点吃光；
  2. 未来收益用裸 close-to-close，没排除涨跌停（停板根本买不进/卖不出）。

本工具把 IC 推进到「真能下单的组合」：
  - 每隔 H 个交易日换仓，按因子排序取 top 分位等权做多；
  - 入场口径与回测引擎一致：signal 日 close 算因子 → **T+1 开盘价买入** → close[t+H] 卖出；
  - 排除「T+1 一字/接近涨停开盘」的票（买不进）；
  - 每次换仓扣一次双边成本（top 分位反转近乎 100% 换手，按满额扣是公允且略保守）；
  - 同时给出 gross / net，以及等权全市场基准，直观看成本吃掉多少。

用法： python3 -m stock_screener.tools.factor_portfolio_backtest [sample_n]
"""

import sys
import time
import math
from pathlib import Path

import numpy as np
import pandas as pd

from stock_screener.tools.ic_validation import load_panels, CACHE_DIR
from stock_screener.utils.indicators import get_limit_pct

# 与 backtest_engine 保持一致的成本口径（fraction，非百分数）
SLIPPAGE = 0.0010
COMMISSION = 0.0003
STAMP_DUTY = 0.0005
ROUND_TRIP = (SLIPPAGE + COMMISSION) * 2 + STAMP_DUTY  # ≈ 0.0031

TRADING_DAYS_PER_YEAR = 244  # A 股年均交易日


# ───────────────────────── 纯函数（可单测） ─────────────────────────

def select_top_quantile(factor_row: pd.Series, q: float) -> pd.Index:
    """取因子值最高的 q 分位股票（dropna），返回 code 索引。"""
    s = factor_row.dropna()
    s = s[np.isfinite(s)]
    if len(s) == 0:
        return pd.Index([])
    k = max(1, math.ceil(len(s) * q))
    return s.nlargest(k).index


def limit_pct_map(codes) -> dict:
    """每只股票的涨停阈值（%）。无名称信息，ST 无法识别 → 主板按 10% 处理。"""
    return {c: get_limit_pct(c) for c in codes}


def tradable_codes(codes, prev_close: pd.Series, buy_open: pd.Series,
                   limits: dict, eps: float = 0.3) -> pd.Index:
    """从候选 codes 中剔除「T+1 开盘接近涨停（买不进）」及缺价的票。

    prev_close: signal 日收盘；buy_open: T+1 开盘。pct 距涨停阈值 < eps 视为买不进。
    """
    out = []
    for c in codes:
        pc = prev_close.get(c)
        op = buy_open.get(c)
        if pc is None or op is None or not np.isfinite(pc) or not np.isfinite(op) or pc <= 0:
            continue
        pct = (op - pc) / pc * 100.0
        if pct >= (limits.get(c, 10.0) - eps):
            continue  # 一字/接近涨停，买不进
        out.append(c)
    return pd.Index(out)


def equal_weight_return(buy_open: pd.Series, sell_close: pd.Series, codes) -> float:
    """等权组合的毛收益（buy 开盘 → sell 收盘）。codes 为空返回 nan。"""
    rets = []
    for c in codes:
        bp = buy_open.get(c)
        sp = sell_close.get(c)
        if bp is None or sp is None or not np.isfinite(bp) or not np.isfinite(sp) or bp <= 0:
            continue
        rets.append(sp / bp - 1.0)
    if not rets:
        return float("nan")
    return float(np.mean(rets))


def annualize(period_rets: list, hold_days: int) -> dict:
    """把一串非重叠的持有期净收益汇总成年化指标。"""
    r = np.asarray([x for x in period_rets if np.isfinite(x)], dtype=float)
    n = len(r)
    if n == 0:
        return {"periods": 0}
    periods_per_year = TRADING_DAYS_PER_YEAR / hold_days
    mean, std = r.mean(), r.std()
    cum = float(np.prod(1.0 + r))
    total_days = n * hold_days
    ann_ret = cum ** (TRADING_DAYS_PER_YEAR / total_days) - 1 if cum > 0 else -1.0
    sharpe = (mean / std * math.sqrt(periods_per_year)) if std > 1e-9 else 0.0
    return {
        "periods": n,
        "mean_per": mean,
        "ann_ret": ann_ret,
        "sharpe": sharpe,
        "win_rate": float((r > 0).mean()),
        "cum": cum - 1.0,
    }


# ───────────────────────── 回测主循环 ─────────────────────────

def backtest_factor(panels: dict, factor: pd.DataFrame, hold: int, q: float):
    """非重叠 hold 日换仓的 top-q 等权多头回测，返回 (gross_list, net_list, mkt_list)。"""
    o, c = panels["open"], panels["close"]
    dates = c.index
    n = len(dates)
    limits = limit_pct_map(c.columns)

    gross, net, mkt = [], [], []
    t = hold  # 需要 t-1 算反转、t 算因子；从 hold 开始留足历史
    while t + 1 + hold < n:
        sig_idx = t
        buy_idx = t + 1
        sell_idx = t + hold  # 与引擎一致：买 open[t+1]，卖 close[t+hold]

        factor_row = factor.iloc[sig_idx]
        cand = select_top_quantile(factor_row, q)
        prev_close = c.iloc[sig_idx]
        buy_open = o.iloc[buy_idx]
        sell_close = c.iloc[sell_idx]

        sel = tradable_codes(cand, prev_close, buy_open, limits)
        g = equal_weight_return(buy_open, sell_close, sel)
        if np.isfinite(g):
            gross.append(g)
            net.append(g - ROUND_TRIP)
        # 全市场等权基准（同区间、同口径，但 buy&hold 无换手成本）
        all_codes = factor_row.dropna().index
        m = equal_weight_return(buy_open, sell_close, all_codes)
        if np.isfinite(m):
            mkt.append(m)
        t += hold
    return gross, net, mkt


def _fmt(s: dict) -> str:
    if s.get("periods", 0) == 0:
        return "  无足够样本"
    return (f"{s['periods']:4d} {s['mean_per']:+8.4f} {s['ann_ret']:+8.1%} "
            f"{s['sharpe']:+7.2f} {s['win_rate']:6.0%} {s['cum']:+8.1%}")


def main():
    sample_n = int(sys.argv[1]) if len(sys.argv) > 1 else None
    all_codes = sorted(p.stem for p in CACHE_DIR.glob("*.csv"))
    if sample_n and sample_n < len(all_codes):
        import random
        random.seed(42)
        codes = random.sample(all_codes, sample_n)
    else:
        codes = all_codes
    print(f"💾 universe: {len(codes)} stocks  | 成本: 双边 {ROUND_TRIP*100:.2f}%")

    t0 = time.perf_counter()
    panels = load_panels(codes)
    close = panels["close"]
    print(f"📐 panel: {close.shape[0]} dates × {close.shape[1]} stocks  ({time.perf_counter()-t0:.1f}s)")

    # 反转因子 rev1 = -(今收/昨收 - 1)：昨日跌得多 → 今日看多
    rev1 = -(close / close.shift(1) - 1)

    print("\n反转因子 rev1（top 分位等权多头，T+1 开盘入场，排除涨停板）")
    print(f"  {'持有/分位':12s} {'换仓':>4s} {'每期均值':>8s} {'年化':>8s} "
          f"{'Sharpe':>7s} {'胜率':>6s} {'累计':>8s}")
    for hold in (1, 2, 5, 10):
        for q in (0.05, 0.10):
            gross, net, mkt = backtest_factor(panels, rev1, hold, q)
            sg = annualize(gross, hold)
            sn = annualize(net, hold)
            sm = annualize(mkt, hold)
            tag = f"H={hold} q={q:.0%}"
            print(f"  {tag:12s} gross {_fmt(sg)}")
            print(f"  {'':12s} net   {_fmt(sn)}")
            print(f"  {'':12s} 市场  {_fmt(sm)}")
            print()

    print("📊 读法：net 已扣双边成本；与「市场」（同区间等权 buy&hold）比才算真超额。")
    print("        年化为几何年化；Sharpe = 每期IR × sqrt(年换仓次数)，无风险利率按 0。")


if __name__ == "__main__":
    main()
