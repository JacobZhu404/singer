"""GTJA 多因子合成回测

挑出 IC 显著且互正交的 4 个因子，做横截面排名 + 等权合成，看合成后能否跨过
含成本门槛——这是 191 单因子工作的自然下一步（详见 [[gtja191-validation]]）。

候选与方向（多周期 IC(10d) 符号决定多空向）：
  - alpha1   (t=+33,  IC>0, +)
  - alpha62  (t= +9,  IC>0, +)
  - alpha83  (t= +8,  IC>0, +)
  - alpha90  (t=-13,  IC<0, −  → 取 −α90 同向)
互相 |ρ|≤0.2，是 GTJA Top 9 里真正独立的少数派。

合成流程：每个因子做方向对齐 → 横截面 pct-rank → 等权平均 → 与单最强因子(alpha1)对照。
回测口径与 `factor_portfolio_backtest` 一致：T+1 开盘入场、排除涨停板、双边成本 0.127%。

用法： PYTHONPATH=/Users/jacob/personal .venv/bin/python tools/gtja_factor_composite.py [sample_n]
"""

from __future__ import annotations
import sys
import time
from pathlib import Path

import pandas as pd

from stock_screener.tools import ic_validation as icv
from stock_screener.tools.ic_validation import load_panels, summarize, CACHE_DIR
from stock_screener.tools.factor_portfolio_backtest import (
    backtest_factor, annualize, ROUND_TRIP,
)
from stock_screener.tools.gtja_alpha191 import compute_all

# (因子名, 方向)，方向已据 IC(10d) 符号定死，无需再查
COMPOSITE = [
    ("alpha1",  +1),
    ("alpha62", +1),
    ("alpha83", +1),
    ("alpha90", -1),
]
HOLDS = [5, 10]
Q = 0.10
HORIZONS = [1, 5, 10]

REPORT_PATH = Path(__file__).resolve().parents[1] / "docs" / "gtja191_composite_report.md"


def _rank_panel(f: pd.DataFrame) -> pd.DataFrame:
    return f.rank(axis=1, pct=True)


def build_composite(factors: dict) -> pd.DataFrame:
    parts = []
    for name, sign in COMPOSITE:
        f = factors[name] * sign
        parts.append(_rank_panel(f))
    # 等权平均，行(日期)/列(股票)对齐
    base = parts[0]
    s = base.copy()
    for p in parts[1:]:
        s = s.add(p, fill_value=None)
    return s / len(parts)


def _fmt_pct(x: float) -> str:
    return f"{x*100:+.2f}%" if x is not None else "—"


def _ic_block(label: str, factor: pd.DataFrame, close: pd.DataFrame) -> dict:
    out = {}
    for d in HORIZONS:
        fwd = close.shift(-d) / close - 1
        ic = icv.daily_ic(factor, fwd)
        out[d] = summarize(ic)
    print(f"  {label:14s}", end="")
    for d in HORIZONS:
        s = out[d]
        if s.get("days", 0) == 0:
            print(f"  IC{d}d=—       IR{d}d=—   ", end="")
        else:
            print(f"  IC{d}d={s['IC均值']:+.4f} IR{d}d={s['IR']:+.2f}", end="")
    print()
    return out


def _bt_block(label: str, factor: pd.DataFrame, panels: dict) -> dict:
    out = {}
    for hold in HOLDS:
        gross, net, mkt = backtest_factor(panels, factor, hold, Q)
        out[hold] = {
            "gross": annualize(gross, hold),
            "net": annualize(net, hold),
            "mkt": annualize(mkt, hold),
        }
    parts = []
    for hold in HOLDS:
        net = out[hold]["net"]; mkt = out[hold]["mkt"]
        if net.get("periods", 0) == 0:
            parts.append(f"H{hold}=—")
            continue
        net_ann = net["ann_ret"]
        mkt_ann = mkt.get("ann_ret", 0.0) if mkt.get("periods", 0) else 0.0
        alpha = net_ann - mkt_ann
        parts.append(f"H{hold} net={_fmt_pct(net_ann)} α={_fmt_pct(alpha)} sharpe={net['sharpe']:+.2f}")
    print(f"  {label:14s}  " + "  |  ".join(parts))
    return out


