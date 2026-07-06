#!/usr/bin/env python3
"""推送今日晨报到微信公众号草稿箱"""
import json, re, os, sys, html as html_mod
from pathlib import Path
from datetime import datetime

# 复用独立的链接页生成模块，确保与命令行脚本输出一致
sys.path.insert(0, str(Path(__file__).parent))
from generate_links_page import extract_cards, build_links_html
try:
    import requests
except ImportError:
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests

BASE = Path(__file__).resolve().parent.parent
_DATE_STR = datetime.now().strftime("%Y%m%d")
HTML_FILE = BASE / f"data/自动驾驶晨报_{_DATE_STR}.html"
CONFIG = json.loads((BASE / "config.json").read_text())
WX = CONFIG["wechat"]

APP_ID = WX["app_id"]
APP_SECRET = WX["app_secret"]
SECTIONS = CONFIG["newsletter"]["sections"]

# 板块名映射: sec-id → "图标 名称"
SEC_NAME_MAP = {s["id"]: f"{s['icon']} {s['name']}" for s in SECTIONS}
# 维度映射: sec-id → 维度文字
DIM_MAP = {"sec-1": "🏢 企业动向", "sec-2": "🏢 企业动向", "sec-3": "🏢 企业动向",
           "sec-4": "🔬 前沿研究", "sec-5": "🤖 具身智能"}
# sec-id → 板块简称（用于概览表）
SEC_SHORT_MAP = {s["id"]: s["name"] for s in SECTIONS}


def get_token():
    r = requests.get("https://api.weixin.qq.com/cgi-bin/token", params={
        "grant_type": "client_credential", "appid": APP_ID, "secret": APP_SECRET
    }, timeout=15)
    d = r.json()
    assert "access_token" in d, f"token失败: {d}"
    print(f"✅ token获取成功")
    return d["access_token"]


def parse_all_cards(html_text):
    """从HTML中提取所有卡片,并确定每条卡片所属板块"""
    # 先找到所有板块的起始和结束位置
    sec_blocks = list(re.finditer(
        r'<div class="sec sec-\d+" id="(sec-\d+)"',
        html_text
    ))
    
    # 卡片正则
    card_pat = re.compile(
        r'<a class="card".*?href="([^"]+)".*?'
        r'<span class="card-num"[^>]*>(\d+)</span>.*?'
        r'<span class="card-source">([^<]+)</span>.*?'
        r'<span class="card-date">([^<]+)</span>.*?'
        r'<div class="card-title">([^<]+)</div>.*?'
        r'<div class="card-summary">([^<]+)</div>',
        re.S
    )
    
    all_cards_data = []
    
    for i, sm in enumerate(sec_blocks):
        sec_id = sm.group(1)
        sec_start = sm.start()
        # 板块结束位置 = 下一个板块开始 或 footer开始
        if i + 1 < len(sec_blocks):
            sec_pat = re.compile(rf'<div class="sec sec-\d+" id="(?:sec-\d+)">')
            next_m = sec_pat.search(html_text, sec_blocks[i+1].start())
            if next_m:
                sec_end = next_m.start()
            else:
                sec_end = len(html_text)
        else:
            # 最后一个板块: 到footer之前
            fm = re.search(r'<div class="footer"', html_text)
            sec_end = fm.start() if fm else len(html_text)
        
        sec_html = html_text[sec_start:sec_end]
        
        sec_name = SEC_NAME_MAP.get(sec_id, sec_id)
        dim = DIM_MAP.get(sec_id, "📌 其他")
        sec_short = SEC_SHORT_MAP.get(sec_id, sec_id)
        
        cards = card_pat.findall(sec_html)
        
        for url, num, source, date, ctitle, summary in cards:
            all_cards_data.append({
                "sec_name": sec_name,
                "sec_short": sec_short,
                "dim": dim,
                "url": url,
                "num": num,
                "source": source,
                "date": date,
                "title": ctitle,
                "summary": summary,
            })
    
    return all_cards_data


