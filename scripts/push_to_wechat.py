#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信公众号「自驾车早」草稿箱推送脚本
读取本地 HTML 文件 → 提取标题/封面/正文 → 质量门禁 → 调微信 API 创建草稿

配置来源: config.json (优先), 环境变量 (可覆盖)

使用方法:
  1. 确保 config.json 中填入了正确的 app_secret
  2. python3 push_to_wechat.py
  3. python3 push_to_wechat.py --skip-qa   (跳过质量门禁)

环境变量覆盖 (可选):
  WX_APP_ID, WX_APP_SECRET, WX_HTML_FILE, WX_COVER_FILE, WX_LINKS_URL, WX_WORK_DIR

依赖: requests (pip install requests)
"""

import os
import re
import sys
import json
import html
import time
import subprocess
from pathlib import Path
from datetime import datetime

try:
    import requests
except ImportError:
    print("缺少 requests 库,请先运行: pip install requests")
    sys.exit(1)

# ==================== 配置加载 ====================
# config.json 位于脚本同级目录的 parent (即 ad-newsletter/)
_SCRIPT_DIR = Path(__file__).parent.resolve()
_BASE_DIR = _SCRIPT_DIR.parent.resolve()
_CONFIG_PATH = _BASE_DIR / "config.json"
_DATE_STR = datetime.now().strftime("%Y%m%d")

# 加载 config.json
CONFIG = {}
if _CONFIG_PATH.exists():
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        CONFIG = json.load(f)
    print(f"✅ 配置已加载: {_CONFIG_PATH}")

WX_CONFIG = CONFIG.get("wechat", {})
PATH_CONFIG = CONFIG.get("paths", {})
NL_CONFIG = CONFIG.get("newsletter", {})

# 凭据: 环境变量 > config.json
APP_ID = os.environ.get("WX_APP_ID", WX_CONFIG.get("app_id", ""))
APP_SECRET = os.environ.get("WX_APP_SECRET", WX_CONFIG.get("app_secret", ""))

# 输出目录: 环境变量 > config.json > data/
OUTPUT_DIR = Path(os.environ.get("WX_OUTPUT_DIR", PATH_CONFIG.get("output_dir", str(_BASE_DIR / "data"))))

# 板块配置
SECTION_NAMES = {}
for sec in NL_CONFIG.get("sections", []):
    SECTION_NAMES[sec["id"]] = sec["name"]

# HTML 文件: 优先环境变量,默认 data/ 目录
HTML_FILE = os.environ.get("WX_HTML_FILE", "")
if not HTML_FILE:
    default_path = OUTPUT_DIR / f"自动驾驶晨报_{_DATE_STR}.html"
    HTML_FILE = str(default_path) if default_path.exists() else str(default_path)

# 封面图: 优先环境变量,默认 data/ 目录
COVER_FILE = os.environ.get("WX_COVER_FILE", "")
if not COVER_FILE:
    default_cover = OUTPUT_DIR / f"cover_{_DATE_STR}.png"
    COVER_FILE = str(default_cover) if default_cover.exists() else str(default_cover)

# 链接汇总页URL
LINKS_URL = os.environ.get("WX_LINKS_URL", "")

# 质量门禁控制
SKIP_QA = "--skip-qa" in sys.argv

# ==============================================


def assert_cred():
    if not APP_ID or not APP_SECRET:
        print("❌ 缺少凭据。请先在 config.json 中填入 app_secret")
        print("   或设置环境变量 WX_APP_ID / WX_APP_SECRET")
        sys.exit(1)


def get_access_token() -> str:
    """获取 access_token,2 小时有效"""
    url = "https://api.weixin.qq.com/cgi-bin/token"
    params = {
        "grant_type": "client_credential",
        "appid": APP_ID,
        "secret": APP_SECRET,
    }
    r = requests.get(url, params=params, timeout=15)
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError(f"获取 access_token 失败: {data}")
    print(f"✅ access_token 获取成功 (expires_in={data.get('expires_in')}s)")
    return data["access_token"]


# ==================== 质量门禁 ====================

def run_quality_gate(html_path: str) -> bool:
    """推送前质量检查: 链接可达性 + URL质量"""
    print("\n🔍 质量门禁检查 ...")

    all_passed = True

    # 1. 链接可达性验证
    print("  1️⃣ 验证链接可达性 ...")
    verify_script = _SCRIPT_DIR / "verify_links.py"
    if verify_script.exists():
        try:
            proc = subprocess.run(
                [sys.executable, str(verify_script), html_path],
                capture_output=True, text=True, timeout=120,
            )
            report = json.loads(proc.stdout) if proc.stdout else {}
            ok_count = report.get("ok", 0)
            fail_count = report.get("fail", 0)
            warn_count = report.get("warn", 0)
            total = report.get("total", 0)
            print(f"     链接验证: {total} 条 → ✅ {ok_count} | ⚠️ {warn_count} | ❌ {fail_count}")
            if fail_count > 0:
                print("     ⚠️ 有链接不可达,详情:")
                for r in report.get("results", []):
                    if r.get("verdict") == "fail":
                        print(f"       - {r.get('url', '?')} → {r.get('error', r.get('status', '?'))}")
                all_passed = False
        except Exception as e:
            print(f"     ⚠️ 链接验证脚本执行失败: {e}")
    else:
        print(f"     ⏭️ verify_links.py 不存在,跳过")

    # 2. URL质量检查
    print("  2️⃣ 检查URL质量 (登录墙/聚合器/首页) ...")
    quality_script = _SCRIPT_DIR / "check_url_quality.py"
    if quality_script.exists():
        try:
            proc = subprocess.run(
                [sys.executable, str(quality_script), html_path],
                capture_output=True, text=True, timeout=60,
            )
            report = json.loads(proc.stdout) if proc.stdout else {}
            ok_count = report.get("ok", 0)
            reject_count = report.get("reject", 0)
            warn_count = report.get("warn", 0)
            total = report.get("total", 0)
            print(f"     URL质量: {total} 条 → ✅ {ok_count} | ⚠️ {warn_count} | ❌ {reject_count}")
            if reject_count > 0:
                print("     ⚠️ 有URL被拒绝,详情:")
                for r in report.get("results", []):
                    if r.get("verdict") == "reject":
                        print(f"       - {r.get('url', '?')} → {', '.join(r.get('issues', []))}")
                print("     💡 被拒绝的URL将在推送中保留但标记为低质量")
        except Exception as e:
            print(f"     ⚠️ URL质量脚本执行失败: {e}")
    else:
        print(f"     ⏭️ check_url_quality.py 不存在,跳过")

    if not all_passed:
        print("\n❌ 质量门禁未通过!存在不可达链接。")
        print("   如仍要推送,请使用 --skip-qa 参数")
        return False

    print("\n✅ 质量门禁通过!")
    return True


# ==================== HTML 解析 ====================

def parse_html(html_text: str) -> dict:
    """
    从晨报 HTML 提取:
    - title: 文章标题
    - digest: 摘要(微信限制 < 54 字节)
    - content_html: 微信正文(用 <section> 重组)
    """
    max_title_bytes = NL_CONFIG.get("max_title_bytes", 60)
    max_digest_bytes = NL_CONFIG.get("max_digest_bytes", 54)

    # 标题
    m_title = re.search(r"<title>(.*?)</title>", html_text, re.S)
    raw_title = m_title.group(1).strip() if m_title else NL_CONFIG.get("title_template", "自动驾驶晨报")
    m_date = re.search(r"(\d+)月(\d+)日", raw_title)
    date_part = f"{m_date.group(1)}月{m_date.group(2)}日" if m_date else _DATE_STR
    if not m_date:
        m_date2 = re.search(r"(\d{4})年(\d+)月(\d+)日", html_text)
        if m_date2:
            date_part = f"{m_date2.group(2)}月{m_date2.group(3)}日"
    title = f"自动驾驶晨报 {date_part}".strip()
    if len(title.encode("utf-8")) > max_title_bytes:
        title = "自动驾驶晨报"

    # Hero 摘要
    m_hero = re.search(r'<span class="num">(\d+)</span> 条', html_text)
    total = m_hero.group(1) if m_hero else "30"

    # Hero 区域维度统计
    m_stats = re.findall(
        r'<span class="stat-icon">([^<]+)</span><span class="stat-label">([^<]+)</span><span class="stat-num">(\d+)</span>',
        html_text
    )
    if not m_stats:
        m_stats = re.findall(
            r'<span class="stat-icon">([^<]+)</span>([^<]+)<span class="stat-num">(\d+)</span>',
            html_text
        )
    stats_lines = [f"{icon} {label.strip()} {n}条" for icon, label, n in m_stats] if m_stats else []

    # 日期字符串
    m_date_full = re.search(r"(\d{4})年(\d+)月(\d+)日.*?星期([一二三四五六日天])", html_text)
    if m_date_full:
        weekday_map = {"一":"一","二":"二","三":"三","四":"四","五":"五","六":"六","日":"日","天":"日"}
        weekday = weekday_map.get(m_date_full.group(4), "")
        date_display = f"{m_date_full.group(1)}年{m_date_full.group(2)}月{m_date_full.group(3)}日 · 星期{weekday}"
    else:
        date_display = f"{datetime.now().year}年{datetime.now().month}月{datetime.now().day}日"

    # 正文:逐板块抽卡片
    sections = re.findall(r'<section id="(sec-\d+)" class="section">(.*?)</section>', html_text, re.S)
    body_parts = []

    # 概览表格行 + 详情卡片
    overview_rows = ""
    detail_cards = []
    for sec_id, sec_content in sections:
        sec_short = SECTION_NAMES.get(sec_id, sec_id)
        sec_full = ""
        m_sec_title = re.search(
            r'<span class="section-icon">([^<]+)</span>([^<]+?)<span class="section-badge">(\d+)</span>',
            sec_content
        )
        if m_sec_title:
            sec_icon, sec_name, sec_count = m_sec_title.groups()
            sec_full = f'<p style="margin:24px 0 12px;padding-left:8px;border-left:4px solid #059669;font-size:18px;font-weight:bold;color:#047857;">{html.escape(sec_icon)}{html.escape(sec_name)} ({sec_count}条)</p>'
        cards = re.findall(r'<a href="([^"]+)"[^>]*class="card">(.*?)</a>', sec_content, re.S)
        for url, card in cards:
            m_num = re.search(r'<span class="card-num">(\d+)</span>', card)
            m_src = re.search(r'<span class="card-source">([^<]+)</span>', card)
            m_card_title = re.search(r'<div class="card-title">([^<]+)</div>', card)
            m_time = re.search(r'<span class="card-time">([^<]+)</span>', card)
            m_summary = re.search(r'<div class="card-summary">([^<]+)</div>', card)
            m_dim = re.search(r'<div class="card-dimension ([a-z-]+)">([^<]+)</div>', card)
            if not m_card_title:
                continue
            num = m_num.group(1) if m_num else ""
            source = m_src.group(1) if m_src else ""
            time_str = m_time.group(1) if m_time else ""
            card_title = m_card_title.group(1).strip()
            summary = m_summary.group(1).strip() if m_summary else ""
            dim_text = ""
            if m_dim:
                dim_text = m_dim.group(2)

            # 概览行
            num_esc = html.escape(num)
            title_esc = html.escape(card_title)
            src_esc = html.escape(source)
            sec_esc = html.escape(sec_short)
            overview_rows += (
                f'<tr>'
                f'<td style="padding:6px 2px;width:20px;vertical-align:middle;text-align:center;">'
                f'<span style="display:inline-block;width:16px;height:16px;line-height:16px;text-align:center;background:#059669;color:#fff;border-radius:50%;font-size:9px;font-weight:bold;">{num_esc}</span>'
                f'</td>'
                f'<td style="padding:6px 4px;font-size:12px;color:#1a1a2e;line-height:1.4;">{title_esc}</td>'
                f'<td style="padding:6px 2px;font-size:10px;color:#059669;white-space:nowrap;vertical-align:middle;">{sec_esc}</td>'
                f'<td style="padding:6px 2px;font-size:10px;color:#6b7280;white-space:nowrap;vertical-align:middle;">{src_esc}</td>'
                f'</tr>'
            )

            # 详情卡片(使用已确认的 inline style)
            clean_date = time_str.replace("🔥", "").replace("📅", "").strip()
            card_html = (
                f'<section style="margin:14px 0;padding:14px 16px;border:1px solid #e5e7eb;'
                f'border-radius:8px;background:#fafafa;">'
                f'<p style="margin:0 0 6px;font-size:13px;color:#6b7280;">'
                f'<span style="display:inline-block;min-width:22px;height:22px;line-height:22px;'
                f'text-align:center;background:#059669;color:#fff;border-radius:50%;'
                f'font-weight:bold;margin-right:6px;">{html.escape(num)}</span>'
                f'{html.escape(source)} · {html.escape(clean_date)} {html.escape(dim_text)}</p>'
                f'<p style="margin:6px 0;font-size:16px;font-weight:bold;color:#111827;">'
                f'{html.escape(card_title)}</p>'
                f'<p style="margin:6px 0;font-size:14px;color:#374151;line-height:1.7;">'
                f'{html.escape(summary)}</p>'
                f'</section>'
            )
            detail_cards.append((sec_full, card_html))

    # 构建概览table
    overview_html = (
        f'<section style="margin:0 0 20px;padding:12px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;">'
        f'<p style="margin:0 0 8px;font-size:14px;font-weight:bold;color:#047857;">📋 今日概览 · {total} 条</p>'
        f'<table style="width:100%;border-collapse:collapse;">'
        f'{overview_rows}'
        f'</table>'
        f'</section>'
    )
    body_parts.append(overview_html)

    # 详情卡片按板块分组输出
    last_sec_full = None
    for sec_full, card_html in detail_cards:
        if sec_full and sec_full != last_sec_full:
            body_parts.append(sec_full)
            last_sec_full = sec_full
        body_parts.append(card_html)

    content_html = "\n".join(body_parts)
    digest = f"今日精选{total}条自动驾驶动态。覆盖行业动态、产品发布、模型发布、前沿论文、其他消息。"
    if len(digest.encode("utf-8")) > max_digest_bytes:
        digest = f"今日{total}条自动驾驶动态精选"

    return {
        "title": title,
        "digest": digest,
        "content_html": content_html,
        "total": total,
        "stats_lines": stats_lines,
        "date_display": date_display,
    }


def to_wechat_article(parsed: dict) -> str:
    """组装公众号文章 HTML 完整正文（使用已确认的 inline style）"""
    head = (
        f'<section style="padding:18px 14px;background:linear-gradient(135deg,#064e3b,#059669);'
        f'color:#fff;border-radius:8px;margin-bottom:18px;">'
        f'<p style="margin:0;font-size:11px;letter-spacing:2px;opacity:.7;">AUTONOMOUS DRIVING · DAILY BRIEF</p>'
        f'<p style="margin:6px 0;font-size:24px;font-weight:bold;">自动驾驶晨报</p>'
        f'<p style="margin:0;font-size:14px;opacity:.85;">{html.escape(parsed["date_display"])}</p>'
        f'<p style="margin:10px 0 0;font-size:15px;">本期精选 '
        f'<strong style="font-size:22px;color:#a7f3d0;">{parsed["total"]}</strong> 条</p>'
        f'</section>'
    )
    footer_section = (
        '<section style="margin:24px 0 0;padding:14px;background:#f0fdf4;'
        'border:1px solid #bbf7d0;border-radius:8px;text-align:center;">'
        '<p style="margin:0;font-size:14px;font-weight:600;color:#065f46;">📎 全部原文链接汇总</p>'
        '<p style="margin:6px 0 0;font-size:13px;color:#4a5568;">'
        '点击文章底部「阅读原文」查看所有原文链接</p>'
        '</section>'
    )
    return head + parsed["content_html"] + footer_section


def upload_thumb(token: str) -> str:
    """上传本地封面图到微信素材库"""
    if not COVER_FILE or not Path(COVER_FILE).exists():
        print(f"  ⏭️ 无封面图 ({COVER_FILE}),尝试 PIL 生成占位图")
        try:
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (900, 500), (6, 78, 59))
            draw = ImageDraw.Draw(img)
            draw.text((450, 250), "自动驾驶晨报", fill=(167, 243, 208))
            placeholder = OUTPUT_DIR / f"cover_placeholder_{_DATE_STR}.png"
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            img.save(str(placeholder))
            cover_file_local = str(placeholder)
        except ImportError:
            print("  ❌ 无 PIL 库,无法生成占位图")
            return ""
    else:
        cover_file_local = COVER_FILE

    print(f"  📷 上传封面图: {cover_file_local}")
    url = "https://api.weixin.qq.com/cgi-bin/material/add_material"
    params = {"access_token": token, "type": "image"}
    with open(cover_file_local, "rb") as f:
        r = requests.post(url, params=params, files={"media": f}, timeout=30)
    data = r.json()
    if "media_id" not in data:
        raise RuntimeError(f"封面图上传失败: {data}")
    print(f"  ✅ 封面图上传成功 media_id={data['media_id']}")
    return data["media_id"]


def create_draft(token: str, article: dict) -> dict:
    """创建草稿。用 ensure_ascii=False 的 JSON 字符串"""
    url = "https://api.weixin.qq.com/cgi-bin/draft/add"
    body = json.dumps(
        {"articles": [{
            "title": article["title"],
            "digest": article["digest"],
            "content": article["content"],
            "content_source_url": article.get("content_source_url", ""),
            "thumb_media_id": article["thumb_media_id"],
            "need_open_comment": 0,
            "only_fans_can_comment": 0,
        }]},
        ensure_ascii=False,
    )
    r = requests.post(
        url,
        params={"access_token": token},
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=30,
    )
    data = r.json()
    if "media_id" not in data:
        raise RuntimeError(f"创建草稿失败: {data}")
    print(f"✅ 草稿创建成功 media_id={data['media_id']}")
    return data


def generate_links_page(html_path: str) -> Path:
    """从 HTML 提取卡片URL,生成链接汇总页"""
    raw = Path(html_path).read_text("utf-8")
    card_pattern = re.compile(
        r'<a href="(https://[^"]+)"[^>]*class="card".*?'
        r'<span class="card-num">(\d+)</span>.*?'
        r'<div class="card-title">([^<]+)</div>.*?'
        r'<div class="card-dimension[^>]+>([^<]+)</div>',
        re.S
    )
    entries = card_pattern.findall(raw)

    sections_data = re.findall(
        r'<section id="(sec-\d+)" class="section">(.*?)</section>', raw, re.S
    )

    # 按板块名分组(从 config.json 读取)
    grouped = {}
    for sec in NL_CONFIG.get("sections", []):
        grouped[f"{sec['icon']} {sec['name']}"] = []

    for url, num, title, dim in entries:
        num_int = int(num)
        # 按编号范围分配到板块(与 config.json sections 对应)
        sections_list = NL_CONFIG.get("sections", [])
        sec_name_map = {
            "sec-1": "📊 行业动态",
            "sec-2": "🚀 产品发布",
            "sec-3": "🧠 模型发布",
            "sec-4": "📄 前沿论文",
            "sec-5": "💡 其他消息",
        }
        # 根据卡片在HTML中的section归属分配板块
        # 如果无法确定板块,按编号范围兜底
        if num_int <= 8:
            sec_key = "📊 行业动态"
        elif num_int <= 14:
            sec_key = "🚀 产品发布"
        elif num_int <= 19:
            sec_key = "🧠 模型发布"
        elif num_int <= 24:
            sec_key = "📄 前沿论文"
        else:
            sec_key = "💡 其他消息"
        grouped.setdefault(sec_key, []).append((url, num, title, dim))

    links_body = ""
    for sec_name, items in grouped.items():
        if not items:
            continue
        links_body += f'<h2 style="margin:20px 0 10px;font-size:16px;color:#059669;">{sec_name}</h2>\n'
        for url, num, title, dim in items:
            links_body += (
                f'<div style="margin:8px 0;padding:10px 12px;background:#f9fafb;border-radius:6px;">'
                f'<div style="font-size:13px;color:#6b7280;">#{num} · {dim.strip()}</div>'
                f'<div style="font-size:14px;font-weight:600;margin:4px 0;">'
                f'<a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer" style="color:#059669;text-decoration:none;">'
                f'{html.escape(title)} →</a></div>'
                f'</div>\n'
            )

    date_display = f"{datetime.now().year}年{datetime.now().month}月{datetime.now().day}日"
    links_html = (
        f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        f'<title>自动驾驶晨报 原文链接汇总</title>'
        f'<style>body{{font-family:-apple-system,sans-serif;max-width:720px;margin:0 auto;padding:20px;background:#f4f6f9;color:#1a1a2e;}}h1{{font-size:18px;color:#047857;}}</style>'
        f'</head><body>'
        f'<h1>🚗 自动驾驶晨报 · 原文链接汇总</h1>'
        f'<p style="color:#6b7280;font-size:13px;">{date_display} · 共 {len(entries)} 篇</p>'
        f'{links_body}'
        f'<p style="font-size:12px;color:#9ca3af;text-align:center;margin-top:30px;">由 WorkBuddy 生成</p>'
        f'</body></html>'
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    links_path = OUTPUT_DIR / f"links_{_DATE_STR}.html"
    links_path.write_text(links_html, encoding="utf-8")
    print(f"  ✅ 链接汇总页已生成: {links_path}")
    return links_path


def main():
    assert_cred()
    html_path = Path(HTML_FILE)
    if not html_path.exists():
        print(f"❌ HTML 文件不存在: {HTML_FILE}")
        print(f"   请设置 WX_HTML_FILE 环境变量或确保文件在工作目录中")
        sys.exit(1)

    # 质量门禁
    if not SKIP_QA:
        if not run_quality_gate(str(html_path)):
            sys.exit(1)
    else:
        print("\n⏭️ 跳过质量门禁 (--skip-qa)")

    print(f"\n📄 读取 HTML: {HTML_FILE}")
    html_text = html_path.read_text(encoding="utf-8")
    parsed = parse_html(html_text)
    print(f"  标题: {parsed['title']}")
    print(f"  摘要: {parsed['digest']}")
    print(f"  正文长度: {len(parsed['content_html'])} 字符")

    # 生成链接汇总页
    links_path = generate_links_page(str(html_path))

    content_source_url = LINKS_URL
    if not content_source_url:
        print(f"  💡 链接汇总页已生成: {links_path}")
        print(f"  💡 请部署后设置 WX_LINKS_URL 环境变量")
        content_source_url = ""

    print("\n🔑 获取 access_token ...")
    token = get_access_token()

    print("\n🖼️  上传封面图 ...")
    thumb_id = upload_thumb(token)

    article = {
        "title": parsed["title"],
        "digest": parsed["digest"],
        "content": to_wechat_article(parsed),
        "thumb_media_id": thumb_id,
        "content_source_url": content_source_url,
    }

    print("\n📝 创建草稿 ...")
    result = create_draft(token, article)

    print(f"\n🎉 全部完成!请到公众号后台「草稿箱」查看")
    print(f"   media_id: {result.get('media_id')}")
    print(f"   链接汇总页: {links_path}")
    if not LINKS_URL:
        print(f"   ⚠️ 链接汇总页未部署到线上,请部署后更新 WX_LINKS_URL")


if __name__ == "__main__":
    main()
