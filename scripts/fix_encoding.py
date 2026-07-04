#!/usr/bin/env python3
"""修复微信草稿中文乱码问题
原因:微信服务器把UTF-8字节误当作Latin-1解析
方案:正文用HTML数字实体,标题/摘要用JSON Unicode转义
"""
import json, re, os, sys, html as html_mod
from pathlib import Path
try:
    import requests
except ImportError:
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests

BASE = Path("/Users/jacob/personal/ad-newsletter")
HTML_FILE = BASE / "data/自动驾驶晨报_20260704.html"
CONFIG = json.loads((BASE / "config.json").read_text())
WX = CONFIG["wechat"]
SECTIONS = CONFIG["newsletter"]["sections"]

APP_ID = WX["app_id"]
APP_SECRET = WX["app_secret"]
MEDIA_ID = "p2qW17rLsuswUSH_hYhMWdfn1s8URuGRwErZjM4nMDHxg_Hyh5W-moAl4Z9MBDeq"
COVER_MEDIA_ID = "p2qW17rLsuswUSH_hYhMWTGAuf-KvC1RNVIGVgaEWow8FhkE8gygxLIGHm233RGV"

SEC_NAME_MAP = {s["id"]: f"{s['icon']} {s['name']}" for s in SECTIONS}
DIM_MAP = {"sec-1": "🏢 企业动向", "sec-2": "🏢 企业动向", "sec-3": "🏢 企业动向",
           "sec-4": "🔬 前沿研究", "sec-5": "🤖 具身智能"}
SEC_SHORT_MAP = {s["id"]: s["name"] for s in SECTIONS}


def get_token():
    r = requests.get("https://api.weixin.qq.com/cgi-bin/token", params={
        "grant_type": "client_credential", "appid": APP_ID, "secret": APP_SECRET
    }, timeout=15)
    return r.json()["access_token"]


def to_html_entities(text: str) -> str:
    """将非ASCII字符转为HTML数字实体,保留HTML标签和style属性中的ASCII内容"""
    result = []
    for ch in text:
        if ord(ch) > 127:
            result.append(f"&#{ord(ch)};")
        else:
            result.append(ch)
    return "".join(result)


def parse_cards(html_text):
    sec_blocks = list(re.finditer(r'<div class="sec sec-\d+" id="(sec-\d+)"', html_text))
    card_pat = re.compile(
        r'<a class="card".*?href="([^"]+)".*?'
        r'<span class="card-num"[^>]*>(\d+)</span>.*?'
        r'<span class="card-source">([^<]+)</span>.*?'
        r'<span class="card-date">([^<]+)</span>.*?'
        r'<div class="card-title">([^<]+)</div>.*?'
        r'<div class="card-summary">([^<]+)</div>',
        re.S
    )
    cards = []
    for i, sm in enumerate(sec_blocks):
        sec_id = sm.group(1)
        sec_start = sm.start()
        if i + 1 < len(sec_blocks):
            sec_end = sec_blocks[i + 1].start()
        else:
            fm = re.search(r'<div class="footer"', html_text)
            sec_end = fm.start() if fm else len(html_text)
        sec_html = html_text[sec_start:sec_end]
        sec_name = SEC_NAME_MAP.get(sec_id, sec_id)
        dim = DIM_MAP.get(sec_id, "📌 其他")
        sec_short = SEC_SHORT_MAP.get(sec_id, sec_id)
        for url, num, source, date, ctitle, summary in card_pat.findall(sec_html):
            cards.append({
                "sec_name": sec_name, "sec_short": sec_short, "dim": dim,
                "url": url, "num": num, "source": source, "date": date,
                "title": ctitle, "summary": summary,
            })
    return cards


