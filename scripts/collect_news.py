#!/usr/bin/env python3
"""
阶段一·脚本渠道 — 广撒网采集，不做价值判断。

抓取热榜 + 媒体站点，用双层关键词过滤：
  tier-1（高置信）：智驾/robotaxi 等精确词
  tier-2（低置信）：汽车/芯片/AI 等泛化词，宁多勿漏

产出 candidates 格式 JSON，供阶段二 merge_candidates.py 融合。

用法：
    python3 scripts/collect_news.py
    python3 scripts/collect_news.py --output data/script_candidates_20260707.json
    python3 scripts/collect_news.py --hotlist-only
    python3 scripts/collect_news.py --media-only
"""
import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path
from datetime import datetime
from typing import List

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("❌ 需要 requests + beautifulsoup4:")
    print("   pip install requests beautifulsoup4")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))
from utils.candidate_schema import Candidate, CandidateList, save_candidates

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

# ─────────────── 关键词配置 ───────────────

TIER1_KEYWORDS = [
    "智驾", "自动驾驶", "智能驾驶", "无人驾驶", "robotaxi",
    "waymo", "tesla fsd", "端到端", "激光雷达", "具身智能",
    "萝卜快跑", "华为智驾", "乾崑",
]

TIER2_KEYWORDS = [
    "汽车", "车企", "新能源", "电动", "出行",
    "机器人", "芯片", "半导体", "大模型", "ai",
    "人工智能", "llm", "gpt", "车规", "域控",
    "adas", "lidar", "智能网联",
]


def _load_config() -> dict:
    try:
        import yaml
        return yaml.safe_load(MATRIX_PATH.read_text("utf-8"))
    except Exception:
        return {}


CONFIG = _load_config()
HOTLIST = CONFIG.get("hotlist", [])
MEDIA_CRAWL = CONFIG.get("media_crawl", {})


def _match_keywords(title: str) -> str:
    """返回置信度: 'high' (tier-1命中), 'low' (tier-2命中), '' (无命中)"""
    tl = title.lower()
    for kw in TIER1_KEYWORDS:
        if kw.lower() in tl:
            return "high"
    for kw in TIER2_KEYWORDS:
        if kw.lower() in tl:
            return "low"
    return ""


def _extract_links(html: str) -> List[dict]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for a in soup.find_all("a"):
        title = a.get_text(strip=True)
        href = a.get("href", "")
        if title and 10 < len(title) < 100:
            results.append({"title": title, "href": href})
    return results


# ─────────────── 热榜爬虫 ───────────────

def fetch_tophub() -> List[Candidate]:
    items = []
    kws = HOTLIST[0]["keywords"] if HOTLIST else TIER1_KEYWORDS
    try:
        r = requests.get("https://tophub.today", headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return items
        for link in _extract_links(r.text):
            tl = link["title"].lower()
            for kw in kws:
                if kw.lower() in tl:
                    href = link["href"]
                    items.append(Candidate(
                        title=link["title"],
                        url=href if href.startswith("http") else "",
                        source_name="tophub.today",
                        origin="script_crawl",
                        confidence="high",
                    ))
                    break
    except Exception as e:
        print(f"  ⚠️ tophub 异常: {e}")
    return items


def fetch_buzzing() -> List[Candidate]:
    items = []
    kws = HOTLIST[1]["keywords"] if len(HOTLIST) > 1 else ["autonomous driving", "robotaxi"]
    urls = ["https://buzzing.cc", "https://tech.buzzing.cc", "https://hn.buzzing.cc"]
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                continue
            for link in _extract_links(r.text):
                tl = link["title"].lower()
                for kw in kws:
                    if kw.lower() in tl:
                        href = link["href"]
                        if href.startswith("/"):
                            href = "https://buzzing.cc" + href
                        items.append(Candidate(
                            title=link["title"],
                            url=href if href.startswith("http") else "",
                            source_name=url.replace("https://", ""),
                            origin="script_crawl",
                            confidence="high",
                        ))
                        break
        except Exception as e:
            print(f"  ⚠️ {url} 异常: {e}")
    return items


# ─────────────── 媒体站点爬虫 ───────────────

def fetch_media_site(name: str, url: str) -> List[Candidate]:
    items = []
    try:
        if "sina" in url:
            parts = url.rsplit("/", 1)
            if len(parts) == 2 and not parts[1].startswith("http"):
                encoded_url = parts[0] + "/" + urllib.parse.quote(parts[1])
            else:
                encoded_url = url
        else:
            encoded_url = url

        r = requests.get(encoded_url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return items

        for link in _extract_links(r.text):
            conf = _match_keywords(link["title"])
            if not conf:
                continue
            href = link["href"]
            if href.startswith("/"):
                parsed = urllib.parse.urlparse(url)
                href = f"{parsed.scheme}://{parsed.netloc}{href}"
            items.append(Candidate(
                title=link["title"],
                url=href if href.startswith("http") else "",
                source_name=name,
                origin="script_crawl",
                confidence=conf,
            ))
    except Exception as e:
        print(f"  ⚠️ {name} 异常: {e}")
    return items


def fetch_all_media() -> List[Candidate]:
    sites = MEDIA_CRAWL.get("sites", [])
    all_items = []
    for site in sorted(sites, key=lambda s: s.get("priority", 99)):
        name = site["name"]
        url = site["url"]
        print(f"  [媒体] {name}...", end=" ", flush=True)
        items = fetch_media_site(name, url)
        high = sum(1 for i in items if i.confidence == "high")
        low = sum(1 for i in items if i.confidence == "low")
        print(f"{len(items)} 条 (高:{high} 低:{low})")
        all_items.extend(items)
    return all_items


# ─────────────── 去重 ───────────────

def _normalize_title(title: str) -> str:
    t = re.sub(r"\s+", "", title).lower()
    t = re.sub(r"[，。、：；！？""''（）\[\]【】｜|·—\-_,.:;!?\"'()​]", "", t)
    return t


def dedup_candidates(items: List[Candidate]) -> List[Candidate]:
    seen_titles = set()
    seen_urls = set()
    result = []
    for item in items:
        norm = _normalize_title(item.title)
        if norm in seen_titles:
            continue
        if item.url and item.url in seen_urls:
            continue
        seen_titles.add(norm)
        if item.url:
            seen_urls.add(item.url)
        result.append(item)
    return result


# ─────────────── main ───────────────

def main():
    parser = argparse.ArgumentParser(description="阶段一·脚本采集")
    parser.add_argument("--output", default=None, help="输出路径")
    parser.add_argument("--max-items", type=int, default=50, help="最大保留条数")
    parser.add_argument("--hotlist-only", action="store_true", help="只爬热榜")
    parser.add_argument("--media-only", action="store_true", help="只爬媒体")
    args = parser.parse_args()

    print(f"🔍 阶段一·脚本采集 — {TODAY.strftime('%Y-%m-%d %H:%M')}\n")

    all_items: List[Candidate] = []

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

    all_items = dedup_candidates(all_items)[:args.max_items]

    high = sum(1 for i in all_items if i.confidence == "high")
    low = sum(1 for i in all_items if i.confidence == "low")

    cl = CandidateList(
        date=TD_STR,
        source_type="script_crawl",
        total=len(all_items),
        candidates=all_items,
    )

    out_path = Path(args.output) if args.output else DATA_DIR / f"script_candidates_{TD_STR}.json"
    save_candidates(cl, out_path)

    print(f"\n📄 阶段一·脚本采集完成: {out_path}")
    print(f"   共 {len(all_items)} 条候选（高置信:{high} 低置信:{low}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
