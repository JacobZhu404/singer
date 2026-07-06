#!/usr/bin/env python3
"""一键封面生成管道：ImageGen 调用 → 等比裁剪 → PIL 叠加文字 → 输出 900×500。

用法:
    python3 scripts/generate_cover.py --date 20260706
    python3 scripts/generate_cover.py --date 20260706 --template assets/cover_template.png
    python3 scripts/generate_cover.py --date 20260706 --prompt "自动驾驶概念图"
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from datetime import datetime

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("❌ 需要 PIL 库，请运行:")
    print("   pip install Pillow")
    sys.exit(1)


# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ASSETS_DIR = BASE_DIR / "assets"

# 中文字体候选路径（优先 macOS STHeiti，逐级 fallback）
FONT_CANDIDATES = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
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

    # 叠加文字
    draw = ImageDraw.Draw(img)
    try:
        title_font = find_font(70)
        date_font = find_font(30)
    except RuntimeError as e:
        print(f"  ❌ {e}")
        raise

    # 标题居中，位于上半部分
    bbox = draw.textbbox((0, 0), title, font=title_font)
    title_w = bbox[2] - bbox[0]
    draw.text(
        ((TARGET_W - title_w) // 2, 100),
        title,
        fill=(255, 255, 255),
        font=title_font,
    )

    # 日期格式: 2026.07.06
    date_display = f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:8]}"
    draw.text(
        (TARGET_W // 2, 420),
        date_display,
        fill=(167, 243, 208),
        font=date_font,
        anchor="mm",
    )

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
