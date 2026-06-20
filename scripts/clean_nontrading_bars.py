"""
扫本地 K 线缓存(data/cache/klines/*.csv),删除日期落在非交易日的行
(周末或节假日,主要是节假日"假 bar"),并同步修正 data/cache/meta.json
里对应股票的 end_date 与 records。

用法:
    python3 scripts/clean_nontrading_bars.py            # dry-run,只报告不修改
    python3 scripts/clean_nontrading_bars.py --apply    # 实际写入

原因:历史上曾在节假日(如 2026-06-19 端午)走过实时/东财路径,写入
amount=0 的假 bar。这些行不仅是脏数据,还把 end_date 顶到节假日,
让"更新判断"误以为已覆盖最新交易日,跳过补抓真正的节前那根。
"""

import os
import sys
import csv
import json
import argparse
from datetime import datetime

# 允许独立运行(不通过包导入)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(ROOT))

from stock_screener.data.market_calendar import is_trading_day  # noqa: E402


KLINES_DIR = os.path.join(ROOT, "data", "cache", "klines")
META_PATH  = os.path.join(ROOT, "data", "cache", "meta.json")


def parse_date(s: str):
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def scan_and_clean(apply_changes: bool) -> dict:
    stats = {"files": 0, "dirty_files": 0, "dropped_rows": 0,
             "by_date": {}, "meta_updates": 0}

    if not os.path.isdir(KLINES_DIR):
        print(f"未找到 {KLINES_DIR}")
        return stats

    meta = {}
    if os.path.exists(META_PATH):
        with open(META_PATH, "r", encoding="utf-8") as f:
            meta = json.load(f)

    csvs = sorted(f for f in os.listdir(KLINES_DIR) if f.endswith(".csv"))
    stats["files"] = len(csvs)

    for fname in csvs:
        code = fname[:-4]
        path = os.path.join(KLINES_DIR, fname)
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            rows = list(reader)
        if not rows:
            continue
        header, data = rows[0], rows[1:]

        kept, dropped = [], []
        for r in data:
            if not r:
                continue
            d = parse_date(r[0])
            if d is None or is_trading_day(d):
                kept.append(r)
            else:
                dropped.append(r)
                stats["by_date"][r[0]] = stats["by_date"].get(r[0], 0) + 1

        if not dropped:
            continue

        stats["dirty_files"] += 1
        stats["dropped_rows"] += len(dropped)

        if apply_changes:
            # 重写 CSV
            with open(path, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(header)
                w.writerows(kept)
            # 更新 meta
            info = meta.get(code)
            if isinstance(info, dict):
                if kept:
                    last_date = kept[-1][0]   # YYYY-MM-DD
                    info["end_date"] = last_date.replace("-", "")
                    info["records"] = len(kept)
                else:
                    info["end_date"] = ""
                    info["records"] = 0
                stats["meta_updates"] += 1

    if apply_changes and stats["meta_updates"]:
        # 安全写 meta:先写临时再 rename
        tmp = META_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        os.replace(tmp, META_PATH)

    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="实际写入(默认 dry-run)")
    args = ap.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] 扫描 {KLINES_DIR}")
    s = scan_and_clean(args.apply)
    print(f"  CSV 总数:      {s['files']}")
    print(f"  含脏行的文件:  {s['dirty_files']}")
    print(f"  删除行数:      {s['dropped_rows']}")
    if s["by_date"]:
        print("  按日期分布(前 10):")
        for d, n in sorted(s["by_date"].items(), key=lambda x: -x[1])[:10]:
            print(f"    {d}: {n}")
    if args.apply:
        print(f"  meta.json 更新: {s['meta_updates']} 只")
    else:
        print("  (dry-run,未修改任何文件;加 --apply 实际执行)")


if __name__ == "__main__":
    main()