def parse():
    html = HTML_FILE.read_text("utf-8")
    
    # 日期
    m = re.search(r'(\d{4})年(\d+)月(\d+)日.*?星期([一二三四五六日天])', html)
    date_display = f"{m.group(1)}年{m.group(2)}月{m.group(3)}日 · 星期{m.group(4)}" if m else "2026年7月4日"
    date_short = f"{m.group(2)}月{m.group(3)}日" if m else "7月4日"
    title = f"自动驾驶晨报 {date_short}"
    
    # 总条数
    m_total = re.search(r'精选\s*<strong[^>]*>(\d+)</strong>\s*条', html)
    total = m_total.group(1) if m_total else "0"
    
    digest = f"今日{total}条自动驾驶动态精选"
    if len(digest.encode()) > 54:
        digest = f"今日{total}条自动驾驶动态"
    
    # 提取全部卡片（按板块分组）
    cards = parse_all_cards(html)
    print(f"   解析到 {len(cards)} 条卡片")
    
    # 构建概览行
    overview_rows = ""
    for c in cards:
        num_e = html_mod.escape(c["num"])
        title_e = html_mod.escape(c["title"])
        src_e = html_mod.escape(c["source"])
        sec_s = html_mod.escape(c["sec_short"])
        overview_rows += (
            f'<tr>'
            f'<td data-colwidth="43" style="padding:6px 2px;vertical-align:middle;text-align:center;">'
            f'<span style="display:inline-block;width:16px;height:16px;line-height:16px;text-align:center;background:#059669;color:#fff;border-radius:50%;font-size:9px;font-weight:bold;">{num_e}</span></td>'
            f'<td style="padding:4px 4px;font-size:12px;color:#1a1a2e;line-height:1.4;">{title_e}</td>'
            f'<td style="padding:6px 2px;font-size:10px;color:#059669;white-space:nowrap;vertical-align:middle;">{sec_s}</td>'
            f'<td style="padding:6px 2px;font-size:10px;color:#6b7280;white-space:nowrap;vertical-align:middle;">{src_e}</td>'
            f'</tr>'
        )
    
    # 构建正文
    body_parts = []
    
    # Header
    body_parts.append(
        f'<section style="padding:14px 12px;background:linear-gradient(135deg,#064e3b,#059669);'
        f'color:#fff;border-radius:8px;margin-bottom:14px;">'
        f'<p style="margin:0;font-size:11px;letter-spacing:2px;opacity:.7;">AUTONOMOUS DRIVING · DAILY BRIEF</p>'
        f'<p style="margin:6px 0;font-size:24px;font-weight:bold;">自动驾驶晨报</p>'
        f'<p style="margin:0;font-size:14px;opacity:.85;">{date_display}</p>'
        f'<p style="margin:10px 0 0;font-size:15px;">本期精选 '
        f'<strong style="font-size:22px;color:#a7f3d0;">{total}</strong> 条</p>'
        f'</section>'
    )
    
    # 概览表
    body_parts.append(
        f'<section style="margin:0 0 14px;padding:12px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;">'
        f'<p style="margin:0 0 8px;font-size:14px;font-weight:bold;color:#047857;">📋 今日概览 · {total} 条</p>'
        f'<table style="border-collapse:collapse;min-width:458px;"><tbody>'
        f'{overview_rows}'
        f'</tbody></table></section>'
    )
    
    # 板块详情
    last_sec_name = None
    for c in cards:
        if c["sec_name"] != last_sec_name:
            body_parts.append(
                f'<p style="margin:16px 0 8px;padding-left:8px;border-left:4px solid #059669;'
                f'font-size:18px;font-weight:bold;color:#047857;">{c["sec_name"]}</p>'
            )
            last_sec_name = c["sec_name"]
        clean_date = c["date"].replace("🔥", "").strip()
        body_parts.append(
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
    
    # Footer
    body_parts.append(
        f'<section style="margin:16px 0 0;padding:12px;background:#f0fdf4;border:1px solid #bbf7d0;'
        f'border-radius:8px;text-align:center;">'
        f'<p style="margin:0;font-size:14px;font-weight:600;color:#065f46;">📎 全部原文链接汇总</p>'
        f'<p style="margin:6px 0 0;font-size:13px;color:#4a5568;">'
        f'点击文章底部「阅读原文」查看所有原文链接</p>'
        f'</section>'
    )
    
    content = "\n".join(body_parts)
    
    return {
        "title": title,
        "digest": digest,
        "content": content,
        "total": total,
    }


def upload_cover(token):
    """上传封面图到微信素材库。优先使用预生成的高质量封面(如ImageGen产物),否则降级为PIL占位图。"""
    date_str = _DATE_STR  # YYYYMMDD
    
    # 查找预生成封面: 今日ImageGen > 今日cover_fixed > 今日cover
    candidates = []
    # ImageGen今日生成的文件
    candidates += sorted(BASE.glob(f"data/*_{date_str}T*.png"))
    candidates += sorted(BASE.glob(f"data/*_{date_str}T*.jpg"))
    # 今日命名封面
    candidates += [
        BASE / f"data/cover_{date_str}_fixed.png",
        BASE / f"data/cover_{date_str}_fixed.jpg",
        BASE / f"data/cover_{date_str}.png",
        BASE / f"data/cover_{date_str}.jpg",
    ]
    
    cover_path = None
    for cand in candidates:
        if cand.exists():
            cover_path = cand
            break
    
    if cover_path:
        print(f"  ✅ 使用预生成封面: {cover_path.name}")
    else:
        print("  ⚠️ 未找到预生成封面,降级为PIL占位图")
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            print("  ❌ 无PIL库,跳过封面上传")
            return ""
        
        img = Image.new("RGB", (900, 500), (6, 78, 59))
        draw = ImageDraw.Draw(img)
        try:
            ft = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 44)
            fs = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 24)
        except:
            ft = fs = None
        draw.text((450, 190), "自动驾驶晨报", fill=(167, 243, 208), font=ft, anchor="mm")
        draw.text((450, 255), date_str[:4] + "." + date_str[4:6] + "." + date_str[6:8], fill=(209, 250, 229), font=fs, anchor="mm")
        cover_path = BASE / f"data/cover_{date_str}.png"
        img.save(str(cover_path))
    
    url = "https://api.weixin.qq.com/cgi-bin/material/add_material"
    with open(cover_path, "rb") as f:
        r = requests.post(url, params={"access_token": token, "type": "image"},
            files={"media": f}, timeout=30)
    d = r.json()
    if "media_id" not in d:
        print(f"  ⚠️ 封面上传失败: {d.get('errmsg', d)}")
        return ""
    print(f"  ✅ 封面上传成功")
    return d["media_id"]


