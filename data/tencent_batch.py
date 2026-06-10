"""
腾讯批量实时行情优化
- 使用腾讯批量接口获取实时价格
- 支持并发批量获取全市场
"""

import re
import time
import logging
import requests
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

_TENCENT_URL = "http://qt.gtimg.cn/q="
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
})


def _to_tencent_code(code: str) -> str:
    """转换为腾讯格式: sh600519 -> sh600519"""
    c = str(code).strip()
    if len(c) > 2 and c[:2].lower() in ("sh", "sz", "bj"):
        return c
    # 纯数字，添加前缀
    if c.startswith("6") or c.startswith("5"):
        return "sh" + c
    else:
        return "sz" + c


def _parse_tencent_line(line: str) -> Optional[dict]:
    """解析腾讯实时行情行"""
    try:
        if not line or "v_" not in line:
            return None
        code = line.split("=")[0].replace("v_", "")
        parts = line.split("=")[1].split("~")
        if len(parts) < 10:
            return None
        return {
            "code": code,
            "name": parts[1],
            "price": float(parts[3]) if parts[3] else 0,
            "prev_close": float(parts[4]) if parts[4] else 0,
            "open": float(parts[5]) if parts[5] else 0,
            "volume": float(parts[6]) if parts[6] else 0,
            "high": float(parts[33]) if parts[33] else 0,
            "low": float(parts[34]) if parts[34] else 0,
            "pct_chg": float(parts[32]) if parts[32] else 0,
        }
    except Exception:
        return None


def get_realtime_batch(codes: List[str], max_workers: int = 3) -> Dict[str, dict]:
    """
    批量获取实时行情（腾讯接口）

    Args:
        codes: 股票代码列表
        max_workers: 并发数（建议2-3，避免封IP）

    Returns:
        {code: {price, volume, pct_chg, ...}}
    """
    if not codes:
        return {}

    # 转换为腾讯格式
    tencent_codes = [_to_tencent_code(c) for c in codes]

    results = {}
    batch_size = 90  # 腾讯批量上限

    # 分批获取
    for i in range(0, len(tencent_codes), batch_size):
        batch = tencent_codes[i:i+batch_size]
        url = _TENCENT_URL + ",".join(batch)

        try:
            resp = _SESSION.get(url, timeout=10)
            if not resp:
                continue

            for line in resp.text.strip().split("\n"):
                data = _parse_tencent_line(line)
                if data:
                    # 转换代码格式：sh600519 -> 600519
                    raw_code = data["code"]
                    if raw_code.startswith("sh"):
                        code = raw_code[2:]
                    elif raw_code.startswith("sz"):
                        code = raw_code[2:]
                    else:
                        code = raw_code
                    results[code] = data

        except Exception as e:
            logger.debug(f"腾讯批量请求失败: {e}")

        # 间隔防封
        time.sleep(0.3)

    return results


def get_realtime_fast(codes: List[str], max_workers: int = 5) -> Dict[str, dict]:
    """
    快速批量获取实时行情（多线程并发）

    Args:
        codes: 股票代码列表
        max_workers: 并发数

    Returns:
        {code: {price, volume, pct_chg, ...}}
    """
    if not codes:
        return {}

    # 分批，每批90只
    batches = []
    batch_size = 90
    for i in range(0, len(codes), batch_size):
        batches.append(codes[i:i+batch_size])

    results = {}

    def _fetch_batch(batch: List[str]) -> Dict[str, dict]:
        return get_realtime_batch(batch)

    with ThreadPoolExecutor(max_workers=min(max_workers, len(batches))) as pool:
        futures = {pool.submit(_fetch_batch, b): b for b in batches}
        for future in as_completed(futures):
            try:
                batch_result = future.result()
                results.update(batch_result)
            except Exception:
                pass

    return results


# 测试
if __name__ == "__main__":
    import time
    codes = [f"60{i:04d}" for i in range(600, 620)]
    codes += [f"00000{i}" for i in range(1, 10)]

    start = time.time()
    results = get_realtime_fast(codes, max_workers=3)
    elapsed = time.time() - start

    print(f"获取 {len(codes)} 只: {elapsed:.2f}s")
    print(f"成功: {len(results)} 只")
    for code, data in list(results.items())[:5]:
        print(f"  {code}: {data['name']} = {data['price']}")