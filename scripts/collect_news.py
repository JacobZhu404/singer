#!/usr/bin/env python3
"""
兜底爬虫：LLM 搜索为主，脚本为辅。
抓取热榜 + 媒体站点中与智驾相关的标题，补充 LLM 可能遗漏的内容。

双轨采集：
  LLM 做 10 组 WebSearch（需要判断力）—— 主力
  脚本抓热榜 + 媒体（机械动作）—— 兜底

用法：
    python3 scripts/collect_news.py
    python3 scripts/collect_news.py --output data/backup_candidates.json
    python3 scripts/collect_news.py --max-items 30
"""
import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path
from datetime import datetime
from typing import Dict, List

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("❌ 需要 requests + beautifulsoup4:")
    print("   pip install requests beautifulsoup4")
    sys.exit(1)

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"
MATRIX_PATH = BASE / "references" / "search_matrix.yaml"
TODAY = datetime.now()
TD_STR = TODAY.strftime("%Y%m%d")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _load_config() -> dict:
    try:
        import yaml
        return yaml.safe_load(MATRIX_PATH.read_text("utf-8"))
    except Exception:
        return {}


CONFIG = _load_config()
HOTLIST = CONFIG.get("hotlist", [])
MEDIA_CRAWL = CONFIG.get("media_crawl", {})
AD_KEYWORDS = MEDIA_CRAWL.get("keywords", [
    "智驾", "自动驾驶", "智能驾驶", "无人驾驶", "robotaxi",
    "waymo", "tesla", "端到端", "激光雷达", "具身智能",
    "FSD", "萝卜快跑", "华为智驾",
])


def _is_ad_related(title: str, keywords: list = None) -> bool:
    kws = keywords or AD_KEYWORDS
    tl = title.lower()
    return any(kw.lower() in tl for kw in kws)


def _extract_links(html: str) -> List[Dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for a in soup.find_all("a"):
        title = a.get_text(strip=True)
        href = a.get("href", "")
        if title and 10 < len(title) < 100:
            results.append({"title": title, "href": href})
    return results


# ─────────────── 热榜爬虫 ───────────────

def fetch_tophub() -> list:
    items = []
    kws = HOTLIST[0]["keywords"] if HOTLIST else AD_KEYWORDS
    try:
        r = requests.get("https://tophub.today", headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return items
        for link in _extract_links(r.text):
            if _is_ad_related(link["title"], kws):
                href = link["href"]
                items.append({
                    "title": link["title"],
                    "url": href if href.startswith("http") else "",
                    "source": "tophub.today",
                })
    except Exception as e:
        print(f"  ⚠️ tophub 异常: {e}")
    return items


def fetch_buzzing() -> list:
    items = []
    kws = HOTLIST[1]["keywords"] if len(HOTLIST) > 1 else ["autonomous driving", "robotaxi"]
    urls = ["https://buzzing.cc", "https://tech.buzzing.cc", "https://hn.buzzing.cc"]
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                continue
            for link in _extract_links(r.text):
                if _is_ad_related(link["title"], kws):
                    href = link["href"]
                    if href.startswith("/"):
                        href = "https://buzzing.cc" + href
                    items.append({
                        "title": link["title"],
                        "url": href if href.startswith("http") else "",
                        "source": url.replace("https://", ""),
                    })
        except Exception as e:
            print(f"  ⚠️ {url} 异常: {e}")
    return items


# ─────────────── 媒体站点爬虫 ───────────────

def fetch_media_site(name: str, url: str) -> list:
    items = []
    try:
        if "sina" in url:
            encoded_url = url.rsplit("/", 1)
            if len(encoded_url) == 2 and not encoded_url[1].startswith("http"):
                encoded_url = encoded_url[0] + "/" + urllib.parse.quote(encoded_url[1])
            else:
                encoded_url = url
        else:
            encoded_url = url

        r = requests.get(encoded_url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return items

        for link in _extract_links(r.text):
            if _is_ad_related(link["title"]):
                href = link["href"]
                if href.startswith("/"):
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    href = f"{parsed.scheme}://{parsed.netloc}{href}"
                items.append({
                    "title": link["title"],
                    "url": href if href.startswith("http") else "",
                    "source": name,
                })
    except Exception as e:
        print(f"  ⚠️ {name} 异常: {e}")
    return items


def fetch_all_media() -> list:
    sites = MEDIA_CRAWL.get("sites", [])
    all_items = []
    for site in sorted(sites, key=lambda s: s.get("priority", 99)):
        name = site["name"]
        url = site["url"]
        print(f"  [媒体] {name}...", end=" ", flush=True)
        items = fetch_media_site(name, url)
        print(f"{len(items)} 条智驾相关")
        all_items.extend(items)
    return all_items


# ─────────────── 去重 ───────────────

def _normalize_title(title: str) -> str:
    t = re.sub(r"\s+", "", title).lower()
    t = re.sub(r"[，。、：；！？""''（）\[\]【】｜|·—\-_,.:;!?\"'()​]", "", t)
    return t


def merge_dedup(items: list) -> list:
    seen_titles = set()
    seen_urls = set()
    result = []
    for item in items:
        norm = _normalize_title(item["title"])
        url = item.get("url", "")
        if norm in seen_titles:
            continue
        if url and url in seen_urls:
            continue
        seen_titles.add(norm)
        if url:
            seen_urls.add(url)
        result.append(item)
    return result


# ─────────────── main ───────────────

def main():
    parser = argparse.ArgumentParser(description="兜底爬虫")
    parser.add_argument("--output", default=None, help="输出路径")
    parser.add_argument("--max-items", type=int, default=30, help="最大保留条数")
    parser.add_argument("--hotlist-only", action="store_true", help="只爬热榜")
    parser.add_argument("--media-only", action="store_true", help="只爬媒体")
    args = parser.parse_args()

    print(f"🔍 兜底爬虫 — {TODAY.strftime('%Y-%m-%d %H:%M')}\n")

    all_items = []

    if not args.media_only:
        print("  [P1] tophub.today...", end=" ", flush=True)
        p1 = fetch_tophub()
        print(f"{len(p1)} 条")
        all_items.extend(p1)

        print("  [P2] buzzing.cc...", end=" ", flush=True)
        p2 = fetch_buzzing()
        print(f"{len(p2)} 条")
        all_items.extend(p2)

    if not args.hotlist_only:
        print()
        media = fetch_all_media()
        all_items.extend(media)

    all_items = merge_dedup(all_items)[:args.max_items]

    if not all_items:
        print("\n⚠️ 未找到智驾相关条目")

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