def generate_links_page() -> Path:
    """从HTML仪表盘提取所有卡片URL,生成格式精美的本地链接汇总页。"""
    html_text = HTML_FILE.read_text("utf-8")

    # 从HTML标题中提取日期
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', html_text)
    date_display = f"{m.group(1)}年{m.group(2)}月{m.group(3)}日" if m else datetime.now().strftime("%Y年%m月%d日")

    sections, total = extract_cards(html_text)
    if not sections:
        print("  ⚠️ 未解析到任何卡片，使用旧版兼容生成")
        # fallback to old minimal version
        return _generate_links_page_legacy()

    links_html = build_links_html(sections, total, date_display)
    links_path = BASE / f"data/links_{_DATE_STR}.html"
    links_path.write_text(links_html, encoding="utf-8")
    print(f"  ✅ 链接汇总页已生成: {links_path}")
    return links_path


def _generate_links_page_legacy() -> Path:
    """旧版最小化链接页（兼容备用）"""
    html_text = HTML_FILE.read_text("utf-8")
    card_pat = re.compile(
        r'<a class="card".*?href="([^"]+)".*?'
        r'<span class="card-num"[^>]*>(\d+)</span>.*?'
        r'<span class="card-source">([^<]+)</span>.*?'
        r'<span class="card-date">([^<]+)</span>.*?'
        r'<div class="card-title">([^<]+)</div>',
        re.S
    )
    sec_blocks = list(re.finditer(r'<div class="sec sec-\d+" id="(sec-\d+)"', html_text))
    sec_names = {"sec-1": "📊 行业动态", "sec-2": "🚀 产品发布", "sec-4": "📄 前沿论文", "sec-5": "💡 其他消息"}
    grouped = {name: [] for name in sec_names.values()}
    for i, sm in enumerate(sec_blocks):
        sec_id = sm.group(1)
        sec_name = sec_names.get(sec_id)
        if not sec_name:
            continue
        end = sec_blocks[i + 1].start() if i + 1 < len(sec_blocks) else len(html_text)
        for url, num, source, date, title in card_pat.findall(html_text[sm.start():end]):
            grouped[sec_name].append((num, title.strip(), url))
    links_body = ""
    for sec_name, items in grouped.items():
        if not items:
            continue
        links_body += f'<h2>{html_mod.escape(sec_name)}</h2>\n'
        for num, title, url in items:
            links_body += f'<p><a href="{html_mod.escape(url)}">#{html_mod.escape(num)} {html_mod.escape(title)}</a></p>\n'
    date_display = datetime.now().strftime("%Y年%m月%d日")
    all_items = sum(grouped.values(), [])
    links_html = (
        f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
        f'<title>自动驾驶晨报 原文链接汇总</title></head><body>'
        f'<h1>原文链接汇总</h1><p>{date_display} · 共 {len(all_items)} 篇</p>'
        f'{links_body}</body></html>'
    )
    links_path = BASE / f"data/links_{_DATE_STR}.html"
    links_path.write_text(links_html, encoding="utf-8")
    return links_path


