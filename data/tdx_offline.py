"""
通达信离线日线数据读取器
目录结构:  {base_dir}/sh/lday/sh600000.day
              {base_dir}/sz/lday/sz000001.day
              {base_dir}/bj/lday/bj000001.day

格式：32字节/条，小端序 <
  字节0-3:   date    (I)  YYYYMMDD 整数
  字节4-7:   open    (I)  开盘价×100，÷100还原
  字节8-11:  high    (I)  最高价×100
  字节12-15: low     (I)  最低价×100
  字节16-19: close   (I)  收盘价×100
  字节20-23: amount  (f)  成交额（浮点数，元）
  字节24-27: volume  (I)  成交量（手）
  字节28-31: reserved(I)  保留
"""

import os
import struct
import logging
from datetime import date
from typing import Optional, Dict, List

import pandas as pd

logger = logging.getLogger(__name__)

# ── 常量 ─────────────────────────────────────────────────────
_TDX_FMT = "<iiiii f ii>"   # 8个格式字符 = 32字节，经实测验证
_PRICE_SCALE = 100.0


class TdxOfflineStore:
    """通达信离线数据仓库（第0层缓存）"""

    def __init__(self, base_dir: str = "/Users/jacob/Downloads/hsjday_extracted"):
        self.base_dir = base_dir
        self._dir_map = {
            "sh": os.path.join(base_dir, "sh", "lday"),
            "sz": os.path.join(base_dir, "sz", "lday"),
            "bj": os.path.join(base_dir, "bj", "lday"),
        }
        self._available = {
            k: os.path.isdir(v) for k, v in self._dir_map.items()
        }
        available = [k for k, v in self._available.items() if v]
        if available:
            logger.info(f"TdxOfflineStore: 可用市场 = {available}")
        else:
            logger.warning(f"TdxOfflineStore: 未找到 .day 数据，base_dir={base_dir}")

    def _guess_market(self, code: str) -> Optional[str]:
        c = str(code).strip().lower()
        for pfx in ("sh", "sz", "bj"):
            if c.startswith(pfx):
                c = c[len(pfx):]
        if not c or not c.isdigit():
            return None
        if c.startswith("6"):
            return "sh"
        if c.startswith("8") or c.startswith("4") or c.startswith("92"):
            return "bj"
        return "sz"

    def _file_path(self, code: str) -> Optional[str]:
        market = self._guess_market(code)
        if market is None or not self._available.get(market, False):
            return None
        c = str(code).strip().lower()
        for pfx in ("sh", "sz", "bj"):
            if c.startswith(pfx):
                c = c[len(pfx):]
        fname = f"{market}{c}.day"
        fpath = os.path.join(self._dir_map[market], fname)
        return fpath if os.path.exists(fpath) else None

    def read_day(self, code: str) -> pd.DataFrame:
        """
        读取单只股票的全部 .day 数据。
        返回 DataFrame，列: [date, open, close, high, low, vol]
        与系统现有缓存格式完全一致。
        """
        fpath = self._file_path(code)
        if fpath is None:
            return pd.DataFrame()

        try:
            with open(fpath, "rb") as fp:
                raw = fp.read()
        except Exception as e:
            logger.warning(f"TdxOfflineStore: 读取 {fpath} 失败: {e}")
            return pd.DataFrame()

        n_records = len(raw) // 32
        if n_records == 0:
            return pd.DataFrame()

        rows = []
        for i in range(n_records):
            offset = i * 32
            chunk = raw[offset : offset + 32]
            if len(chunk) < 32:
                break
            rec = struct.unpack(_TDX_FMT, chunk)
            date_int = rec[0]
            date_str = str(date_int)
            if len(date_str) != 8:
                continue
            try:
                dt = date(
                    int(date_str[:4]),
                    int(date_str[4:6]),
                    int(date_str[6:8]),
                )
            except (ValueError, IndexError):
                continue

            rows.append({
                "date":   str(dt),
                "open":   rec[1] / _PRICE_SCALE,
                "close":  rec[4] / _PRICE_SCALE,
                "high":   rec[2] / _PRICE_SCALE,
                "low":    rec[3] / _PRICE_SCALE,
                "vol":    float(rec[6]),
            })

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
        df = df.sort_values("date").reset_index(drop=True)
        df = df[(df["open"] > 0) & (df["close"] > 0)].copy()
        return df

    def get_kline(self, code: str, days: int = 60) -> pd.DataFrame:
        """兼容接口：返回最近 days 条K线"""
        df = self.read_day(code)
        if df.empty or len(df) < 5:
            return pd.DataFrame()
        if len(df) > days:
            df = df.tail(days).reset_index(drop=True)
        return df

    def list_available_codes(self) -> List[str]:
        """返回本地 .day 文件对应的 6 位股票代码列表（不带市场前缀）"""
        codes = set()
        for market, dpath in self._dir_map.items():
            if not os.path.isdir(dpath):
                continue
            for fname in os.listdir(dpath):
                if fname.endswith(".day") and fname.startswith(market):
                    code_num = fname.replace(".day", "")[2:]
                    codes.add(code_num)
        return sorted(codes)

    def get_cache_status(self) -> Dict:
        total = 0
        for dpath in self._dir_map.values():
            if os.path.isdir(dpath):
                total += len([f for f in os.listdir(dpath) if f.endswith(".day")])
        return {
            "total_stocks": total,
            "base_dir": self.base_dir,
            "markets": {k: v for k, v in self._available.items() if v},
        }


# ── 全局单例 ─────────────────────────────────────────────────────
tdx_store = TdxOfflineStore()
