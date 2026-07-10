#!/usr/bin/env python3
"""
从 outline.json → 格式稳定的 HTML 仪表盘。
Jinja2 模板渲染，逐字节可复现，彻底消除排版抖动。

用法:
    python3 scripts/generate_html.py --outline data/outline_YYYYMMDD.json
    python3 scripts/generate_html.py --outline data/outline_YYYYMMDD.json --output data/自定义.html
"""
import argparse
import html as html_mod
import sys
from pathlib import Path

try:
    import jinja2
except ImportError:
    print("❌ 需要 jinja2 库，请运行: pip install jinja2")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))
from utils.outline_schema import Outline


BASE = Path(__file__).resolve().parent.parent
TEMPLATE = BASE / "assets/dashboard_template.html.j2"
DATA = BASE / "data"

SEC_COLORS = {
    1: "059669",
    2: "8b5cf6",
    3: "f59e0b",
    4: "ec4899",
    5: "ef4444",
}

SEC_BADGE_COLORS = {
    1: {"bg": "dbeafe", "fg": "1d4ed8"},
    2: {"bg": "d1fae5", "fg": "047857"},
    3: {"bg": "e0e7ff", "fg": "4338ca"},
    4: {"bg": "fce7f3", "fg": "be185d"},
    5: {"bg": "fef3c7", "fg": "b45309"},
}


def build_render_data(outline: Outline) -> dict:
    """将 outline 结构化为模板所需的平面列表和分组数据。"""
    flat = []
    sections_nonempty = []
    sections_empty = []

    for sec in outline.sections:
        sec_num = int(sec.id.split("-")[1])
        sec_color = SEC_COLORS.get(sec_num, "059669")
        sec_name = sec.name

        items_prepped = []
        for item in sec.items:
            items_prepped.append({
                "num": item.num,
                "url": item.url,
                "source_esc": html_mod.escape(item.source),
                "date": item.date,
                "title_esc": html_mod.escape(item.title),
                "summary_esc": html_mod.escape(item.summary),
                "tags": [item.dimension],
            })
            flat.append({
                "num": item.num,
                "title_esc": html_mod.escape(item.title),
                "source_esc": html_mod.escape(item.source),
                "sec_name": sec_name,
                "sec_id": sec_num,
            })

        sec_data = {
            "id": sec.id,
            "icon": sec.icon,
            "name": sec_name,
            "count": sec.count,
            "cards": items_prepped,
            "num_color": sec_color,
            "sec_num": sec_num,
        }

        if sec.count > 0:
            sections_nonempty.append(sec_data)
        else:
            sections_empty.append(sec_data)

    return {
        "title": f"自动驾驶晨报 | {outline.date_display.split('·')[0].strip()}"
                 if "·" in outline.date_display else f"自动驾驶晨报 | {outline.date_display}",
        "date_display": outline.date_display,
        "total": outline.total,
        "flat_items": flat,
        "sections_nonempty": sections_nonempty,
        "sections_empty": sections_empty,
        "SEC_COLORS": SEC_COLORS,
        "SEC_BADGE_COLORS": SEC_BADGE_COLORS,
    }


def render(outline: Outline) -> str:
    """加载模板 + 渲染 → HTML 字符串。"""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE.parent)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    tmpl = env.get_template(TEMPLATE.name)
    data = build_render_data(outline)
    return tmpl.render(**data)


def main():
    parser = argparse.ArgumentParser(description="从 outline.json 生成格式稳定的 HTML 仪表盘")
    parser.add_argument("--outline", required=True, help="outline JSON 路径")
    parser.add_argument("--output", default=None, help="输出 HTML 路径（默认: data/自动驾驶晨报_<日期>.html）")
    args = parser.parse_args()

    # 1) 加载并验证 outline
    print(f"📄 加载 outline: {args.outline}")
    outline = Outline.from_file(args.outline)
    print(f"   验证通过: {outline.title}, {outline.total} 条")

    # 2) 渲染 HTML
    print(f"\n🎨 渲染模板: {TEMPLATE}")
    html_str = render(outline)
    print(f"   输出长度: {len(html_str)} 字符")

    # 3) 确定输出路径
    if args.output:
        out_path = Path(args.output)
    else:
        date_str = outline.generated_at.replace("-", "")
        out_path = DATA / f"自动驾驶晨报_{date_str}.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_str, encoding="utf-8")
    print(f"\n✅ HTML 已生成: {out_path}")

    # 4) 结构验证
    assert '<div class="header">' in html_str, "缺少 header 结构"
    assert '<div class="overview">' in html_str, "缺少 overview 结构"
    assert str(outline.total) in html_str, f"缺少总条数 {outline.total}"
    for item in outline.flat_items():
        escaped_title = html_mod.escape(item.title)
        assert escaped_title in html_str, f"缺少标题: {item.title[:20]}"
    print(f"   自检通过: 结构/条数/全部卡片均在输出中")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
