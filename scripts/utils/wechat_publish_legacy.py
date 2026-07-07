#!/usr/bin/env python3.11
"""
生成专业版公众号文章并创建草稿
由每日自动化调用
"""
import json, urllib.request, subprocess, sys
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup
import re

# ── 微信配置 ──
WECHAT_APPID = "wx4bd59eaceaddd0cf"
WECHAT_SECRET = "8181bc6dac277487e0627ac14108b732"
DASHBOARD_URL = "https://32fe3baecb0740f68497897bd222c58c.app.codebuddy.work"
THUMB_MEDIA_ID = "p2qW17rLsuswUSH_hYhMWW6mhxIjBHo-Tv7Uxt599g5d4N-b8xUkK9011P0oPOa3"

# ── 路径 ──
WORKSPACE = Path("/Users/jacob/WorkBuddy/2026-07-01-09-18-45")
ARTICLE_SRC = WORKSPACE / "dashboard_assets/wechat_publish/wechat_ready.html"

# ── 获取 access_token ──
def get_token():
    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={WECHAT_APPID}&secret={WECHAT_SECRET}"
    resp = json.loads(urllib.request.urlopen(urllib.request.Request(url), timeout=15).read())
    return resp.get("access_token")

# ── 解析新闻数据 ──
def parse_news(src_path):
    with open(src_path) as f:
        soup = BeautifulSoup(f.read(), "html.parser")
    sections = []
    cur = None
    
    # Try wechat_ready format first: section headers in <p> with emoji + name + （N条）
    for el in soup.find_all("p"):
        text = el.get_text(strip=True)
        m = re.search(r"([🧠🚀📜⚠️🔬])\s*(.+?)（(\d+)条）", text)
        if m:
            emoji, raw_name, count = m.group(1), m.group(2).strip(), m.group(3)
            cur = {"name": raw_name, "emoji": emoji, "items": []}
            sections.append(cur)
            continue
        # Item cards: bold title line
        if cur is not None and el.get("style") and "font-weight:bold" in (el.get("style", "")):
            title = re.sub(r"^\d+\.\s*", "", text)
            # Find parent card div
            parent = el.find_parent("div", style=lambda v: v and "background:#F9FAFB" in v)
            if parent:
                ps = parent.find_all("p")
                summary = ""
                relevance = ""
                meta = ""
                for p in ps:
                    pt = p.get_text(strip=True)
                    if pt.startswith("💡"):
                        relevance = pt.replace("💡", "").strip()
                    elif pt.startswith("📅"):
                        meta = pt
                    elif p.get("style") and "font-size:15px" in p.get("style", ""):
                        pass  # already got title
                    else:
                        summary = pt
                cur["items"].append({"title": title, "summary": summary, "relevance": relevance, "meta": meta or "—"})
    
    # Fallback: try old format (h3 + 📌)
    if not sections:
        for el in soup.find_all(["h3", "div"]):
            if el.name == "h3":
                text = el.get_text(strip=True)
                m = re.match(r"📌\s*(.+?)\d*（(\d+)条）", text)
                if m:
                    raw = m.group(1).strip()
                    clean = re.sub(r"^[🧠🚀📜⚠️🔬📌]+\s*", "", raw)
                    emoji = raw[0] if raw and not raw[0].isalnum() else "📌"
                    cur = {"name": clean, "emoji": emoji, "items": []}
                    sections.append(cur)
            elif el.name == "div" and cur is not None:
                ps = el.find_all("p")
                if len(ps) >= 2:
                    title = re.sub(r"^\d+\.\s*", "", ps[0].get_text(strip=True))
                    summary = ps[1].get_text(strip=True)
                    meta_txt = ps[2].get_text(strip=True).replace("→ 阅读原文", "").replace("📅 📅", "📅").replace("📅📅", "📅").strip() if len(ps) > 2 else ""
                    relevance = ""
                    rm = re.search(r"「(.+?)」", summary)
                    if rm:
                        relevance = rm.group(1)
                        summary = summary[:rm.start()].strip()
                    cur["items"].append({"title": title, "summary": summary, "relevance": relevance, "meta": meta_txt})
    
    return sections

# ── 生成 HTML ──
COLORS = {
    "模型发布/更新": "#6366F1", "产品发布/更新": "#059669",
    "政策法规": "#D97706", "事故/舆情": "#DC2626", "技术进展/论文突破": "#DB2777",
}

