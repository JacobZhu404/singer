"""按回测结果推导各策略在综合排名中的权重。

`merge_results` 用 `STRATEGY_REGISTRY[*]["weight"]` 给每只命中股票算加权总分
（`weighted_score += sig.score * weight + rank_bonus`），权重越高的策略对最终
综合排名影响越大。此前权重是手调的（0.9~1.3），本工具把它锚定到**当前成本口径
下的回测 α**，让排名向历史上真正赚钱的策略倾斜。

口径前提（重要）：只认与当前成本一致的回测结果。成本/口径变更后必须先重跑回测
（tools/full_backtest.py），再跑本工具——否则权重会固化已知错误的旧 α。

权重公式（平滑、抗噪、向中性收缩）：
  1. 取每只策略在持有期 {5,10,30} 日的净 α（已扣 0.127% 双边成本）。
     跳过 2 日：太短，微观结构噪声大。
  2. 逐持有期做**横截面 z-score**，再对三个 z 求均值得 zblend。
     —— 用 z 而非原始 α，避免 30 日 α 数值天然偏大而主导混合；衡量的是
        "相对同侪是否持续靠前"，跨持有期均衡。
  3. **可信度收缩**：eff = zblend × n/(n+K)，K=150。样本越小越往 0（中性）拉。
     —— 这一步专治 high_tight_flag 这类 18 笔交易、α 看着高但不可信的策略。
  4. 映射到权重：weight = clip(1.0 + GAIN×eff, WMIN, WMAX)。
     中性（eff=0）→ 1.0；GAIN/区间刻意保守，避免对回测噪声过度下注。

用法：
  # 预览（默认 dry-run，不改任何文件）
  PYTHONPATH=/Users/jacob/personal .venv/bin/python tools/derive_weights.py
  # 指定回测 JSON
  PYTHONPATH=/Users/jacob/personal .venv/bin/python tools/derive_weights.py --report backtest/results/backtest_xxx.json
  # 确认后写回 registry.py 的 weight 字面量
  PYTHONPATH=/Users/jacob/personal .venv/bin/python tools/derive_weights.py --apply
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics as st
from typing import Dict, List, Tuple

from stock_screener.strategies.registry import STRATEGY_REGISTRY

# ── 公式常量（改这里即可调参，注释见模块头）────────────────────────────────
PERIODS = [5, 10, 30]
CREDIBILITY_K = 150       # 可信度收缩半衰量（≈ 健康样本量）
GAIN = 0.18               # eff → 权重的斜率
W_MIN, W_MAX = 0.6, 1.4   # 权重夹取区间

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RESULTS_DIR = os.path.join(_ROOT, "backtest", "results")
_REGISTRY_PATH = os.path.join(_ROOT, "strategies", "registry.py")


def _latest_report() -> str:
    """最新的 backtest_*.json（按文件名时间戳，排除 sweep/factor 等其它产物）。"""
    cands = sorted(glob.glob(os.path.join(_RESULTS_DIR, "backtest_*.json")))
    if not cands:
        raise SystemExit(f"未找到回测结果: {_RESULTS_DIR}/backtest_*.json，先跑 tools/full_backtest.py")
    return cands[-1]


def _load_report(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _check_cost_consistency(report: dict) -> None:
    """回测成本口径必须与当前代码一致，否则权重会固化旧 α。"""
    from stock_screener.backtest import backtest_engine as bt
    meta = report.get("__meta__", {})
    file_cost = meta.get("round_trip_cost_pct")
    cur_cost = round(bt.ROUND_TRIP_COST_PCT, 5)
    if file_cost is None:
        print("⚠️  回测 JSON 缺 __meta__.round_trip_cost_pct，无法校验成本口径，谨慎使用。")
        return
    if abs(float(file_cost) - cur_cost) > 1e-4:
        raise SystemExit(
            f"❌ 成本口径不一致：回测={file_cost}% 当前={cur_cost}%。\n"
            f"   成本/口径已变更，请先重跑 tools/full_backtest.py 再推导权重。"
        )


def compute_weights(report: dict) -> List[Tuple[str, int, float, float, float, float, float]]:
    """返回按新权重降序的 (策略, n, zblend, cred, eff, 旧权重, 新权重)。"""
    names = [k for k in report if k != "__meta__" and k in STRATEGY_REGISTRY]

    def alpha(name: str, p: int) -> float:
        return report[name]["period_stats"].get(str(p), {}).get("alpha", 0.0)

    trades = {k: int(report[k].get("total_trades", 0)) for k in names}

    # 逐持有期横截面 z-score
    z_by_name: Dict[str, List[float]] = {k: [] for k in names}
    for p in PERIODS:
        vals = [alpha(k, p) for k in names]
        mean = st.mean(vals)
        sd = st.pstdev(vals) or 1.0
        for k in names:
            z_by_name[k].append((alpha(k, p) - mean) / sd)

    rows = []
    for k in names:
        zblend = st.mean(z_by_name[k])
        n = trades[k]
        cred = n / (n + CREDIBILITY_K) if (n + CREDIBILITY_K) else 0.0
        eff = zblend * cred
        new_w = round(max(W_MIN, min(W_MAX, 1.0 + GAIN * eff)), 2)
        old_w = float(STRATEGY_REGISTRY[k].get("weight", 1.0))
        rows.append((k, n, zblend, cred, eff, old_w, new_w))

    rows.sort(key=lambda r: -r[6])
    return rows


def print_table(rows, report_path: str) -> None:
    print(f"📊 源回测: {os.path.basename(report_path)}")
    print(f"   公式: z-score({PERIODS}) × n/(n+{CREDIBILITY_K}) → 1.0+{GAIN}·eff, clip[{W_MIN},{W_MAX}]")
    print(f"{'strategy':24s}{'n':>5}{'zblend':>8}{'cred':>6}{'eff':>7}{'curW':>6}{'newW':>6}  Δ")
    print("─" * 70)
    for k, n, zb, cred, eff, ow, nw in rows:
        print(f"{k:24s}{n:5d}{zb:8.2f}{cred:6.2f}{eff:7.2f}{ow:6.2f}{nw:6.2f}  {nw-ow:+.2f}")


def apply_to_registry(rows) -> None:
    """把新权重写回 registry.py 的 weight 字面量（每个策略块内唯一一处）。"""
    with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
        src = f.read()

    changed = 0
    for k, _, _, _, _, _, new_w in rows:
        # 锚定到唯一的策略 key，非贪婪匹配到该块内第一个 "weight": <num>
        pattern = re.compile(
            r'("' + re.escape(k) + r'":\s*\{.*?"weight":\s*)[\d.]+',
            re.DOTALL,
        )
        new_src, cnt = pattern.subn(lambda m: f"{m.group(1)}{new_w}", src, count=1)
        if cnt == 1:
            src = new_src
            changed += 1
        else:
            print(f"⚠️  未能在 registry.py 定位 {k} 的 weight，跳过")

    with open(_REGISTRY_PATH, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"✅ 已写回 registry.py：{changed}/{len(rows)} 个策略权重")


def main():
    ap = argparse.ArgumentParser(description="按回测 α 推导策略权重")
    ap.add_argument("--report", default=None, help="回测 JSON 路径（默认取最新 backtest_*.json）")
    ap.add_argument("--apply", action="store_true", help="写回 registry.py（默认仅预览）")
    args = ap.parse_args()

    path = args.report or _latest_report()
    report = _load_report(path)
    _check_cost_consistency(report)
    rows = compute_weights(report)
    print_table(rows, path)

    if args.apply:
        apply_to_registry(rows)
    else:
        print("\n（dry-run：未改动任何文件。确认无误后加 --apply 写回 registry.py）")


if __name__ == "__main__":
    main()