def render_report(ic_alpha1: dict, ic_comp: dict, bt_alpha1: dict, bt_comp: dict, meta: dict) -> str:
    lines = []
    lines.append("# GTJA Alpha191 — 多因子合成 vs 最强单因子")
    lines.append("")
    lines.append(f"> 数据：本地 cache（{meta['n_stocks']} 只 × {meta['n_dates']} 个交易日）")
    lines.append(f"> 跑出时间：{meta['ts']}")
    lines.append(f"> 成本：双边 {ROUND_TRIP*100:.3f}% / 笔")
    lines.append(f"> 合成方法：方向对齐 → 横截面 pct-rank → 等权平均（{len(COMPOSITE)} 个因子）")
    lines.append("")
    lines.append("## 合成构成")
    lines.append("")
    lines.append("| 因子 | 方向 | 角色 |")
    lines.append("|---|:-:|---|")
    role = {
        "alpha1": "最强单因子（IC t=+33）",
        "alpha62": "独立有效（IC t=+9）",
        "alpha83": "独立有效（IC t=+8）",
        "alpha90": "独立有效（IC t=-13，反向取负）",
    }
    for name, sign in COMPOSITE:
        s = "↑" if sign > 0 else "↓"
        lines.append(f"| `{name}` | {s} | {role.get(name, '')} |")
    lines.append("")
    lines.append("**互相 |ρ|≤0.2**（详见 `gtja191_factor_report.md`）——这是 GTJA Top 9 里")
    lines.append("真正独立的少数派，合成才有信息增量。")
    lines.append("")

    # IC
    lines.append("## 1. IC（日度横截面 Spearman）")
    lines.append("")
    lines.append("| | IC(1d) | IR(1d) | IC(5d) | IR(5d) | IC(10d) | IR(10d) | t(10d) |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for label, blk in [("alpha1 单因子", ic_alpha1), ("4 因子合成", ic_comp)]:
        cells = [label]
        for d in HORIZONS:
            s = blk[d]
            if s.get("days", 0) == 0:
                cells += ["—", "—"]
            else:
                cells += [f"{s['IC均值']:+.4f}", f"{s['IR']:+.2f}"]
        s10 = blk[10]
        cells.append(f"{s10.get('t-stat', 0.0):+.1f}" if s10.get("days", 0) else "—")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    # Backtest
    lines.append("## 2. 含成本回测（top-{q}% 等权多头，T+1 开盘入场，排除涨停板）".format(q=int(Q*100)))
    lines.append("")
    lines.append("| | H5 net 年化 | H5 α | H5 Sharpe | H10 net 年化 | H10 α | H10 Sharpe |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for label, blk in [("alpha1 单因子", bt_alpha1), ("4 因子合成", bt_comp)]:
        cells = [label]
        for hold in HOLDS:
            net = blk[hold]["net"]; mkt = blk[hold]["mkt"]
            if net.get("periods", 0) == 0:
                cells += ["—", "—", "—"]
                continue
            net_ann = net["ann_ret"]
            mkt_ann = mkt.get("ann_ret", 0.0) if mkt.get("periods", 0) else 0.0
            alpha = net_ann - mkt_ann
            cells += [_fmt_pct(net_ann), _fmt_pct(alpha), f"{net['sharpe']:+.2f}"]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    # 结论
    bt5_alpha = bt_alpha1[5]["net"]["ann_ret"] - bt_alpha1[5]["mkt"].get("ann_ret", 0.0)
    bt5_comp = bt_comp[5]["net"]["ann_ret"] - bt_comp[5]["mkt"].get("ann_ret", 0.0)
    delta = bt5_comp - bt5_alpha
    verdict = ("✅ 合成有增量" if delta > 0 else "❌ 合成无增量")
    lines.append("## 结论")
    lines.append("")
    lines.append(f"- H5 α：单 alpha1 {_fmt_pct(bt5_alpha)} → 4 因子合成 {_fmt_pct(bt5_comp)}（Δ {_fmt_pct(delta)}） {verdict}")
    lines.append("")
    lines.append("**读法**：")
    lines.append("- 合成的核心假设是「独立有效因子按 rank 等权平均后，特异噪声相互抵消、共有 alpha 加强」。")
    lines.append("- 如果合成 α ≤ 单最强，说明这几个因子虽然 IC 独立，但其 alpha 不可加（或被换手成本吞掉）。")
    lines.append("- 单因子 H5 α>0 已经是 [[gtja191-validation]] 的边缘正向结果，合成是为了把边缘变成有量。")
    lines.append("")
    lines.append("## 复现")
    lines.append("")
    lines.append("```bash")
    lines.append(f"PYTHONPATH=/Users/jacob/personal .venv/bin/python tools/gtja_factor_composite.py {meta['n_stocks']}")
    lines.append("```")
    return "\n".join(lines) + "\n"


def main():
    sample_n = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
    all_codes = sorted(p.stem for p in CACHE_DIR.glob("*.csv"))
    if sample_n and sample_n < len(all_codes):
        import random
        random.seed(42)
        codes = random.sample(all_codes, sample_n)
    else:
        codes = all_codes
    print(f"💾 universe: {len(codes)} stocks  | 成本: 双边 {ROUND_TRIP*100:.3f}%")

    t0 = time.perf_counter()
    panels = load_panels(codes)
    close = panels["close"]
    n_dates, n_stocks = close.shape
    print(f"📐 panel: {n_dates} dates × {n_stocks} stocks  ({time.perf_counter()-t0:.1f}s)")

    min_stocks = max(30, min(100, int(n_stocks * 0.3)))
    if min_stocks != icv.MIN_STOCKS_PER_DAY:
        icv.MIN_STOCKS_PER_DAY = min_stocks
        print(f"⚙️  MIN_STOCKS_PER_DAY → {min_stocks}（sample 较小）")

    t1 = time.perf_counter()
    factors = compute_all(panels)
    print(f"🧮 computed {len(factors)} factors  ({time.perf_counter()-t1:.1f}s)")

    print("\n══════ IC ══════")
    alpha1_dir = factors["alpha1"] * (+1)  # 方向已对齐
    ic_alpha1 = _ic_block("alpha1 单因子", alpha1_dir, close)
    composite = build_composite(factors)
    ic_comp = _ic_block("4 因子合成", composite, close)

    print("\n══════ 含成本回测 ══════")
    bt_alpha1 = _bt_block("alpha1 单因子", alpha1_dir, panels)
    bt_comp = _bt_block("4 因子合成", composite, panels)

    meta = {
        "n_stocks": n_stocks,
        "n_dates": n_dates,
        "ts": time.strftime("%Y-%m-%d %H:%M"),
    }
    report = render_report(ic_alpha1, ic_comp, bt_alpha1, bt_comp, meta)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"\n📝 report → {REPORT_PATH.relative_to(REPORT_PATH.parents[1])}")
    print(f"   total: {time.perf_counter()-t0:.1f}s")


if __name__ == "__main__":
    main()