def build_html(sections, total, date_str, weekday_str):
    parts = []
    parts.append(f'''<section style="max-width:677px;margin:0 auto;padding:20px 16px;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;color:#333;line-height:1.8;">
<section style="text-align:center;padding:24px 0 16px;">
<p style="font-size:20px;font-weight:bold;color:#222;margin:0 0 6px;letter-spacing:2px;">🚗 自动驾驶晨报</p>
<p style="font-size:14px;color:#999;margin:0 0 4px;">{date_str} {weekday_str}</p>
<p style="font-size:13px;color:#bbb;margin:0;">{total}条动态 · 5大版块</p>
</section>
<section style="border-top:1px solid #eee;margin:12px 0;"></section>''')

    for sec in sections:
        color = COLORS.get(sec["name"], "#6366F1")
        emoji = sec["emoji"]
        parts.append(f'<section style="margin:28px 0 16px;"><p style="font-size:17px;font-weight:bold;color:#222;margin:0;padding-left:12px;border-left:3px solid {color};">{emoji} {sec["name"]}</p></section>')
        for i, item in enumerate(sec["items"], 1):
            parts.append(f'<section style="background:#FAFBFC;padding:14px 16px;margin:10px 0;border-radius:6px;"><p style="margin:0 0 6px;font-size:16px;font-weight:600;color:#222;">{i}. {item["title"]}</p><p style="margin:0 0 8px;font-size:15px;color:#555;line-height:1.8;">{item["summary"]}</p>')
            if item["relevance"]:
                parts.append(f'<p style="margin:0 0 6px;font-size:13px;color:{color};line-height:1.6;">💡 {item["relevance"]}</p>')
            parts.append(f'<p style="margin:0;font-size:13px;color:#999;">{item["meta"]}</p></section>')

    parts.append(f'''<section style="border-top:1px solid #eee;margin:28px 0 16px;"></section>
<section style="text-align:center;padding:16px 12px;background:#F0F7FF;border-radius:8px;">
<p style="font-size:15px;font-weight:bold;color:#2563EB;margin:0 0 8px;">📊 查看完整仪表盘（含所有原文链接）</p>
<p style="font-size:14px;color:#3B82F6;margin:0 0 8px;word-break:break-all;">{DASHBOARD_URL}</p>
<p style="font-size:13px;color:#6B7280;margin:0;">👆 复制链接到浏览器打开，可点击每条新闻的原文</p>
</section>
<section style="text-align:center;padding:24px 0 8px;"><p style="font-size:12px;color:#ccc;margin:0;">— 由 AI 自动生成 · 每日早 8:00 —</p></section>
</section>''')

    return "\n".join(parts)

# ── 创建微信草稿 ──
def create_draft(token, content_html, digest="20条动态 · 覆盖5大维度", total="20"):
    payload = {
        "articles": [{
            "title": f"自动驾驶晨报 · {datetime.now().strftime('%Y年%m月%d日')}",
            "thumb_media_id": THUMB_MEDIA_ID,
            "author": "自驾车早",
            "digest": digest[:64],
            "show_cover_pic": 0,
            "content": content_html,
            "content_source_url": DASHBOARD_URL,
            "need_open_comment": 0,
            "only_fans_can_comment": 0,
        }]
    }
    url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}"
    req = urllib.request.Request(url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    resp = urllib.request.urlopen(req, timeout=30)
    return json.loads(resp.read().decode("utf-8"))

# ── 主流程 ──
def main():
    # Read the pre-generated WeChat article directly
    with open(ARTICLE_SRC) as f:
        html_content = f.read()

    # Extract total from content
    now = datetime.now()
    date_str = now.strftime("%Y年%m月%d日")
    weekday_str = ["周一","周二","周三","周四","周五","周六","周日"][now.weekday()]

    # Update date in content
    html_content = html_content.replace("2026年7月3日", date_str)
    html_content = html_content.replace("星期五", weekday_str)
    
    # Extract total count
    m = re.search(r'本期精选.*?(\d+)\s*条', html_content)
    total = m.group(1) if m else "20"
    
    # Replace digest
    digest = f"{total}条动态 · 覆盖5大维度"[:64]

    # Save to file
    out_path = WORKSPACE / "dashboard_assets/wechat_publish/wechat_pro.html"
    out_path.write_text(html_content, encoding="utf-8")
    print(f"[OK] Saved to {out_path} ({total} items)")

    # Create WeChat draft
    print("[INFO] Getting WeChat access_token...")
    token = get_token()
    if not token:
        print("[ERROR] Failed to get access_token")
        return False

    print("[INFO] Creating WeChat draft...")
    result = create_draft(token, html_content, digest, total)

    if "media_id" in result:
        print(f"\n✅ 草稿创建成功！media_id: {result['media_id']}")
        print(f"   请到 mp.weixin.qq.com → 草稿箱 → 发布")
        return True
    else:
        print(f"\n❌ 创建失败: {json.dumps(result, ensure_ascii=False, indent=2)}")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
