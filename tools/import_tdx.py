"""
通达信 .day 离线数据 → CSV 缓存 批量导入脚本（运维工具，非常态运行）

用法：
    cd /Users/jacob/personal/stock_screener
    python3 tools/import_tdx.py

说明：
  - 本脚本读取通达信导出的 .day 文件，写入 data/cache/klines/<code>.csv，
    供 MarketScanner 作为历史K线的本地兜底。
  - 一次性运行；之后日常数据走 prefetch_batch 增量更新即可。
"""

import os
import sys
import struct
import glob
import datetime
import csv
import logging

logger = logging.getLogger(__name__)

# ── 配置 ─────────────────────────────────────
_TDX_ROOT  = "/Users/jacob/Downloads/hsjday_extracted"
# tools/ → 项目根 → data/cache/klines
_PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_CACHE_DIR = os.path.join(_PROJECT_ROOT, "data", "cache", "klines")
_FMT       = "<iiiii f ii>"   # 8字段，32字节/条，实测验证
_SCALE     = 100.0
# ─────────────────────────────────────────────


def parse_day_file(filepath):
    """解析单个 .day 文件，返回 list of dicts（列顺序与缓存一致）"""
    try:
        with open(filepath, "rb") as f:
            raw = f.read()
    except Exception as e:
        logger.warning(f"读取 TDX .day 文件失败 {filepath}: {e}")
        return None

    n = len(raw) // 32
    if n == 0:
        return None

    rows = []
    for i in range(n):
        chunk = raw[i * 32 : (i + 1) * 32]
        if len(chunk) < 32:
            break
        rec = struct.unpack(_FMT, chunk)
        date_int = rec[0]
        date_str = str(date_int)
        if len(date_str) != 8:
            continue
        try:
            dt = datetime.date(
                int(date_str[:4]),
                int(date_str[4:6]),
                int(date_str[6:8]),
            )
        except (ValueError, IndexError):
            continue

        rows.append({
            "date":  str(dt),
            "open":   rec[1] / _SCALE,
            "close":  rec[4] / _SCALE,
            "high":   rec[2] / _SCALE,
            "low":    rec[3] / _SCALE,
            "vol":    float(rec[6]),
        })

    if not rows:
        return None

    # 去重（同一天可能两条记录），保留最后一条
    seen = {}
    for r in rows:
        seen[r["date"]] = r
    result = sorted(seen.values(), key=lambda x: x["date"])

    # 过滤停牌日
    result = [r for r in result if r["open"] > 0 and r["close"] > 0]
    return result if len(result) >= 5 else None


def import_all():
    os.makedirs(_CACHE_DIR, exist_ok=True)
    markets = []
    for m in ("sh", "sz", "bj"):
        d = os.path.join(_TDX_ROOT, m, "lday")
        if os.path.isdir(d):
            markets.append((m, d))
    if not markets:
        print(f"错误：未找到任何 .day 文件，请检查 {_TDX_ROOT}")
        sys.exit(1)

    total = sum(len(glob.glob(os.path.join(d, "*.day"))) for _, d in markets)
    print(f"开始导入：{total} 个 .day 文件")
    print(f"输出目录：{_CACHE_DIR}")
    print("-" * 60)

    done = 0
    ok   = 0
    fail = 0

    for market, day_dir in markets:
        day_files = sorted(glob.glob(os.path.join(day_dir, "*.day")))
        for fpath in day_files:
            fname     = os.path.basename(fpath)
            code_full = fname.replace(".day", "")
            code6     = code_full[2:]
            out_path  = os.path.join(_CACHE_DIR, f"{code6}.csv")

            rows = parse_day_file(fpath)
            if rows is None:
                fail += 1
            else:
                with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(
                        f,
                        fieldnames=["date", "open", "close", "high", "low", "vol"]
                    )
                    writer.writeheader()
                    writer.writerows(rows)
                ok += 1

            done += 1
            if done % 500 == 0 or done == total:
                print(f"  进度：{done}/{total}  成功={ok}  失败={fail}")

    print("-" * 60)
    print(f"完成：成功={ok}，失败={fail}，总计={done}")


if __name__ == "__main__":
    import_all()
