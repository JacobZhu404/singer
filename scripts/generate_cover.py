#!/usr/bin/env python3
"""一键封面生成管道：ImageGen 调用 → 等比裁剪 → PIL 叠加文字 → 输出 900×500。

用法:
    python3 scripts/generate_cover.py --date 20260706
    python3 scripts/generate_cover.py --date 20260706 --template assets/cover_template.png
    python3 scripts/generate_cover.py --date 20260706 --prompt "自动驾驶概念图"
"""
import argparse
import sys
from pathlib import Path
from datetime import datetime

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
except ImportError:
    print("❌ 需要 PIL 库，请运行:")
    print("   pip install Pillow")
    sys.exit(1)


# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ASSETS_DIR = BASE_DIR / "assets"

FONT_CANDIDATES = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]

TARGET_W, TARGET_H = 900, 500


def find_font(size: int = 70) -> ImageFont.FreeTypeFont:
    """找到可用的中文字体，逐级 fallback。"""
    for fp in FONT_CANDIDATES:
        p = Path(fp)
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                continue
    raise RuntimeError("没有可用的中文字体！请安装中文字体后重试。")


def generate_imagegen_bg(prompt: str, date_str: str):
    """提示用户在 WorkBuddy 中调用 ImageGen 工具生成背景图。"""
    print(f"\n  🎨 需要生成无文字背景图，请在对话中调用 ImageGen：")
    print(f"    prompt: {prompt}")
    print(f"    size: 1024x1024")
    print(f"    output_dir: {DATA_DIR}")
    print(f"    quality: high")
    print(f"  ⚠️ 注意：提示词中必须包含 NO text / no typography / no watermark")
    print(f"          生成后再次运行: python3 scripts/generate_cover.py --date {date_str}\n")


def find_latest_background() -> Path:
    """找到最新生成的无文字背景图。"""
    candidates = []
    for p in DATA_DIR.glob("*.png"):
        # 排除已处理封面
        if p.name.startswith("cover_"):
            continue
        candidates.append(p)
    if not candidates:
        raise FileNotFoundError(
            "未找到无文字背景图！请先用 ImageGen 生成背景图，\n"
            "或使用 --template 指定已有模板图。"
        )
    return max(candidates, key=lambda x: x.stat().st_mtime)


def draw_glow_text(
    canvas,
    xy,
    text,
    font,
    core_fill,
    glow_fill,
    anchor=None,
    glow_radius: int = 10,
    glow_passes: int = 4,
    tracking: int = 0,
):
    """在 RGBA canvas 上绘制霓虹发光文字：模糊的彩色光晕 + 锐利的亮色字芯。

    tracking>0 时按字距逐字绘制（英文副标题用），使其更有科技感。
    """
    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    if tracking > 0:
        _draw_tracked(gd, xy, text, font, glow_fill + (255,), anchor, tracking)
    else:
        gd.text(xy, text, font=font, fill=glow_fill + (255,), anchor=anchor)
    glow = glow.filter(ImageFilter.GaussianBlur(glow_radius))
    for _ in range(glow_passes):
        canvas.alpha_composite(glow)

    d = ImageDraw.Draw(canvas)
    if tracking > 0:
        _draw_tracked(d, xy, text, font, core_fill + (255,), anchor, tracking)
    else:
        d.text(xy, text, font=font, fill=core_fill + (255,), anchor=anchor)


def _draw_tracked(draw, xy, text, font, fill, anchor, tracking):
    """按固定字距逐字符绘制（PIL 无原生 letter-spacing）。仅支持水平居中/左对齐。"""
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    x, y = xy
    if anchor and anchor[0] == "m":
        x -= total / 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill, anchor="l" + (anchor[1] if anchor else "a"))
        x += w + tracking


