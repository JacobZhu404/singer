# -*- coding: utf-8 -*-
"""
多数据源适配器（新浪 / 腾讯 / 东方财富，自动降级）
"""

import re
import json
import time
import logging
import threading
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
    """统一去除 sh/sz/bj 前缀，返回纯数字代码"""
    c = str(code).strip()
    if len(c) > 2 and c[:2].lower() in ("sh", "sz", "bj"):
        return c[2:]
    return c


def _get_market(code6: str) -> str:
    """根据纯数字代码判断市场：sh / sz / bj"""
    c = code6.strip()
    if c.startswith("6") or c.startswith("5"):
        return "sh"
    elif c.startswith("8") or c.startswith("4") or c.startswith("9"):
        return "bj"
    else:
        return "sz"


def _get_sina_symbol(code6: str) -> str:
    """返回新浪K线接口用的 symbol（sh/sz/bj + 代码）"""
    return _get_market(code6) + code6.strip()


def _get_tencent_prefix(code6: str) -> str:
    """返回腾讯接口用的前缀（sh / sz / bj），同 _get_market"""
    return _get_market(code6)


def _get_eastmoney_secid(code6: str) -> str:
    """返回东方财富 K 线接口用的 secid（1/0/2 + . + 代码）"""
    SECID_MAP = {"sh": "1.", "sz": "0.", "bj": "2."}
    return SECID_MAP.get(_get_market(code6), "0.") + code6.strip()


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
        scode = _get_sina_symbol(code6)
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
        """
        通过东方财富接口获取全市场A股列表（SH + SZ + BJ）。
        单次请求分页拉取，比新浪财经API更稳定。

        返回标准化列名 [ts_code, name, market_flag]。
        """
        import time, os, json

        EAST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
        # fs: m:0+t:6(沪A) , m:0+t:80(深A) , m:1+t:2(北交所)
        FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
        FIELDS = "f12,f14,f13"  # f12=代码, f14=名称, f13=市场(1=沪,0=深)

        cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "data", "cache")
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, "stocks.json")  # 统一文件名

        all_stocks = {}
        page, page_size = 1, 500

        while True:
            params = {"pn": page, "pz": page_size, "fs": FS, "fields": FIELDS}
            try:
                resp = _get(_EAST_SESSION, EAST_URL, params=params, timeout=15)
                if not resp:
                    logger.warning(f"东财股票列表API无响应 (page={page})")
                    break
                data = resp.json()
                diff = data.get("data", {}).get("diff", {})
                total = data.get("data", {}).get("total", 0)
                if not diff:
                    break
                for k, v in diff.items():
                    code = str(v.get("f12", "")).strip()
                    name = str(v.get("f14", "")).strip()
                    if code:
                        all_stocks[code] = {"ts_code": code, "name": name}
                logger.info(f"东财股票列表: 第{page}页 +{len(diff)}条, 累计={len(all_stocks)}/{total}")
                if len(all_stocks) >= total:
                    break
                page += 1
                time.sleep(0.05)
            except Exception as e:
                logger.warning(f"东财股票列表请求失败(page={page}): {e}")
                break

        if len(all_stocks) < 3000:
            logger.warning(f"东财返回仅{len(all_stocks)}只（期望>=3000），回退缓存")
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        cached = json.load(f)
                    rows = [{"ts_code": str(r.get("ts_code", r.get("code", ""))).strip(),
                             "name": str(r.get("name", "")).strip()}
                            for r in cached if r.get("ts_code") or r.get("code")]
                    if rows:
                        logger.info(f"回退缓存: {len(rows)} 只")
                        return pd.DataFrame(rows)
                except Exception as e2:
                    logger.error(f"读取缓存失败: {e2}")

        rows = list(all_stocks.values())
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)
            logger.info(f"股票列表(东财API): {len(rows)} 只，已更新缓存")
        except Exception as e:
            logger.warning(f"更新股票列表缓存失败: {e}")
        return pd.DataFrame(rows)