def build_article():
    html_text = HTML_FILE.read_text("utf-8")
    
    m = re.search(r'(\d{4})年(\d+)月(\d+)日.*?星期([一二三四五六日天])', html_text)
    date_display = f"{m.group(1)}年{m.group(2)}月{m.group(3)}日 · 星期{m.group(4)}" if m else "2026年7月4日"
    date_short = f"{m.group(2)}月{m.group(3)}日" if m else "7月4日"
    title = f"自动驾驶晨报 {date_short}"
    
    m_total = re.search(r'精选\s*<strong[^>]*>(\d+)</strong>\s*条', html_text)
    total = m_total.group(1) if m_total else "0"
    digest = f"今日{total}条自动驾驶动态精选"
    
    cards = parse_cards(html_text)
    
    overview_rows = ""
    for c in cards:
        overview_rows += (
            f'<tr>'
            f'<td data-colwidth="43" style="padding:6px 2px;vertical-align:middle;text-align:center;">'
            f'<span style="display:inline-block;width:16px;height:16px;line-height:16px;text-align:center;background:#059669;color:#fff;border-radius:50%;font-size:9px;font-weight:bold;">{html_mod.escape(c["num"])}</span></td>'
            f'<td style="padding:4px 4px;font-size:12px;color:#1a1a2e;line-height:1.4;">{html_mod.escape(c["title"][:25])}</td>'
            f'<td style="padding:6px 2px;font-size:10px;color:#059669;white-space:nowrap;vertical-align:middle;">{html_mod.escape(c["sec_short"])}</td>'
            f'<td style="padding:6px 2px;font-size:10px;color:#6b7280;white-space:nowrap;vertical-align:middle;">{html_mod.escape(c["source"])}</td>'
            f'</tr>'
        )
    
    parts = []
    parts.append(
        f'<section style="padding:14px 12px;background:linear-gradient(135deg,#064e3b,#059669);'
        f'color:#fff;border-radius:8px;margin-bottom:14px;">'
        f'<p style="margin:0;font-size:11px;letter-spacing:2px;opacity:.7;">AUTONOMOUS DRIVING · DAILY BRIEF</p>'
        f'<p style="margin:6px 0;font-size:24px;font-weight:bold;">{title}</p>'
        f'<p style="margin:0;font-size:14px;opacity:.85;">{date_display}</p>'
        f'<p style="margin:10px 0 0;font-size:15px;">本期精选 '
        f'<strong style="font-size:22px;color:#a7f3d0;">{total}</strong> 条</p>'
        f'</section>'
    )
    parts.append(
        f'<section style="margin:0 0 14px;padding:12px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;">'
        f'<p style="margin:0 0 8px;font-size:14px;font-weight:bold;color:#047857;">📋 今日概览 · {total} 条</p>'
        f'<table style="border-collapse:collapse;min-width:458px;"><tbody>'
        f'{overview_rows}'
        f'</tbody></table></section>'
    )
    
    last_sec = None
    for c in cards:
        if c["sec_name"] != last_sec:
            parts.append(
                f'<p style="margin:16px 0 8px;padding-left:8px;border-left:4px solid #059669;'
                f'font-size:18px;font-weight:bold;color:#047857;">{c["sec_name"]}</p>'
            )
            last_sec = c["sec_name"]
        clean_date = c["date"].replace("🔥", "").strip()
        parts.append(
            f'<section style="margin:10px 0;padding:10px 12px;border:1px solid #e5e7eb;'
            f'border-radius:8px;background:#fafafa;">'
            f'<p style="margin:0 0 4px;font-size:13px;color:#6b7280;">'
            f'<span style="display:inline-block;min-width:22px;height:22px;line-height:22px;'
            f'text-align:center;background:#059669;color:#fff;border-radius:50%;'
            f'font-weight:bold;margin-right:6px;">{html_mod.escape(c["num"])}</span>'
            f'{html_mod.escape(c["source"])} · {html_mod.escape(clean_date)} {c["dim"]}</p>'
            f'<p style="margin:4px 0;font-size:16px;font-weight:bold;color:#111827;">'
            f'{html_mod.escape(c["title"])}</p>'
            f'<p style="margin:4px 0;font-size:14px;color:#374151;line-height:1.7;">'
            f'{html_mod.escape(c["summary"])}</p>'
            f'</section>'
        )
    
    parts.append(
        f'<section style="margin:16px 0 0;padding:12px;background:#f0fdf4;border:1px solid #bbf7d0;'
        f'border-radius:8px;text-align:center;">'
        f'<p style="margin:0;font-size:14px;font-weight:600;color:#065f46;">📎 全部原文链接汇总</p>'
        f'<p style="margin:6px 0 0;font-size:13px;color:#4a5568;">'
        f'点击文章底部「阅读原文」查看所有原文链接</p>'
        f'</section>'
    )
    
    content = "\n".join(parts)
    # 关键：把正文里所有非ASCII字符转成HTML数字实体，避免微信服务器编码误判
    content_entities = to_html_entities(content)
    
    return {
        "title": title,
        "digest": digest,
        "content": content_entities,
    }


def update_draft(token, article, thumb_media_id):
    # 读取旧草稿获取已有字段
    r = requests.post("https://api.weixin.qq.com/cgi-bin/draft/get",
        params={"access_token": token},
        data=json.dumps({"media_id": MEDIA_ID}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"}, timeout=30)
    old = r.json()
    old_article = old["news_item"][0]
    
    # 关键：标题和摘要用JSON Unicode转义,不用裸UTF-8字节
    new_article = {
        "title": article["title"],
        "digest": article["digest"],
        "content": article["content"],
        "content_source_url": old_article.get("content_source_url", ""),
        "thumb_media_id": thumb_media_id,
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
    }
    
    # 用 ensure_ascii=True 让标题/摘要变成 \uXXXX,正文已经是HTML实体(ASCII安全)
    body = json.dumps({
        "media_id": MEDIA_ID,
        "index": 0,
        "articles": new_article
    }, ensure_ascii=True)
    
    r2 = requests.post("https://api.weixin.qq.com/cgi-bin/draft/update",
        params={"access_token": token},
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=30)
    res = r2.json()
    assert res.get("errcode") == 0, f"更新失败: {res}"
    print("✅ 草稿已重新编码并更新")


def main():
    print("📖 重新生成正文...")
    article = build_article()
    print(f"   标题: {article['title']}")
    print(f"   摘要: {article['digest']}")
    print(f"   正文长度: {len(article['content'])} 字符")
    
    print("\n🔑 获取token...")
    token = get_token()
    
    print("\n📤 更新草稿...")
    # 复用当前封面
    update_draft(token, article, COVER_MEDIA_ID)
    
    print("\n🎉 完成! 请刷新公众号草稿箱查看。")


if __name__ == "__main__":
    main()
