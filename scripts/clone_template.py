#!/usr/bin/env python3.11
"""
1:1 复刻昨天公众号文章排版 + 今日内容
精确使用已发布文章的 inline style
"""
import json, urllib.request, re, html as html_mod
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup

DASHBOARD_PATH = "/Users/jacob/WorkBuddy/2026-07-01-09-18-45/dashboard_assets/index.html"
OUTPUT_DIR = Path("/Users/jacob/WorkBuddy/2026-07-01-09-18-45/dashboard_assets/wechat_publish")
DASHBOARD_URL = "https://32fe3baecb0740f68497897bd222c58c.app.codebuddy.work"

def extract_cards(html_path):
    with open(html_path) as f:
        soup = BeautifulSoup(f.read(), "html.parser")
    
    sections = []
    all_cards = []
    sec_id = 0
    
    for sec in soup.select(".section"):
        title_el = sec.select_one(".section-title")
        if not title_el:
            continue
        raw = title_el.get_text(strip=True)
        clean = re.sub(r"\d+$", "", raw).strip()
        cards_in_sec = sec.select(".card")
        if not cards_in_sec:
            continue
        
        sec_id += 1
        sec_cards = []
        for card in cards_in_sec:
            all_cards.append(len(all_cards) + 1)
            num = len(all_cards)
            title = (card.select_one(".card-title") or card).get_text(strip=True)
            summary = (card.select_one(".card-summary") or card).get_text(strip=True)
            source = (card.select_one(".card-source") or card).get_text(strip=True)
            date = (card.select_one(".card-date") or card).get_text(strip=True)
            link = card.select_one(".card-link")
            url = link.get("href", "") if link else ""
            
            sec_cards.append({
                "num": num, "title": title, "summary": summary,
                "source": source, "date": date, "url": url
            })
        
        sections.append({"name": clean, "count": len(sec_cards), "cards": sec_cards})
    
    return sections, len(all_cards)

def build_article(sections, total, date_str):
    weekday_map = ["周一","周二","周三","周四","周五","周六","周日"]
    weekday = weekday_map[datetime.now().weekday()]
    
    parts = []
    
    # ════════ HEADER: 渐变绿色背景 ════════
    parts.append(
        f'<section style="padding:14px 12px;background:linear-gradient(135deg,#064e3b,#059669);'
        f'color:#fff;border-radius:8px;margin-bottom:14px;">'
        f'<p style="margin:0;font-size:11px;letter-spacing:2px;opacity:.7;">'
        f'AUTONOMOUS DRIVING · DAILY BRIEF</p>'
        f'<p style="margin:6px 0;font-size:24px;font-weight:bold;">自动驾驶晨报</p>'
        f'<p style="margin:0;font-size:14px;opacity:.85;">{date_str} · {weekday}</p>'
        f'<p style="margin:10px 0 0;font-size:15px;">本期精选 '
        f'<strong style="font-size:22px;color:#a7f3d0;">{total}</strong> 条</p>'
        f'</section>'
    )
    
    # ════════ 概览 TABLE ════════
    rows = []
    for sec in sections:
        for c in sec["cards"]:
            # 板块缩写
            sec_short = sec["name"]
            if "模型" in sec_short: sec_abbr = "模型发布"
            elif "产品" in sec_short: sec_abbr = "产品发布"
            elif "政策" in sec_short: sec_abbr = "政策法规"
            elif "事故" in sec_short or "舆情" in sec_short: sec_abbr = "事故舆情"
            elif "技术" in sec_short or "论文" in sec_short: sec_abbr = "前沿论文"
            else: sec_abbr = sec_short
            
            rows.append(
                f'<tr>'
                f'<td data-colwidth="43" style="padding:6px 2px;vertical-align:middle;text-align:center;">'
                f'<span style="display:inline-block;width:16px;height:16px;line-height:16px;text-align:center;'
                f'background:#059669;color:#fff;border-radius:50%;font-size:9px;font-weight:bold;">{c["num"]}</span></td>'
                f'<td data-colwidth="365" style="padding:4px 4px;font-size:12px;color:#1a1a2e;line-height:1.4;">'
                f'<section>{html_mod.escape(c["title"][:25])}</section></td>'
                f'<td style="padding:6px 2px;font-size:10px;color:#059669;white-space:nowrap;vertical-align:middle;">'
                f'<section>{sec_abbr}</section></td>'
                f'<td style="padding:6px 2px;font-size:10px;color:#6b7280;white-space:nowrap;vertical-align:middle;">'
                f'<section>{html_mod.escape(c["source"])}</section></td>'
                f'</tr>'
            )
    
    parts.append(
        f'<section style="margin:0 0 14px;padding:12px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;">'
        f'<p style="margin:0 0 8px;font-size:14px;font-weight:bold;color:#047857;">📋 今日概览 · {total} 条</p>'
        f'<table style="border-collapse:collapse;min-width:458px;"><tbody>'
        f'{"".join(rows)}'
        f'</tbody></table></section>'
    )
    
    # ════════ 板块详情 ════════
    for sec in sections:
        parts.append(
            f'<p style="margin:16px 0 8px;padding-left:8px;border-left:4px solid #059669;'
            f'font-size:18px;font-weight:bold;color:#047857;">{sec["name"]} ({sec["count"]}条)</p>'
        )
        
        # 维度映射(匹配 section 实际名称,可能含 emoji)
        dim_map = {
            "模型发布/更新": "🏢 企业动向",
            "🧠模型发布/更新": "🏢 企业动向",
            "产品发布/更新": "🏢 企业动向",
            "🚀产品发布/更新": "🏢 企业动向",
            "政策法规": "📜 政策法规",
            "📜政策法规": "📜 政策法规",
            "事故/舆情": "⚠️ 事故舆情",
            "⚠️事故/舆情": "⚠️ 事故舆情",
            "技术进展/论文突破": "🔬 前沿研究",
            "🔬技术进展/论文突破": "🔬 前沿研究",
        }
        dim = dim_map.get(sec["name"], "📌 其他")
        
        for c in sec["cards"]:
            # 去掉日期中的 📅 前缀
            clean_date = c["date"].replace("📅", "").strip()
            parts.append(
                f'<section style="margin:10px 0;padding:10px 12px;border:1px solid #e5e7eb;'
                f'border-radius:8px;background:#fafafa;">'
                f'<p style="margin:0 0 4px;font-size:13px;color:#6b7280;">'
                f'<span style="display:inline-block;min-width:22px;height:22px;line-height:22px;'
                f'text-align:center;background:#059669;color:#fff;border-radius:50%;'
                f'font-weight:bold;margin-right:6px;">{c["num"]}</span>'
                f'{html_mod.escape(c["source"])} · {html_mod.escape(clean_date.replace("🔥","").replace("🔥","").strip())} {dim}</p>'
                f'<p style="margin:4px 0;font-size:16px;font-weight:bold;color:#111827;">'
                f'{html_mod.escape(c["title"])}</p>'
                f'<p style="margin:4px 0;font-size:14px;color:#374151;line-height:1.7;">'
                f'{html_mod.escape(c["summary"])}</p>'
                f'</section>'
            )
    
    # ════════ FOOTER ════════
    parts.append(
        f'<section style="margin:16px 0 0;padding:12px;background:#f0fdf4;border:1px solid #bbf7d0;'
        f'border-radius:8px;text-align:center;">'
        f'<p style="margin:0;font-size:14px;font-weight:600;color:#065f46;">📎 全部原文链接汇总</p>'
        f'<p style="margin:6px 0 0;font-size:13px;color:#4a5568;">'
        f'点击文章底部「阅读原文」查看所有 {total} 条原文链接</p>'
        f'</section>'
    )
    
    return "\n".join(parts)