class TencentDataSource(DataSource):
    name = "tencent"

    def get_kline(self, code: str, days: int = 60) -> pd.DataFrame:
        code6 = _to_code6(code)
        prefix = _get_tencent_prefix(code6)
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
                     "high": float(k[3]), "low": float(k[4]),
                     "vol": float(k[5]) if len(k) > 5 else 0.0}
                    for k in klines if isinstance(k, list) and len(k) >= 5]
            return pd.DataFrame(rows)
        except Exception as e:
            logger.debug(f"腾讯K线解析失败 {code}: {e}")
            return pd.DataFrame()

    def get_realtime(self, code: str) -> Dict:
        code6 = _to_code6(code)
        prefix = _get_tencent_prefix(code6)
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
                "code": code6, "名称": parts[1] if len(parts) > 1 else code6,
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
        normalized = [_get_tencent_prefix(_to_code6(c)) + _to_code6(c) for c in codes]
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
                            "code": raw_code, "名称": parts[1] if len(parts) > 1 else raw_code,
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
        secid = _get_eastmoney_secid(code6)
        resp = _get(_EAST_SESSION, "https://push2his.eastmoney.com/api/qt/stock/kline/get", {
            "secid": secid, "fields1": "f1,f2,f3,f4,f5,f6",
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
                                 "high": float(p[3]), "low": float(p[4]), "vol": float(p[5])})
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
    """
    按优先级尝试多个数据源，失败自动降级。
    带源健康状态追踪：某源连续失败多次后自动跳过，直到冷却期过。
    """
    def __init__(self):
        self._sources: List[DataSource] = [
            SinaDataSource(),
            TencentDataSource(),
            EastmoneyDataSource(),
        ]
        # 源健康状态: {name: {"fail_count": int, "last_fail": float}}
        self._health: Dict[str, Dict] = {}
        self._health_lock = threading.Lock()
        self._fail_threshold = 5        # 连续失败N次后标记为不健康
        self._cooldown_sec = 300       # 冷却期（秒），过后重试

    def _mark_fail(self, name: str):
        now = time.time()
        with self._health_lock:
            if name not in self._health:
                self._health[name] = {"fail_count": 0, "last_fail": now}
            h = self._health[name]
            # 如果距离上次失败超过冷却期，重置计数
            if now - h["last_fail"] > self._cooldown_sec:
                h["fail_count"] = 0
            h["fail_count"] += 1
            h["last_fail"] = now

    def _mark_success(self, name: str):
        with self._health_lock:
            if name in self._health:
                self._health[name]["fail_count"] = 0

    def _is_healthy(self, name: str) -> bool:
        with self._health_lock:
            if name not in self._health:
                return True
            h = self._health[name]
            # 超过冷却期，允许重试
            if time.time() - h["last_fail"] > self._cooldown_sec:
                return True
            return h["fail_count"] < self._fail_threshold

    def get_kline(self, code: str, days: int = 60) -> pd.DataFrame:
        for source in self._sources:
            if not self._is_healthy(source.name):
                logger.debug(f"[{source.name}] 源暂时不可用（连续失败），跳过")
                continue
            try:
                df = source.get_kline(code, days)
                if not df.empty:
                    self._mark_success(source.name)
                    return df
                # 返回空DataFrame也视为失败（接口被封会返回空或HTML）
                self._mark_fail(source.name)
            except Exception as e:
                self._mark_fail(source.name)
                logger.debug(f"[{source.name}] K线失败 {code}: {e}")
        return pd.DataFrame()

    def get_health_status(self) -> Dict:
        """返回各数据源健康状态，供前端展示"""
        with self._health_lock:
            return {name: {"fail_count": h["fail_count"],
                          "last_fail": h["last_fail"],
                          "healthy": self._is_healthy(name)}
                    for name, h in self._health.items()}

    def get_realtime(self, code: str) -> Dict:
        for source in self._sources:
            if not self._is_healthy(source.name):
                logger.debug(f"[{source.name}] 源暂时不可用（连续失败），跳过")
                continue
            try:
                data = source.get_realtime(code)
                if data:
                    self._mark_success(source.name)
                    return data
                self._mark_fail(source.name)
            except Exception as e:
                self._mark_fail(source.name)
                logger.debug(f"[{source.name}] 实时行情失败 {code}: {e}")
        return {}

    def get_realtime_batch(self, codes: List[str]) -> Dict[str, Dict]:
        # 先尝试腾讯批量接口（最快）
        if self._is_healthy("tencent"):
            try:
                results = TencentDataSource().get_realtime_batch(codes)
                if results:
                    self._mark_success("tencent")
                    return results
                self._mark_fail("tencent")
            except Exception as e:
                self._mark_fail("tencent")
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
