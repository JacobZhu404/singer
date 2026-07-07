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
    python3 scripts/collect_news.py --rss-only
    python3 scripts/collect_news.py --api-only
"""
import argparse
import json
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
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

# 硬编码默认值；若 search_matrix.yaml 的 media_crawl.keywords_tier{1,2} 存在则以其为准，
# 避免配置与代码两处维护导致漂移。
_TIER1_DEFAULT = [
    "智驾", "自动驾驶", "智能驾驶", "无人驾驶", "robotaxi",
    "waymo", "tesla fsd", "端到端", "激光雷达", "具身智能",
    "萝卜快跑", "华为智驾", "乾崑",
]

_TIER2_DEFAULT = [
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


def _flatten_keywords(raw) -> List[str]:
    """展平关键词列表：条目可以是单词，也可以是「别名组」子列表。

    YAML 里 `- [小马智行, pony.ai]` 会解析成一个子列表，展平后每个别名
    都是独立关键词——命中任一即视为该 tier 命中。分组仅为可读性/维护，
    不改变匹配语义。
    """
    out: List[str] = []
    for kw in raw or []:
        if isinstance(kw, (list, tuple)):
            out.extend(str(x) for x in kw)
        else:
            out.append(str(kw))
    return out


CONFIG = _load_config()
HOTLIST = CONFIG.get("hotlist", [])
MEDIA_CRAWL = CONFIG.get("media_crawl", {})

TIER1_KEYWORDS = _flatten_keywords(MEDIA_CRAWL.get("keywords_tier1") or _TIER1_DEFAULT)
TIER2_KEYWORDS = _flatten_keywords(MEDIA_CRAWL.get("keywords_tier2") or _TIER2_DEFAULT)


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


def _resp_text(r) -> str:
    """返回正确解码的响应文本。

    部分站点（如快科技 mydrivers 用 gbk）不在 HTTP 头声明 charset，
    requests 会退回 ISO-8859-1 导致中文乱码、进而误命中英文关键词。
    此时改用 apparent_encoding（按内容嗅探）重新解码。
    """
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or r.encoding
    return r.text


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

        for link in _extract_links(_resp_text(r)):
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


# ─────────────── RSS 订阅源解析 ───────────────

def fetch_rss_feed(name: str, url: str) -> List[Candidate]:
    """解析 RSS 2.0 / Atom 订阅源，用关键词过滤后返回候选列表。"""
    items = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return items

        # 部分站点声明编码与实际不符，先按字节解析，失败再按声明编码重试
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            root = ET.fromstring(r.text.encode(r.encoding or "utf-8"))

        tag = root.tag.lower()
        entries = []
        if "rss" in tag:
            channel = root.find("channel")
            if channel is not None:
                entries = channel.findall("item")
        elif "feed" in tag:  # Atom
            entries = root.findall("{http://www.w3.org/2005/Atom}entry") or root.findall("entry")

        for entry in entries:
            title_elem = entry.find("title")
            title = (title_elem.text or "").strip() if title_elem is not None else ""
            if not title or len(title) < 5:
                continue

            href = ""
            link_elem = entry.find("link")
            if link_elem is not None:
                href = link_elem.get("href") or (link_elem.text or "").strip()
            if not href:
                ns = "{http://www.w3.org/2005/Atom}"
                link_elem = entry.find(f"{ns}link")
                if link_elem is not None:
                    href = link_elem.get("href", "")

            conf = _match_keywords(title)
            if not conf:
                continue

            items.append(Candidate(
                title=title,
                url=href if href.startswith("http") else "",
                source_name=name,
                origin="script_crawl",
                confidence=conf,
            ))
    except Exception as e:
        print(f"  ⚠️ RSS {name} 异常: {e}")
    return items


def fetch_all_rss() -> List[Candidate]:
    feeds = MEDIA_CRAWL.get("feeds", [])
    all_items = []
    for feed in sorted(feeds, key=lambda f: f.get("priority", 99)):
        name = feed["name"]
        url = feed["url"]
        print(f"  [RSS] {name}...", end=" ", flush=True)
        items = fetch_rss_feed(name, url)
        high = sum(1 for i in items if i.confidence == "high")
        low = sum(1 for i in items if i.confidence == "low")
        print(f"{len(items)} 条 (高:{high} 低:{low})")
        all_items.extend(items)
    return all_items


# ─────────────── API 数据源 ───────────────

def fetch_hackernews() -> List[Candidate]:
    """通过 HackerNews 官方 Algolia 搜索 API 按关键词拉取技术向新闻。

    用 search 接口一次性返回命中的 story（标题+原文链接），避免 Firebase
    topstories 全量拉取后再逐条取详情的串行开销（30+ 请求、易超时挂起）。
    """
    items = []
    apis = MEDIA_CRAWL.get("apis", [])
    hn_config = next((a for a in apis if "hackernews" in a.get("name", "").lower()), None)
    if not hn_config:
        return items

    search_url = hn_config.get("search_endpoint") or "https://hn.algolia.com/api/v1/search"
    params = {
        "query": hn_config.get("query") or "autonomous driving",
        "tags": "story",
        "hitsPerPage": hn_config.get("max_items", 50),
    }
    try:
        r = requests.get(search_url, params=params, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return items
        for hit in r.json().get("hits", []):
            title = hit.get("title") or hit.get("story_title") or ""
            if not title:
                continue
            conf = _match_keywords(title)
            if not conf:
                continue
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
            items.append(Candidate(
                title=title,
                url=url,
                source_name="HackerNews",
                origin="script_crawl",
                confidence=conf,
            ))
    except Exception as e:
        print(f"  ⚠️ HackerNews 异常: {e}")
    return items


def fetch_all_apis() -> List[Candidate]:
    print("  [API] HackerNews...", end=" ", flush=True)
    items = fetch_hackernews()
    high = sum(1 for i in items if i.confidence == "high")
    low = sum(1 for i in items if i.confidence == "low")
    print(f"{len(items)} 条 (高:{high} 低:{low})")
    return items


# ─────────────── 去重 ───────────────

def _normalize_title(title: str) -> str:
    t = re.sub(r"\s+", "", title).lower()
    t = re.sub(r"[，。、：；！？""''（）\[\]【】｜|·—\-_,.:;!?\"'()​]", "", t)
    return t


def cap_per_source(items: List[Candidate], n: int) -> List[Candidate]:
    """每个来源最多保留 n 条，防止单一聚合站（如新浪tag页）淹没其他信源。

    聚合站会把同一事件的多家转载全部列出，若不设上限，广撒网阶段的输出
    会被单一来源占满，丧失多样性。价值排序交给阶段二 merge。
    """
    if n <= 0:
        return items
    counts: dict = {}
    result = []
    for item in items:
        c = counts.get(item.source_name, 0)
        if c >= n:
            continue
        counts[item.source_name] = c + 1
        result.append(item)
    return result


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
    parser.add_argument("--max-items", type=int, default=300,
                        help="最大保留条数（广撒网阶段宜宽松，价值判断交给阶段二 merge）")
    parser.add_argument("--per-source", type=int, default=40,
                        help="每个来源最多保留条数，防止单一聚合站淹没其他信源（0=不限）")
    parser.add_argument("--hotlist-only", action="store_true", help="只爬热榜")
    parser.add_argument("--media-only", action="store_true", help="只爬媒体")
    parser.add_argument("--rss-only", action="store_true", help="只爬RSS订阅")
    parser.add_argument("--api-only", action="store_true", help="只爬API数据源")
    args = parser.parse_args()

    # 任一 only 标志开启时，仅执行对应渠道；否则全部执行
    any_only = args.hotlist_only or args.media_only or args.rss_only or args.api_only

    print(f"🔍 阶段一·脚本采集 — {TODAY.strftime('%Y-%m-%d %H:%M')}\n")

    all_items: List[Candidate] = []

    if (not any_only) or args.hotlist_only:
        print("  [P1] tophub.today...", end=" ", flush=True)
        p1 = fetch_tophub()
        print(f"{len(p1)} 条")
        all_items.extend(p1)

        print("  [P2] buzzing.cc...", end=" ", flush=True)
        p2 = fetch_buzzing()
        print(f"{len(p2)} 条")
        all_items.extend(p2)

    if (not any_only) or args.media_only:
        if any_only:
            print()
        media = fetch_all_media()
        all_items.extend(media)

    if (not any_only) or args.rss_only:
        if any_only:
            print()
        rss = fetch_all_rss()
        all_items.extend(rss)

    if (not any_only) or args.api_only:
        if any_only:
            print()
        api = fetch_all_apis()
        all_items.extend(api)

    all_items = dedup_candidates(all_items)
    # 稳定排序：高置信优先，同置信内保持来源优先级/插入顺序。
    # 使 per-source 截断优先保留 high，全局截断也偏向高置信。
    _rank = {"high": 0, "low": 1}
    all_items.sort(key=lambda c: _rank.get(c.confidence, 2))
    all_items = cap_per_source(all_items, args.per_source)[:args.max_items]

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
