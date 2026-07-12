#!/usr/bin/env python3
"""从 HTML 仪表盘生成格式精美的原文链接汇总页。

用法:
    python3 scripts/generate_links_page.py --html data/自动驾驶晨报_YYYYMMDD.html
    # 输出: data/links_YYYYMMDD.html
"""
import argparse
import re
import html as html_mod
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from utils.parse_cards import extract_cards, group_by_section


def build_links_html(sections, total, date_display: str):
    """生成与仪表盘同风格的链接汇总页 HTML。"""

    # Header + CSS
    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">',
        "<title>自动驾驶晨报 · 原文链接汇总</title>",
        "<style>",
        "*{margin:0;padding:0;box-sizing:border-box}",
        'body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;background:#f4f6f9;color:#333;line-height:1.5;font-size:14px;-webkit-font-smoothing:antialiased}',
        ".header{background:linear-gradient(135deg,#064e3b,#059669);padding:16px 14px;text-align:center}",
        ".header .sub{font-size:10px;color:#a7f3d0;letter-spacing:1px;opacity:.7;margin-bottom:2px}",
        ".header h1{font-size:20px;font-weight:700;color:#fff;margin:0}",
        ".header .meta{font-size:11px;color:#d1fae5;margin-top:4px;opacity:.85}",
        ".sec{margin:10px 14px;background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.04);overflow:hidden}",
        ".sec-title{font-size:15px;font-weight:700;padding:10px 12px;border-bottom:2px solid #e5e7eb;display:flex;align-items:center;gap:6px}",
        ".card{display:block;padding:10px 12px;text-decoration:none;color:inherit;border-bottom:1px solid #f3f4f6}",
        ".card:last-child{border-bottom:none}",
        ".card:active{background:#f8f9fa}",
        ".card-top{display:flex;align-items:center;gap:6px;margin-bottom:4px;flex-wrap:wrap}",
        ".card-num{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;font-size:9px;font-weight:700;color:#fff;flex-shrink:0}",
        ".card-source{font-size:10px;color:#059669;font-weight:600}",
        ".card-date{font-size:10px;color:#999;margin-left:auto}",
        ".card-title{font-size:13px;font-weight:600;color:#111;margin-bottom:3px;line-height:1.4}",
        ".footer{text-align:center;padding:16px 14px;font-size:11px;color:#999;background:#f0fdf4;margin:10px 14px;border-radius:8px;border:1px solid #bbf7d0}",
        "</style>",
        "</head>",
        "<body>",
        '<div class="header">',
        '  <div class="sub">AUTONOMOUS DRIVING · SOURCE LINKS</div>',
        '  <h1>原文链接汇总</h1>',
        f'  <div class="meta">{html_mod.escape(date_display)} · 共 <strong style="color:#a7f3d0">{total}</strong> 篇原文</div>',
        "</div>",
    ]

    for sec in sections:
        sec_name = html_mod.escape(sec["name"])
        sec_color = sec["color"]
        parts.append(
            f'<div class="sec">'
            f'<div class="sec-title" style="border-left:3px solid {sec_color}">{sec_name}</div>'
        )
        for item in sec["items"]:
            num = html_mod.escape(item["num"])
            title = html_mod.escape(item["title"])
            source = html_mod.escape(item["source"])
            date = html_mod.escape(item["date"])
            url = html_mod.escape(item["url"])
            parts.append(
                f'<a class="card" href="{url}" target="_blank" rel="noopener noreferrer">'
                f'<div class="card-top">'
                f'<span class="card-num" style="background:{sec_color}">{num}</span>'
                f'<span class="card-source">{source}</span>'
                f'<span class="card-date">{date}</span>'
                f'</div>'
                f'<div class="card-title">{title} →</div>'
                f'</a>'
            )
        parts.append("</div>")

    parts.append(
        '<div class="footer">'
        '  📎 点击卡片即可跳转到原文<br>\n'
        '  由 WorkBuddy 自动生成'
        '</div>'
    )
    parts.extend(["</body>", "</html>"])

    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Generate links summary page from dashboard HTML")
    parser.add_argument("--html", required=True, help="Path to dashboard HTML file")
    args = parser.parse_args()

    html_path = Path(args.html)
    if not html_path.exists():
        print(f"❌ HTML 文件不存在: {html_path}")
        return 1

    html_text = html_path.read_text(encoding="utf-8")

    # 提取日期
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', html_text)
    date_display = f"{m.group(1)}年{m.group(2)}月{m.group(3)}日" if m else datetime.now().strftime("%Y年%m月%d日")

    cards = extract_cards(html_text)
    sections = group_by_section(cards)
    total = len(cards)
    if not sections:
        print("⚠️ 未从 HTML 中解析到任何卡片")
        return 1

    links_html = build_links_html(sections, total, date_display)

    # 输出到 data/links_YYYYMMDD.html
    date_str_match = re.search(r'_(\d{8})\.html$', html_path.name)
    date_str = date_str_match.group(1) if date_str_match else datetime.now().strftime("%Y%m%d")
    out_path = html_path.parent / f"links_{date_str}.html"
    out_path.write_text(links_html, encoding="utf-8")

    print(f"✅ 已生成链接汇总页: {out_path}")
    print(f"   共 {total} 条链接，{len(sections)} 个板块")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
