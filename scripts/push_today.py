#!/usr/bin/env python3
"""推送今日晨报到微信公众号草稿箱。

数据源: outline.json（不再从仪表盘 HTML 解析）
正文生成: Jinja2 模板（assets/wechat_article.html.j2）
"""
import json, re, os, sys
import urllib.request
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from utils.outline_schema import load_outline
from utils.parse_cards import SEC_META
from generate_wechat import render_wechat
from generate_links_page import build_links_html

try:
    import requests
except ImportError:
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests

BASE = Path(__file__).resolve().parent.parent
_DATE_STR = datetime.now().strftime("%Y%m%d")
OUTLINE_FILE = BASE / f"data/outline_{_DATE_STR}.json"
CONFIG = json.loads((BASE / "config.json").read_text())
WX = CONFIG["wechat"]

APP_ID = WX["app_id"]
APP_SECRET = WX["app_secret"]


def get_token():
    r = requests.get("https://api.weixin.qq.com/cgi-bin/token", params={
        "grant_type": "client_credential", "appid": APP_ID, "secret": APP_SECRET
    }, timeout=15)
    d = r.json()
    assert "access_token" in d, f"token失败: {d}"
    print(f"✅ token获取成功")
    return d["access_token"]


def upload_cover(token):
    """上传封面图到微信素材库。优先使用预生成的高质量封面,否则降级为PIL占位图。"""
    date_str = _DATE_STR
    candidates = []
    candidates += sorted(BASE.glob(f"data/*_{date_str}T*.png"))
    candidates += sorted(BASE.glob(f"data/*_{date_str}T*.jpg"))
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
            from generate_cover import find_font
            ft = find_font(44)
            fs = find_font(24)
        except Exception:
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


def generate_links_page(outline) -> Path:
    """从 outline 生成链接汇总页。"""
    import html as html_mod

    sections = []
    for sec in outline.sections:
        if sec.count == 0:
            continue
        items = []
        for item in sec.items:
            items.append({
                "num": str(item.num),
                "url": item.url,
                "title": item.title,
                "source": item.source,
                "date": item.date,
            })
        sections.append({
            "name": f"{sec.icon} {sec.name}",
            "color": SEC_META.get(sec.id, ("", "#059669"))[1],
            "items": items,
        })

    total = outline.total
    dd = outline.date_display.split("·")[0].strip() if "·" in outline.date_display else outline.date_display

    links_html = build_links_html(sections, total, dd)
    links_path = BASE / f"data/links_{_DATE_STR}.html"
    links_path.write_text(links_html, encoding="utf-8")
    print(f"  ✅ 链接汇总页已生成: {links_path}")
    return links_path


def _post_json(url: str, body: bytes, timeout: int = 30) -> dict:
    """用 urllib 发送 JSON POST。

    注意：WeChat 服务器对 requests 库的 POST 会按 Latin-1 解码 UTF-8 中文，
          导致标题/正文乱码；urllib（标准库）无此问题。
    """
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def create_draft(token, article):
    url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}"
    body = json.dumps({"articles": [{
        "title": article["title"],
        "digest": article["digest"],
        "content": article["content"],
        "content_source_url": article.get("content_source_url", ""),
        "thumb_media_id": article.get("thumb_media_id", ""),
        "need_open_comment": 1,
        "only_fans_can_comment": 0,
    }]}, ensure_ascii=False).encode("utf-8")

    d = _post_json(url, body, timeout=30)
    assert "media_id" in d, f"创建草稿失败: {d}"
    print(f"✅ 草稿创建成功! media_id={d['media_id']}")
    return d


def main():
    if not OUTLINE_FILE.exists():
        print(f"❌ outline 不存在: {OUTLINE_FILE}")
        sys.exit(1)

    print("📄 加载 outline...")
    outline = load_outline(OUTLINE_FILE)
    print(f"   {outline.title}, {outline.total} 条")

    print("\n📖 生成微信正文...")
    article = render_wechat(outline)
    print(f"   标题: {article['title']}")
    print(f"   摘要: {article['digest']}")
    print(f"   正文长度: {len(article['content'])} 字符")

    print("\n🔗 生成链接汇总页...")
    links_path = generate_links_page(outline)

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