def create_draft(token, article):
    url = "https://api.weixin.qq.com/cgi-bin/draft/add"
    body = json.dumps({"articles": [{
        "title": article["title"],
        "digest": article["digest"],
        "content": article["content"],
        "content_source_url": article.get("content_source_url", ""),
        "thumb_media_id": article.get("thumb_media_id", ""),
        "need_open_comment": 1,
        "only_fans_can_comment": 0,
    }]}, ensure_ascii=False)
    
    r = requests.post(url, params={"access_token": token},
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=30)
    d = r.json()
    assert "media_id" in d, f"创建草稿失败: {d}"
    print(f"✅ 草稿创建成功! media_id={d['media_id']}")
    return d


def main():
    if not HTML_FILE.exists():
        print(f"❌ HTML文件不存在: {HTML_FILE}")
        sys.exit(1)
    
    print("📖 解析数据...")
    article = parse()
    print(f"   标题: {article['title']}")
    print(f"   摘要: {article['digest']}")
    print(f"   正文长度: {len(article['content'])} 字符")
    
    print("\n🔗 生成链接汇总页...")
    links_path = generate_links_page()
    
    # content_source_url: 优先环境变量,否则空(需部署后手动更新)
    links_url = os.environ.get("WX_LINKS_URL", "")
    article["content_source_url"] = links_url
    if links_url:
        print(f"   阅读原文链接: {links_url}")
    else:
        print(f"   ⚠️ 未设置 WX_LINKS_URL,阅读原文为空。请部署 {links_path} 后更新")
    
    print("\n🔑 获取token...")
    token = get_token()
    
    print("\n🖼️  上传封面...")
    thumb_id = upload_cover(token)
    article["thumb_media_id"] = thumb_id
    
    print("\n📝 创建草稿...")
    create_draft(token, article)
    
    print("\n🎉 完成! 请到公众号后台「草稿箱」查看并发布。")


if __name__ == "__main__":
    main()
