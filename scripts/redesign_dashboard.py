#!/usr/bin/env python3.11
"""完全重构仪表盘布局 —> 移动优先、紧凑、卡片式"""
from bs4 import BeautifulSoup
import re
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
SRC = _BASE / "data/dashboard_20260703.html"
DST = _BASE / "data/dashboard_20260703.html"

with open(SRC) as f:
    soup = BeautifulSoup(f.read(), "html.parser")

# Extract data
sections_data = []
for sec in soup.select(".section"):
    title_el = sec.select_one(".section-title")
    if not title_el:
        continue
    raw = title_el.get_text(strip=True)
    clean = re.sub(r"\d+$", "", raw).strip()
    cards = []
    for card in sec.select(".card"):
        num = card.select_one(".card-number")
        title = card.select_one(".card-title")
        summary = card.select_one(".card-summary")
        source = card.select_one(".card-source")
        date = card.select_one(".card-date")
        relevance = card.select_one(".card-relevance")
        link = card.select_one(".card-link")
        tags = [t.get_text(strip=True) for t in card.select(".card-tag")]
        cards.append({
            "num": num.get_text(strip=True) if num else "",
            "title": title.get_text(strip=True) if title else "",
            "summary": summary.get_text(strip=True) if summary else "",
            "source": source.get_text(strip=True) if source else "",
            "date": date.get_text(strip=True) if date else "",
            "relevance": relevance.get_text(strip=True) if relevance else "",
            "url": link.get("href", "") if link else "",
            "tags": tags,
        })
    if cards:
        sections_data.append({"name": clean, "cards": cards})

total = sum(len(s["cards"]) for s in sections_data)

# Icon mapping
ICONS = {"模型发布/更新":"🧠","产品发布/更新":"🚀","政策法规":"📜","事故/舆情":"⚠️","技术进展/论文突破":"🔬"}
COLORS = {"模型发布/更新":"#6366F1","产品发布/更新":"#059669","政策法规":"#D97706","事故/舆情":"#DC2626","技术进展/论文突破":"#DB2777"}

# ── Generate compact HTML ──
parts = []
parts.append('''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<title>自动驾驶晨报 | 2026年7月3日</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;background:#f4f6f9;color:#333;line-height:1.5;font-size:14px;-webkit-font-smoothing:antialiased}

/* Header - compact bar */
.header{background:linear-gradient(135deg,#064e3b,#059669);padding:14px 14px;text-align:center}
.header .sub{font-size:10px;color:#a7f3d0;letter-spacing:1px;opacity:.7;margin-bottom:2px}
.header h1{font-size:18px;font-weight:700;color:#fff;margin:0}
.header .meta{font-size:11px;color:#d1fae5;margin-top:4px;opacity:.85}

/* Section */
.sec{margin:10px 0}
.sec-title{font-size:14px;font-weight:700;color:#111;padding:8px 14px;margin:0;border-bottom:2px solid #e5e7eb;background:#fff}
.sec-title .badge{display:inline-block;font-size:10px;background:#e5e7eb;color:#666;padding:1px 8px;border-radius:8px;margin-left:4px;font-weight:600}

/* Cards - compact feed style */
.card{display:block;padding:10px 14px;margin:6px 14px;background:#fff;border-radius:8px;text-decoration:none;color:inherit;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.card:active{background:#f8f9fa}
.card-top{display:flex;align-items:center;gap:6px;margin-bottom:4px}
.card-num{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;font-size:9px;font-weight:700;color:#fff;flex-shrink:0}
.card-source{font-size:10px;color:#059669;font-weight:600}
.card-date{font-size:10px;color:#999;margin-left:auto}
.card-title{font-size:13px;font-weight:600;color:#111;margin-bottom:2px;line-height:1.4}
.card-summary{font-size:12px;color:#666;line-height:1.5;margin-bottom:4px}
.card-rel{font-size:11px;color:#3b82f6;padding:4px 8px;background:#eff6ff;border-radius:4px;margin-bottom:4px;line-height:1.4}
.card-tags{display:flex;gap:4px;flex-wrap:wrap}
.card-tag{font-size:9px;color:#888;background:#f3f4f6;padding:1px 6px;border-radius:4px}

/* Footer */
.footer{text-align:center;padding:16px 14px;font-size:11px;color:#999}
.footer a{color:#059669;text-decoration:none}
</style>
</head>
<body>''')

# Header
parts.append('''<div class="header">
  <div class="sub">AUTONOMOUS DRIVING · DAILY BRIEF</div>
  <h1>🚗 自动驾驶晨报</h1>
  <div class="meta">2026年7月3日 · 周五 · 精选 <strong style="color:#a7f3d0">''' + str(total) + '''</strong> 条动态</div>
</div>''')

# Sections
for sec in sections_data:
    icon = ICONS.get(sec["name"], "📌")
    color = COLORS.get(sec["name"], "#6366F1")
    parts.append(f'<div class="sec"><div class="sec-title">{icon} {sec["name"]}<span class="badge">{len(sec["cards"])}</span></div>')
    for c in sec["cards"]:
        num_color = color
        parts.append(f'<a class="card" href="{c["url"]}" target="_blank" rel="noopener noreferrer">')
        parts.append(f'<div class="card-top"><span class="card-num" style="background:{num_color}">{c["num"]}</span><span class="card-source">{c["source"]}</span><span class="card-date">{c["date"].replace("🔥","").replace("📅","").strip()}</span></div>')
        parts.append(f'<div class="card-title">{c["title"]}</div>')
        if c["summary"]:
            parts.append(f'<div class="card-summary">{c["summary"]}</div>')
        if c["relevance"]:
            parts.append(f'<div class="card-rel">💡 {c["relevance"]}</div>')
        if c["tags"]:
            tag_html = "".join(f'<span class="card-tag">{t}</span>' for t in c["tags"][:4])
            parts.append(f'<div class="card-tags">{tag_html}</div>')
        parts.append('</a>')
    parts.append('</div>')

# Footer
parts.append(f'<div class="footer">由 AI 自动生成 · <a href="https://32fe3baecb0740f68497897bd222c58c.app.codebuddy.work">完整链接汇总 →</a></div>')
parts.append('</body></html>')

html = "\n".join(parts)
DST.write_text(html, encoding="utf-8")

# Stats
total_cards = sum(len(s["cards"]) for s in sections_data)
print(f"✅ 重构完成: {len(sections_data)} 版块, {total_cards} 条")
print(f"   HTML: {DST}")
