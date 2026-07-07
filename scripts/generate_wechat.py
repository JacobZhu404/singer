#!/usr/bin/env python3
"""
outline.json → 微信公众号正文 HTML。
Jinja2 模板渲染，与仪表盘生成走同一套 schema，格式逐字节可复现。

用法:
    # 作为模块
    from generate_wechat import render_wechat
    from utils.outline_schema import load_outline
    outline = load_outline("data/outline_20260706.json")
    result = render_wechat(outline)
    # result = {"title": ..., "digest": ..., "content": ...}

    # 独立运行（预览/调试）
    python3 scripts/generate_wechat.py --outline data/outline_20260706.json
"""
import argparse
import html as html_mod
import json
import sys
from pathlib import Path

try:
    import jinja2
except ImportError:
    print("需要 jinja2: pip install jinja2")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))
from utils.outline_schema import Outline, load_outline

BASE = Path(__file__).resolve().parent.parent
TEMPLATE = BASE / "assets" / "wechat_article.html.j2"
CONFIG = json.loads((BASE / "config.json").read_text())
SECTIONS_CFG = CONFIG["newsletter"]["sections"]
SEC_NAME_MAP = {s["id"]: f"{s['icon']} {s['name']}" for s in SECTIONS_CFG}
SEC_SHORT_MAP = {s["id"]: s["name"] for s in SECTIONS_CFG}


def _build_render_data(outline):
    flat_items = []
    sections_nonempty = []

    for sec in outline.sections:
        cards = []
        for item in sec.items:
            clean_date = item.date.replace("\U0001f525", "").strip()
            card = {
                "num": item.num,
                "source_esc": html_mod.escape(item.source),
                "date": html_mod.escape(clean_date),
                "title_esc": html_mod.escape(item.title),
                "summary_esc": html_mod.escape(item.summary),
            }
            cards.append(card)
            flat_items.append({
                "num": item.num,
                "title_esc": html_mod.escape(item.title),
                "source_esc": html_mod.escape(item.source),
                "sec_short": html_mod.escape(SEC_SHORT_MAP.get(sec.id, sec.name)),
            })

        if sec.count > 0:
            sections_nonempty.append({
                "name": SEC_NAME_MAP.get(sec.id, f"{sec.icon} {sec.name}"),
                "cards": cards,
            })

    dd = outline.date_display
    if "·" not in dd and "星期" in dd:
        dd = dd.replace(" 星期", " · 星期")

    return {
        "date_display": dd,
        "total": outline.total,
        "flat_items": flat_items,
        "sections_nonempty": sections_nonempty,
    }


def render_wechat(outline):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE.parent)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    tmpl = env.get_template(TEMPLATE.name)
    data = _build_render_data(outline)
    content = tmpl.render(**data)

    date_parts = outline.date_display.split("·")[0].strip() if "·" in outline.date_display else outline.date_display
    import re
    m = re.search(r"(\d+)月(\d+)日", date_parts)
    date_short = f"{m.group(1)}月{m.group(2)}日" if m else date_parts

    title = f"自动驾驶晨报 {date_short}"
    digest = f"今日{outline.total}条自动驾驶动态精选"
    if len(digest.encode()) > 54:
        digest = f"今日{outline.total}条自动驾驶动态"

    return {
        "title": title,
        "digest": digest,
        "content": content,
        "total": str(outline.total),
    }


def main():
    parser = argparse.ArgumentParser(description="从 outline.json 生成微信公众号正文")
    parser.add_argument("--outline", required=True, help="outline JSON 路径")
    parser.add_argument("--output", default=None, help="输出 HTML 路径")
    args = parser.parse_args()

    outline = load_outline(args.outline)
    result = render_wechat(outline)

    print(f"标题: {result['title']}")
    print(f"摘要: {result['digest']}")
    print(f"正文: {len(result['content'])} 字符")

    if args.output:
        Path(args.output).write_text(result["content"], "utf-8")
        print(f"已保存: {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