def process_cover(
    bg_path: Path,
    date_str: str,
    title: str = "自动驾驶晨报",
) -> Path:
    """裁剪背景图 → 等比缩放 → 叠加文字 → 输出成品。"""
    print(f"  🖼️  处理背景图: {bg_path.name}")

    img = Image.open(bg_path)

    # 等比裁剪至 900:500 比例
    src_w, src_h = img.size
    ratio = TARGET_W / TARGET_H
    src_ratio = src_w / src_h
    if src_ratio > ratio:
        new_w = int(src_h * ratio)
        left = (src_w - new_w) // 2
        img = img.crop((left, 0, left + new_w, src_h))
    else:
        new_h = int(src_w / ratio)
        top = (src_h - new_h) // 2
        img = img.crop((0, top, src_w, top + new_h))

    # 缩放到 900x500
    img = img.resize((TARGET_W, TARGET_H), Image.LANCZOS)

    # 叠加文字（在 RGBA 图层上做霓虹发光）
    canvas = img.convert("RGBA")
    try:
        title_font = find_font(60)
        subtitle_font = find_font(18)
        date_font = find_font(30)
    except RuntimeError as e:
        print(f"  ❌ {e}")
        raise

    # 颜色：亮薄荷字芯 + 青绿光晕，营造科技霓虹感
    CORE = (214, 255, 240)   # 近白薄荷字芯
    GLOW = (16, 220, 180)    # 青绿光晕
    MINT = (167, 243, 208)   # 薄荷（副标题/日期字芯）

    # 标题：顶部居中，霓虹发光
    draw_glow_text(
        canvas, (TARGET_W // 2, 62), title, title_font,
        core_fill=CORE, glow_fill=GLOW, anchor="ma",
        glow_radius=12, glow_passes=5,
    )

    # 副标题：英文小字、字距拉开，增强科技感
    draw_glow_text(
        canvas, (TARGET_W // 2, 138), "AUTONOMOUS DRIVING · DAILY BRIEF", subtitle_font,
        core_fill=MINT, glow_fill=GLOW, anchor="ma",
        glow_radius=5, glow_passes=2, tracking=6,
    )

    # 日期：右对齐、上提到安全区（公众号列表会居中裁成 1:1，
    # 超出 x=700 会被切掉；右边缘对齐 x=700 可在列表和完整封面都可见）
    date_display = f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:8]}"
    draw_glow_text(
        canvas, (TARGET_W - 200, TARGET_H - 80), date_display, date_font,
        core_fill=MINT, glow_fill=GLOW, anchor="rm",
        glow_radius=8, glow_passes=3,
    )

    img = canvas.convert("RGB")

    # 保存
    out_path = DATA_DIR / f"cover_{date_str}_fixed.png"
    img.save(str(out_path))
    print(f"  ✅ 封面已生成: {out_path} (900×500)")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="一键生成公众号封面（900×500）")
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y%m%d"),
        help="日期，如 20260706（默认今天）",
    )
    parser.add_argument(
        "--template",
        default=None,
        help="使用已有模板图（PNG），跳过 ImageGen 步骤",
    )
    parser.add_argument(
        "--prompt",
        default=(
            "A futuristic autonomous driving newsletter cover background. "
            "Dark teal gradient with subtle circuit patterns. Glowing cyan wireframe "
            "self-driving car with light trails and sensor beams. Minimal, no text, "
            "no typography. High-end tech aesthetic. Leave space for title overlay."
        ),
        help="ImageGen 提示词（默认：自动驾驶未来感背景）",
    )
    parser.add_argument(
        "--title",
        default="自动驾驶晨报",
        help="封面标题文字（默认：自动驾驶晨报）",
    )
    args = parser.parse_args()

    date_str = args.date
    print(f"📅 生成 {date_str} 封面图\n")

    # 1) 确定背景图
    if args.template:
        bg_path = Path(args.template)
        if not bg_path.exists():
            print(f"❌ 模板文件不存在: {bg_path}")
            return 1
        print(f"  📁 使用模板: {bg_path.name}")
    else:
        try:
            bg_path = find_latest_background()
            print(f"  📁 使用已有背景图: {bg_path.name}")
        except FileNotFoundError:
            generate_imagegen_bg(args.prompt, date_str)
            print()
            print("  ⚠️  未找到背景图，请按以下步骤操作：")
            print("  1. 在对话中调用 ImageGen 工具生成无文字背景图")
            print("  2. 再次运行此脚本即可完成封面")
            return 1

    # 2) 处理并输出成品
    try:
        out_path = process_cover(bg_path, date_str, title=args.title)
    except Exception as e:
        print(f"  ❌ 封面生成失败: {e}")
        return 1

    print(f"\n🎉 封面生成完成！")
    print(f"   文件: {out_path}")
    print(f"   尺寸: {TARGET_W}×{TARGET_H}")
    print(f"   后续: push_today.py 会自动使用此封面")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
