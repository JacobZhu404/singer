#!/usr/bin/env python3
"""从仪表盘 HTML 中提取卡片的公共模块。

供 generate_links_page.py / verify_content.py 等需要解析仪表盘 HTML 的脚本共用。
"""
import re
from typing import List

SEC_META = {
    "sec-1": ("📊 行业动态", "#059669"),
    "sec-2": ("🚀 产品发布", "#8b5cf6"),
    "sec-3": ("🧠 模型发布", "#f59e0b"),
    "sec-4": ("📄 前沿论文", "#ec4899"),
    "sec-5": ("💡 其他消息", "#ef4444"),
    "sec-6": ("🤖 大模型进展", "#0ea5e9"),
}

_CARD_RE = re.compile(
    r'<a class="card"[^>]*?href="([^"]+)"[^>]*?>'
    r'.*?<span class="card-num"[^>]*>(\d+)</span>'
    r'.*?<span class="card-source">([^<]+)</span>'
    r'.*?<span class="card-date">([^<]+)</span>'
    r'.*?<div class="card-title">([^<]+)</div>'
    r'(?:.*?<div class="card-summary">([^<]*)</div>)?',
    re.S,
)

_SEC_RE = re.compile(r'<div class="sec sec-(\d+)" id="(sec-\d+)">')


def extract_cards(html_text: str) -> List[dict]:
    """提取所有卡片,返回 [{num, title, source, date, url, sec_id, sec_name, sec_color}]。"""
    sec_blocks = list(_SEC_RE.finditer(html_text))
    cards = []
    for i, sm in enumerate(sec_blocks):
        sec_id = sm.group(2)
        sec_name, sec_color = SEC_META.get(sec_id, (f"板块 {sm.group(1)}", "#059669"))
        start = sm.start()
        end = sec_blocks[i + 1].start() if i + 1 < len(sec_blocks) else len(html_text)
        sec_html = html_text[start:end]
        for match in _CARD_RE.findall(sec_html):
            url, num, source, date, title = match[:5]
            summary = match[5] if len(match) > 5 else ""
            cards.append({
                "num": num.strip(),
                "title": title.strip(),
                "source": source.strip(),
                "date": date.strip(),
                "url": url.strip(),
                "summary": summary.strip() if summary else "",
                "sec_id": sec_id,
                "sec_name": sec_name,
                "sec_color": sec_color,
            })
    return cards


def group_by_section(cards: List[dict]) -> List[dict]:
    """将 flat cards 按板块分组,返回 [{name, color, items}]。"""
    sections = []
    seen = set()
    for card in cards:
        sid = card["sec_id"]
        if sid not in seen:
            seen.add(sid)
            sections.append({
                "name": card["sec_name"],
                "color": card["sec_color"],
                "items": [],
            })
        for sec in sections:
            if sec["name"] == card["sec_name"]:
                sec["items"].append(card)
                break
    return [s for s in sections if s["items"]]
