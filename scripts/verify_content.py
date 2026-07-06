#!/usr/bin/env python3
"""内容真实性校验：检查仪表盘中的每个URL，其网页标题是否与卡片标题匹配。

用法:
    python3 scripts/verify_content.py --html data/自动驾驶晨报_YYYYMMDD.html [--threshold 0.3]

说明:
    - 通过 requests 抓取每个URL的 <title> 标签
    - 与卡片标题做简单关键词匹配
    - 报告明显不匹配或无法访问的链接
    - 结果仅供参考，最终判断仍需人工复核
"""
import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    print("缺少 requests 库，请先安装: pip install requests")
    sys.exit(1)


def extract_cards(html_text: str):
    """从仪表盘 HTML 中提取所有卡片。"""
    sec_blocks = list(re.finditer(r'<div class="sec sec-\d+" id="(sec-\d+)">', html_text))
    card_pat = re.compile(
        r'<a class="card"[^>]*?href="([^"]+)"[^>]*?>'
        r'.*?<span class="card-num"[^>]*>(\d+)</span>'
        r'.*?<span class="card-source">([^<]+)</span>'
        r'.*?<span class="card-date">([^<]+)</span>'
        r'.*?<div class="card-title">([^<]+)</div>',
        re.S
    )
    cards = []
    for i, sm in enumerate(sec_blocks):
        start = sm.start()
        end = sec_blocks[i + 1].start() if i + 1 < len(sec_blocks) else len(html_text)
        sec_html = html_text[start:end]
        for url, num, source, date, title in card_pat.findall(sec_html):
            cards.append({
                "num": num.strip(),
                "title": title.strip(),
                "source": source.strip(),
                "date": date.strip(),
                "url": url.strip(),
                "sec_id": sm.group(1),
            })
    return cards


def fetch_title(url: str, timeout: int = 15) -> tuple:
    """获取网页标题和HTTP状态码，自动检测编码。"""
    try:
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=timeout,
            allow_redirects=True,
        )
        code = r.status_code
        if code != 200:
            return code, ""
        # 自动检测编码（处理 GBK/GB2312 等）
        if r.encoding and r.encoding.lower() in ("iso-8859-1",):
            r.encoding = r.apparent_encoding
        # 简单提取 <title>...</title>
        m = re.search(r'<title[^>]*>(.*?)</title>', r.text, re.S | re.I)
        title = m.group(1).strip() if m else ""
        # 去除HTML实体
        title = re.sub(r'<[^>]+>', '', title)
        # 去除常见站点后缀
        title = re.sub(r'[_\-\|].*?(腾讯新闻|新浪网|搜狐|网易|IT之家|虎嗅网|36氪|知乎|车质网|网通社汽车)$', '', title, flags=re.I).strip()
        return code, title
    except requests.exceptions.Timeout:
        return "TIMEOUT", ""
    except Exception as e:
        return f"ERROR:{type(e).__name__}", ""


def title_similarity(card_title: str, page_title: str) -> float:
    """简单标题相似度：关键词重叠率。"""
    if not page_title:
        return 0.0
    # 提取中文字符和英文单词
    card_words = set(re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]+', card_title.lower()))
    page_words = set(re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]+', page_title.lower()))
    if not card_words:
        return 0.0
    overlap = card_words & page_words
    return len(overlap) / len(card_words)


def main():
    parser = argparse.ArgumentParser(description="Verify dashboard link content matches card titles")
    parser.add_argument("--html", required=True, help="Path to dashboard HTML file")
    parser.add_argument("--threshold", type=float, default=0.25, help="Title similarity threshold (default 0.25)")
    args = parser.parse_args()

    html_path = Path(args.html)
    if not html_path.exists():
        print(f"❌ HTML 文件不存在: {html_path}")
        return 1

    html_text = html_path.read_text(encoding="utf-8")
    cards = extract_cards(html_text)
    print(f"共解析到 {len(cards)} 条卡片\n")

    ok = 0
    suspicious = []
    failed = []

    for c in cards:
        print(f"[#{c['num']}] {c['title'][:40]}...", end=" ", flush=True)
        code, page_title = fetch_title(c["url"])
        sim = title_similarity(c["title"], page_title)

        if code != 200:
            print(f"❌ HTTP {code}")
            failed.append({**c, "code": code})
        elif sim < args.threshold:
            print(f"⚠️ 疑似不匹配 (相似度 {sim:.2f})")
            print(f"      卡片: {c['title']}")
            print(f"      网页: {page_title[:80]}")
            suspicious.append({**c, "sim": sim, "page_title": page_title})
        else:
            print(f"✅ (相似度 {sim:.2f})")
            ok += 1
        time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"结果: {ok}/{len(cards)} 通过 | {len(suspicious)} 疑似不匹配 | {len(failed)} 访问失败")

    if suspicious:
        print("\n⚠️ 疑似不匹配：")
        for item in suspicious:
            print(f"  #{item['num']} {item['url']}")
            print(f"      卡片: {item['title']}")
            print(f"      网页: {item['page_title'][:80]}")

    if failed:
        print("\n❌ 访问失败：")
        for item in failed:
            print(f"  #{item['num']} [{item['code']}] {item['url']}")

    return 1 if (suspicious or failed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
