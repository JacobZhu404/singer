"""
通信达 .day 文件 → 系统CSV缓存 批量导入脚本

用法：
    python /Users/jacob/personal/stock_screener/data/import_tonghuada.py

输出：
    data/cache/klines/{code6}.csv  (与现有缓存格式完全一致)
"""

import os
import sys
import struct
import datetime
import pandas as pd

# ── 路径配置 ──────────────────────────────────────────────────────────────────
_TONGHUADA_ROOT = "/Users/jacob/Downloads/hsjday_extracted"
_OUTPUT_DIR      = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cache", "klines"
)
# ────────────────────────────────────────────────────────────────────────────────


def parse_tonghuada_day(filepath: str) -> pd.DataFrame:
    """
    解析通信达 .day 文件，返回标准 DataFrame。
    字段：date(open, high, low, close, volume, amount
    价格已 ÷100 还原为元（通信达以"分"存储）。
    """
    if not os.path.exists(filepath):
        return pd.DataFrame()

    with open(filepath, "rb") as f:
        data = f.read()

    if len(data) % 32 != 0:
        print(f"  ⚠ 文件大小异常: {filepath}, {len(data)} 字节")
        return pd.DataFrame()

    records = []
    for offset in range(0, len(data), 32):
        chunk = data[offset : offset + 32]
        # 通通信达日线格式（小端序）：
        #   I  date       (YYYYMMDD 整数)
        #   I  open       (分，÷100 → 元)
        #   I  high       (分)
        #   I  low        (分)
        #   I  close      (分)
        #   f  amount    (成交额，float，元)
        #   I  volume     (成交量，手)
        #   I  reserved   (保留字段)
        rec = struct.unpack("<IIIIIfII", chunk)
        date_int = rec[0]
        date_str = str(date_int)
        try:
            dt = datetime.date(
                int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8])
            )
        except ValueError:
            continue  # 跳过无效日期

        records.append(
            {
                "date": dt,
                "open": rec[1] / 100.0,
                "high": rec[2] / 100.0,
                "low": rec[3] / 100.0,
                "close": rec[4] / 100.0,
                "amount": float(rec[5]),
                "volume": int(rec[6]),
            }
        )

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    # 去重（保留最后一条，防止数据文件中有重复日期）
    df = df.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    return df


def import_one_market(market: str, max_workers: int = 20) -> tuple:
    """
    导入单个市场（sh 或 sz）的所有 .day 文件。
    返回 (成功数, 失败数, 跳过数)。
    """
    day_dir = os.path.join(_TONGHUADA_ROOT, market, "lday")
    if not os.path.isdir(day_dir):
        print(f"  ⚠ 目录不存在: {day_dir}")
        return 0, 0, 0

    day_files = [f for f in os.listdir(day_dir) if f.endswith(".day")]
    total = len(day_files)
    print(f"  [{market.upper()}] 发现 {total} 个 .day 文件")

    success = 0
    failed  = 0
    skipped = 0

    for i, fname in enumerate(sorted(day_files), 1):
        # fname 格式：sh600000.day 或 sz000001.day
        code6 = fname.replace(".day", "")   # 带前缀，如 sh600000
        code6_raw = code6
        # 去掉前缀存为缓存文件名（与现有系统一致）
        code6_num = code6.replace("sh", "").replace("sz", "")
        out_path  = os.path.join(_OUTPUT_DIR, f"{code6_num}.csv")

        # 跳过已存在的（增量更新时用）
        # 如需强制重导，删掉这个 continue
        # if os.path.exists(out_path):
        #     skipped += 1
        #     continue

        fpath = os.path.join(day_dir, fname)
        df    = parse_tonghuada_day(fpath)
        if df.empty:
            failed += 1
            continue

        # 保存为与现有缓存完全一致的 CSV 格式
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
        success += 1

        if i % 500 == 0 or i == total:
            print(f"  [{market.upper()}] 进度: {i}/{total}  "
                  f"(成功={success}, 失败={failed}, 跳过={skipped})")

    return success, failed, skipped


def main():
    os.makedirs(_OUTPUT_DIR, exist_ok=True)

    print("=" * 60)
    print("通信达 K线数据 → 系统缓存 批量导入")
    print(f"  源目录 : {_TONGHUADA_ROOT}")
    print(f"  输出目录: {_OUTPUT_DIR}")
    print("=" * 60)

    total_success = 0
    total_failed  = 0
    total_skipped = 0

    for market in ("sh", "sz", "bj"):
        day_dir = os.path.join(_TONGHUADA_ROOT, market, "lday")
        if not os.path.isdir(day_dir):
            print(f"\n[{market.upper()}] 目录不存在，跳过")
            continue

        print(f"\n[{market.upper()}] 开始导入...")
        s, f, k = import_one_market(market)
        total_success += s
        total_failed  += f
        total_skipped += k
        print(f"[{market.upper()}] 完成: 成功={s}, 失败={f}, 跳过={k}")

    print("\n" + "=" * 60)
    print(f"全部完成: 成功={total_success}, 失败={total_failed}, 跳过={total_skipped}")
    print(f"缓存文件目录: {_OUTPUT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
