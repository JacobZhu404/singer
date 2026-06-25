"""
基本面数据获取（PE/PB/市值/换手率）

数据源：新浪 `Market_Center.getHQNodeData` 节点接口，一次拉一页 100 只，
所有 A 股 ~5300 只，分 `sh_a` + `sz_a` 两个节点合计约 55 页。本地用线程池并发。

**为什么是新浪而不是 akshare？**
akshare 的 `stock_zh_a_spot_em` 底层走 push2.eastmoney.com，在当前网络环境
被阻断（RemoteDisconnected）。项目其他模块也用新浪节点接口做 K 线兜底，
路径已验证可用。akshare 不可达时这条路仍能跑。

PE/PB 隔夜变化很小（基于年报/季报），故采用「当日磁盘缓存」：
当天已抓过就不再走网络，跨天自动重抓。

返回结构：{code6: {"pe": float|None, "pb": float|None,
                  "mktcap_wan": float, "nmc_wan": float, "turnover": float}}
PE 在亏损公司处为负数或 None（新浪原样返回，调用方按需识别）。
"""

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Dict, Optional

from .data_sources import _SINA_SESSION, _get

logger = logging.getLogger(__name__)

_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cache", "fundamentals.json"
)
_FILE_LOCK = threading.Lock()

_SINA_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
_NODES = ("sh_a", "sz_a")  # 北交所 bj_a 在该节点目前为空（沪深 ~5200 只已覆盖项目主流）
_PAGE_SIZE = 100


def _parse_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fetch_one_page(node: str, page: int) -> list:
    """抓取单页：返回原始记录列表（可能为空表示翻页结束）"""
    resp = _get(
        _SINA_SESSION,
        _SINA_URL,
        {"node": node, "num": _PAGE_SIZE, "sort": "symbol", "asc": 1, "page": page},
        timeout=12,
        retries=2,
    )
    if not resp:
        return []
    try:
        data = json.loads(resp.text)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.debug(f"新浪节点解析失败 node={node} page={page}: {e}")
        return []


def _fetch_node(node: str, max_workers: int = 8) -> Dict[str, dict]:
    """串行翻页直到空（不能并发翻页：需感知到「空页」才停止）。

    实测沪深 A 股每个节点约 25-30 页（每页 100 行），单页 ~0.3s，
    串行下 10 秒内可完成；用线程池开多个节点足够。
    """
    out: Dict[str, dict] = {}
    page = 1
    while True:
        rows = _fetch_one_page(node, page)
        if not rows:
            break
        for r in rows:
            code = str(r.get("code", "")).strip()
            if not code or len(code) != 6:
                continue
            out[code] = {
                "pe": _parse_float(r.get("per")),
                "pb": _parse_float(r.get("pb")),
                "mktcap_wan": _parse_float(r.get("mktcap")) or 0.0,
                "nmc_wan": _parse_float(r.get("nmc")) or 0.0,
                "turnover": _parse_float(r.get("turnoverratio")) or 0.0,
            }
        page += 1
        # 防御：上限保护，避免接口异常时无限翻页
        if page > 200:
            logger.warning(f"新浪节点翻页超过 200 页，强制终止 node={node}")
            break
    return out


def fetch_market_fundamentals(progress_callback=None) -> Dict[str, dict]:
    """
    全市场 A 股基本面快照（PE/PB/总市值/流通市值/换手率）。

    Args:
        progress_callback: 可选回调 (done_node, total_nodes, current_node)

    Returns:
        {code6: {"pe": float|None, "pb": float|None, "mktcap_wan": float,
                 "nmc_wan": float, "turnover": float}}，失败时返回空 dict。
    """
    result: Dict[str, dict] = {}
    total = len(_NODES)

    with ThreadPoolExecutor(max_workers=len(_NODES)) as ex:
        futures = {ex.submit(_fetch_node, node): node for node in _NODES}
        done = 0
        for fut in as_completed(futures):
            node = futures[fut]
            try:
                part = fut.result()
                result.update(part)
                logger.info(f"基本面抓取 node={node} 完成: {len(part)} 只")
            except Exception as e:
                logger.warning(f"基本面抓取 node={node} 失败: {e}")
            done += 1
            if progress_callback:
                progress_callback(done, total, node)

    return result


def _load_disk_cache() -> Optional[dict]:
    """读取磁盘缓存（含元数据）。文件不存在或损坏返回 None。"""
    with _FILE_LOCK:
        if not os.path.exists(_CACHE_PATH):
            return None
        try:
            with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"基本面缓存损坏: {e}，将重新抓取")
            return None
        except Exception as e:
            logger.warning(f"读取基本面缓存失败: {e}")
            return None


def _save_disk_cache(fundamentals: Dict[str, dict]):
    payload = {
        "_fetched_date": date.today().isoformat(),
        "_fetched_at": datetime.now().isoformat(),
        "data": fundamentals,
    }
    os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
    with _FILE_LOCK:
        try:
            tmp = _CACHE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, _CACHE_PATH)
        except Exception as e:
            logger.warning(f"保存基本面缓存失败: {e}")


def load_or_fetch_fundamentals(
    force_refresh: bool = False,
    progress_callback=None,
) -> Dict[str, dict]:
    """
    优先用当日磁盘缓存（PE/PB 隔夜变化极小），过期或强制刷新时重抓。
    全失败时返回空 dict（调用方应优雅降级）。
    """
    if not force_refresh:
        cached = _load_disk_cache()
        if cached and cached.get("_fetched_date") == date.today().isoformat():
            data = cached.get("data", {})
            if data:
                logger.info(f"基本面命中当日缓存: {len(data)} 只")
                return data

    data = fetch_market_fundamentals(progress_callback=progress_callback)
    if data:
        _save_disk_cache(data)
        logger.info(f"基本面抓取完成: {len(data)} 只，已落盘")
    else:
        # 全失败时退回最近一份磁盘缓存（哪怕过期也比没有强）
        cached = _load_disk_cache()
        if cached and cached.get("data"):
            stale = cached["data"]
            logger.warning(
                f"基本面实时抓取失败，回退到 {cached.get('_fetched_date')} 缓存"
                f"（{len(stale)} 只）"
            )
            return stale
    return data