def main():
    today = datetime.now()
    date_str = today.strftime("%Y年%m月%d日")
    
    print("📖 提取今日数据...")
    sections, total = extract_cards(DASHBOARD_PATH)
    print(f"   共 {total} 条, {len(sections)} 个版块")
    
    print("\n📝 生成1:1复刻版文章...")
    html_content = build_article(sections, total, date_str)
    
    # 保存
    out_path = OUTPUT_DIR / "wechat_clone.html"
    out_path.write_text(html_content, encoding="utf-8")
    print(f"   ✅ {out_path}")
    
    # 预览页
    preview_path = OUTPUT_DIR / "wechat_clone_preview.html"
    preview_html = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>自动驾驶晨报 · 复刻版 · {date_str}</title>
<style>body{{margin:0;padding:20px;background:#f5f5f5;font-family:-apple-system,sans-serif;}}
.container{{max-width:677px;margin:0 auto;background:#fff;padding:20px;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,0.08);}}
.banner{{text-align:center;padding:12px;background:#F0F7FF;border-radius:8px;margin-bottom:16px;font-size:13px;color:#2563EB;}}</style>
</head><body>
<div class="container">
<div class="banner">📋 1:1 复刻昨天发布版本 · 渐变绿header + 概览table + 卡片详情</div>
{html_content}
</div></body></html>'''
    preview_path.write_text(preview_html, encoding="utf-8")
    print(f"   ✅ 预览页: {preview_path}")
    
    # 部署预览
    import subprocess
    deploy_dir = Path("/tmp/wechat_clone_deploy")
    deploy_dir.mkdir(parents=True, exist_ok=True)
    (deploy_dir / "index.html").write_text(preview_html, encoding="utf-8")
    
    print("\n🌐 推送到CloudStudio...")
    # Use workbuddy_cloudstudio_deploy - we'll do this outside
    
    print(f"\n🎉 完成! 文件已就绪:")
    print(f"   文章: {out_path}")
    print(f"   预览: {preview_path}")

if __name__ == "__main__":
    main()
