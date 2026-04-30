# -*- coding: utf-8 -*-
"""
多数据源适配器（新浪 / 腾讯 / 东方财富，自动降级）
"""

import re
import json
import time
import logging
from typing import Optional, List, Dict
from datetime import datetime

import requests
import pandas as pd

logger = logging.getLogger(__name__)

# ─── HTTP Sessions ──────────────────────────────────────────────────────────
def _make_session(referer: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Referer": referer,
        "Accept": "application/json, text/plain, */*",
    })
    return s

_SINA_SESSION    = _make_session("https://finance.sina.com.cn/")
_TENCENT_SESSION = _make_session("https://qt.gtimg.cn/")
_EAST_SESSION    = _make_session("https://quote.eastmoney.com/")


def _get(session: requests.Session, url: str, params: dict = None,
         timeout: int = 15, retries: int = 2) -> Optional[requests.Response]:
    """带重试的 HTTP GET"""
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as e:
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
            else:
                logger.debug(f"请求失败({retries+1}次) {url}: {e}")
    return None


def _to_code6(code: str) -> str:
    """统一去除 sh/sz 前缀，返回6位代码"""
    c = str(code).strip()
    return c[2:] if len(c) > 2 and c[:2].lower() in ("sh", "sz") else c


def _safe_float(parts: list, idx: int, default: float = 0.0) -> float:
    try:
        v = parts[idx]
        if not isinstance(v, str):
            v = str(v)
        # 先 strip，再检查是否为空
        v = v.strip()
        if not v:
            return default
        # 正确识别多小数点：只有一个小数点且全为数字才合法
        # 例外：科学计数法 "1.5e-3" 也合法
        if v.count(".") > 1:
            return default
        float(v)  # 触发异常则走 except
        return float(v)
    except (IndexError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════════════════
# 数据源实现
# ═══════════════════════════════════════════════════════════════════════════

class DataSource:
    name: str = "base"

    def get_kline(self, code: str, days: int = 60) -> pd.DataFrame:
        raise NotImplementedError

    def get_realtime(self, code: str) -> Dict:
        raise NotImplementedError

    def get_stock_list(self) -> pd.DataFrame:
        raise NotImplementedError


class SinaDataSource(DataSource):
    name = "sina"

    def get_kline(self, code: str, days: int = 60) -> pd.DataFrame:
        code6 = _to_code6(code)
        scode = f"sh{code6}" if code6.startswith("6") else f"sz{code6}"
        url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
        resp = _get(_SINA_SESSION, url, {"symbol": scode, "scale": 240, "ma": "no", "datalen": days})
        if not resp:
            return pd.DataFrame()
        try:
            data = json.loads(resp.text.strip())
            if not data:
                return pd.DataFrame()
            df = pd.DataFrame(data).rename(columns={
                "day": "date", "open": "open", "close": "close",
                "high": "high", "low": "low", "volume": "vol",
            })
            for col in ["open", "close", "high", "low", "vol"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            return df
        except Exception as e:
            logger.debug(f"新浪K线解析失败 {code}: {e}")
            return pd.DataFrame()

    def get_realtime(self, code: str) -> Dict:
        return TencentDataSource().get_realtime(code)

    def get_stock_list(self) -> pd.DataFrame:
        all_rows, seen = [], set()
        # 按涨跌幅正序+倒序各取前几页，覆盖更多股票
        for asc in (0, 1):
            for page in range(1, 30 if asc == 0 else 6):
                url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
                resp = _get(_SINA_SESSION, url, {
                    "num": 100, "page": page, "sort": "changepercent",
                    "asc": asc, "node": "hs_a", "_s_r_a": "page"
                })
                if not resp:
                    break
                try:
                    rows = json.loads(resp.text.strip())
                    if not rows:
                        break
                    for row in rows:
                        sym = row.get("symbol", "").replace("sh", "").replace("sz", "").replace("bj", "")
                        name = row.get("name", sym)
                        if sym and sym not in seen:
                            seen.add(sym)
                            all_rows.append({"代码": sym, "名称": name})
                except Exception:
                    break
                time.sleep(0.1)
        return pd.DataFrame(all_rows)


class TencentDataSource(DataSource):
    name = "tencent"

    def get_kline(self, code: str, days: int = 60) -> pd.DataFrame:
        code6 = _to_code6(code)
        prefix = "sh" if code6.startswith("6") else "sz"
        resp = _get(_TENCENT_SESSION,
                    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                    {"param": f"{prefix}{code6},day,,,{days},qfq"})
        if not resp:
            return pd.DataFrame()
        try:
            data = resp.json()
            key = f"{prefix}{code6}"
            stock_data = data.get("data", {}).get(key, {})
            klines = stock_data.get("day", []) or stock_data.get("qfqday", [])
            if not klines:
                return pd.DataFrame()
            rows = [{"date": k[0], "open": float(k[1]), "close": float(k[2]),
                     "low": float(k[3]), "high": float(k[4]),
                     "vol": float(k[5]) if len(k) > 5 else 0.0}
                    for k in klines if isinstance(k, list) and len(k) >= 5]
            return pd.DataFrame(rows)
        except Exception as e:
            logger.debug(f"腾讯K线解析失败 {code}: {e}")
            return pd.DataFrame()

    def get_realtime(self, code: str) -> Dict:
        code6 = _to_code6(code)
        prefix = "sh" if code6.startswith("6") else "sz"
        resp = _get(_TENCENT_SESSION, f"https://qt.gtimg.cn/q={prefix}{code6}")
        if not resp:
            return {}
        try:
            m = re.match(rf'v_{prefix}{code6}="([^"]+)"', resp.text.strip())
            if not m:
                return {}
            parts = m.group(1).split("~")
            if len(parts) < 35:
                return {}
            return {
                "代码": code6, "名称": parts[1] if len(parts) > 1 else code6,
                "最新价": _safe_float(parts, 3), "昨收": _safe_float(parts, 4),
                "今开": _safe_float(parts, 5), "成交量": _safe_float(parts, 6),
                "成交额": _safe_float(parts, 37), "换手率": _safe_float(parts, 38),
                "市盈率": _safe_float(parts, 39), "涨跌幅": _safe_float(parts, 32),
                "涨跌额": _safe_float(parts, 31),
                "最高价": _safe_float(parts, 33), "最低价": _safe_float(parts, 34),
            }
        except Exception as e:
            logger.debug(f"腾讯实时行情解析失败 {code}: {e}")
            return {}

    def get_realtime_batch(self, codes: List[str]) -> Dict[str, Dict]:
        normalized = [("sh" if _to_code6(c).startswith("6") else "sz") + _to_code6(c) for c in codes]
        results = {}
        for i in range(0, len(normalized), 90):
            batch = normalized[i:i+90]
            resp = _get(_TENCENT_SESSION, f"https://qt.gtimg.cn/q={','.join(batch)}")
            if not resp:
                continue
            for line in resp.text.strip().split("\n"):
                m = re.match(r'v_([a-z]{2}\d+)="([^"]+)"', line)
                if m:
                    parts = m.group(2).split("~")
                    raw_code = m.group(1)[2:]
                    if len(parts) > 35:
                        results[raw_code] = {
                            "代码": raw_code, "名称": parts[1] if len(parts) > 1 else raw_code,
                            "最新价": _safe_float(parts, 3), "昨收": _safe_float(parts, 4),
                            "今开": _safe_float(parts, 5), "成交量": _safe_float(parts, 6),
                            "成交额": _safe_float(parts, 37), "换手率": _safe_float(parts, 38),
                            "市盈率": _safe_float(parts, 39), "涨跌幅": _safe_float(parts, 32),
                            "涨跌额": _safe_float(parts, 31),
                            "最高价": _safe_float(parts, 33), "最低价": _safe_float(parts, 34),
                        }
            time.sleep(0.15)
        return results

    def get_stock_list(self) -> pd.DataFrame:
        return SinaDataSource().get_stock_list()


class EastmoneyDataSource(DataSource):
    name = "eastmoney"

    def get_kline(self, code: str, days: int = 60) -> pd.DataFrame:
        code6 = _to_code6(code)
        prefix = "1" if code6.startswith("6") else "0"
        resp = _get(_EAST_SESSION, "https://push2his.eastmoney.com/api/qt/stock/kline/get", {
            "secid": f"{prefix}.{code6}", "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57", "klt": "101",
            "fqt": "0", "end": datetime.now().strftime("%Y%m%d"), "lmt": days,
        })
        if not resp:
            return pd.DataFrame()
        try:
            klines = resp.json().get("data", {}).get("klines", [])
            rows = []
            for item in klines:
                p = item.split(",")
                if len(p) >= 6:
                    rows.append({"date": p[0], "open": float(p[1]), "close": float(p[2]),
                                 "low": float(p[3]), "high": float(p[4]), "vol": float(p[5])})
            return pd.DataFrame(rows)
        except Exception as e:
            logger.debug(f"东财K线解析失败 {code}: {e}")
            return pd.DataFrame()

    def get_realtime(self, code: str) -> Dict:
        return TencentDataSource().get_realtime(code)

    def get_stock_list(self) -> pd.DataFrame:
        return SinaDataSource().get_stock_list()


# ═══════════════════════════════════════════════════════════════════════════
# 多数据源管理器（带自动降级）
# ═══════════════════════════════════════════════════════════════════════════

class DataSourceManager:
    """按优先级尝试多个数据源，失败自动降级"""

    def __init__(self):
        self._sources: List[DataSource] = [
            SinaDataSource(),
            TencentDataSource(),
            EastmoneyDataSource(),
        ]

    def get_kline(self, code: str, days: int = 60) -> pd.DataFrame:
        for source in self._sources:
            try:
                df = source.get_kline(code, days)
                if not df.empty:
                    return df
            except Exception as e:
                logger.debug(f"[{source.name}] K线失败 {code}: {e}")
        return pd.DataFrame()

    def get_realtime(self, code: str) -> Dict:
        for source in self._sources:
            try:
                data = source.get_realtime(code)
                if data:
                    return data
            except Exception as e:
                logger.debug(f"[{source.name}] 实时行情失败 {code}: {e}")
        return {}

    def get_realtime_batch(self, codes: List[str]) -> Dict[str, Dict]:
        try:
            results = TencentDataSource().get_realtime_batch(codes)
            if results:
                return results
        except Exception as e:
            logger.debug(f"腾讯批量行情失败: {e}")
        # 降级为逐个查询
        results = {}
        for code in codes:
            data = self.get_realtime(code)
            if data:
                results[code] = data
        return results

    def get_stock_list(self) -> pd.DataFrame:
        for source in self._sources:
            try:
                df = source.get_stock_list()
                if not df.empty:
                    return df
            except Exception as e:
                logger.debug(f"[{source.name}] 股票列表失败: {e}")
        return pd.DataFrame()


# 全局单例
data_manager = DataSourceManager()
