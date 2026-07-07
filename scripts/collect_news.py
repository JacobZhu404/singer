#!/usr/bin/env python3
"""
热榜兜底爬虫：LLM 搜索为主，脚本为辅。在 LLM 执行完 WebSearch 后运行此脚本，
抓取热榜数据源中与智驾相关的标题，补充 LLM 可能遗漏的内容。

理念：
  LLM 做 12 组 WebSearch（需要判断力、迭代能力）—— 主要产出
  脚本抓热榜（机械动作）—— 兜底，防止 LLM 遗漏热门话题

用法：
    python3 scripts/collect_news.py
    python3 scripts/collect_news.py --output data/backup_candidates.json
    python3 scripts/collect_news.py --max-items 20

输出：
    data/backup_candidates_YYYYMMDD.json — 兜底候选列表
"""
import argparse
import json
import re
import sys
from pathlib import Path
from datetime import datetime

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("❌ 需要 requests + beautifulsoup4，请运行:")
    print("   pip install requests beautifulsoup4")
    sys.exit(1)

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"
MATRIX_PATH = BASE / "references" / "search_matrix.yaml"
TODAY = datetime.now()
TD_STR = TODAY.strftime("%Y%m%d")


def _load_hotlist_config():
    """从 search_matrix.yaml 读取热榜配置,消除硬编码。"""
    try:
        import yaml
        matrix = yaml.safe_load(MATRIX_PATH.read_text("utf-8"))
        return matrix.get("hotlist", [])
    except Exception:
        return [
            {"rank": "P1", "url": "https://tophub.today",
             "keywords": ["智能驾驶", "自动驾驶", "robotaxi", "Waymo", "Tesla"]},
            {"rank": "P2", "url": "https://buzzing.cc",
             "keywords": ["autonomous driving", "robotaxi", "Waymo", "Tesla FSD", "self-driving"]},
        ]


HOTLIST_CONFIG = _load_hotlist_config()


def fetch_tophub() -> list:
    """抓取 tophub.today 首页，提取与智驾相关的标题。"""
    items = []
    try:
        r = requests.get(
            "https://tophub.today",
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
            },
            timeout=15,
        )
        if r.status_code != 200:
            print(f"  ⚠️ tophub 返回 {r.status_code}")
            return items

        soup = BeautifulSoup(r.text, "html.parser")
        # tophub 的条目通常在 <a> 标签内
        for a in soup.find_all("a"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if not title:
                continue
            # 检查是否含智驾关键词
            for kw in HOTLIST_CONFIG[0]["keywords"]:
                if kw.lower() in title.lower():
                    items.append({
                        "title": title,
                        "url": href if href.startswith("http") else "",
                        "source": "tophub.today",
                    })
                    break
    except Exception as e:
        print(f"  ⚠️ tophub 抓取异常: {e}")

    return items


def fetch_buzzing() -> list:
    """抓取 buzzing.cc 首页/子站，提取自动驾驶相关标题。"""
    items = []
    urls = [
        "https://buzzing.cc",
        "https://tech.buzzing.cc",
        "https://hn.buzzing.cc",
    ]
    for url in urls:
        try:
            r = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
                timeout=15,
            )
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a"):
                title = a.get_text(strip=True)
                href = a.get("href", "")
                if not title:
                    continue
                for kw in HOTLIST_CONFIG[1]["keywords"]:
                    if kw.lower() in title.lower():
                        # 处理相对链接
                        if href.startswith("/"):
                            href = "https://buzzing.cc" + href
                        items.append({
                            "title": title,
                            "url": href if href.startswith("http") else "",
                            "source": url.replace("https://", ""),
                        })
                        break
        except Exception as e:
            print(f"  ⚠️ {url} 抓取异常: {e}")

    return items


def merge_dedup(items: list) -> list:
    """去除重复标题。"""
    seen = set()
    result = []
    for item in items:
        norm = re.sub(r"\s+", "", item["title"]).lower()
        if norm not in seen:
            seen.add(norm)
            result.append(item)
    return result


def main():
    parser = argparse.ArgumentParser(description="热榜兜底爬虫")
    parser.add_argument("--output", default=None, help="输出路径")
    parser.add_argument("--max-items", type=int, default=30, help="最大保留条数")
    args = parser.parse_args()

    print("🔍 热榜兜底爬虫 — 抓取中...\n")

    # 1) tophub
    print("  [P1] tophub.today...", end=" ", flush=True)
    p1 = fetch_tophub()
    print(f"{len(p1)} 条智驾相关")

    # 2) buzzing
    print("  [P2] buzzing.cc...", end=" ", flush=True)
    p2 = fetch_buzzing()
    print(f"{len(p2)} 条智驾相关")

    # 3) 合并去重
    all_items = merge_dedup(p1 + p2)
    all_items = all_items[:args.max_items]

    if not all_items:
        print("\n⚠️ 未找到任何智驾相关热榜条目（这很正常，不是所有热榜都含智驾）")
        print("   兜底无产出，不影响 LLM 搜索结果。")

    # 4) 输出
    result = {
        "date": TD_STR,
        "generated_by": "collect_news.py",
        "total": len(all_items),
        "items": all_items,
    }

    out_path = Path(args.output) if args.output else DATA_DIR / f"backup_candidates_{TD_STR}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n📄 兜底结果: {out_path}")
    print(f"   共 {len(all_items)} 条候选（已去重）")
    print(f"   供 LLM 合并参考，与 LLM 搜索结果一同进入 dedup 环节")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
