#!/usr/bin/env python3
"""导出 outline_*.json → 小程序数据（miniprogram/data/）。

每期一份干净 JSON + index.json 清单 + 随包打包用的 local.js。
小程序运行时优先读 local.js（开发期免 CDN），部署后切到网络请求。

用法:
    python3 scripts/export_mini.py                 # 导出全部 outline_*.json
    python3 scripts/export_mini.py --date 20260708  # 只导出某期
"""
import argparse
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC_DIR = BASE / "data"
OUT_DIR = BASE / "miniprogram" / "data"

DATE_RE = re.compile(r"outline_(\d{8})\.json$")


def transform(path: Path):
    """把原始 outline 转成小程序精简结构。"""
    raw = json.loads(path.read_text("utf-8"))
    m = DATE_RE.search(path.name)
    date = m.group(1) if m else raw.get("generated_at", "").replace("-", "")

    sections = []
    for s in raw.get("sections", []):
        sections.append({
            "id": s.get("id"),
            "name": s.get("name"),
            "icon": s.get("icon", ""),
            "items": [
                {
                    "num": it.get("num"),
                    "title": it.get("title", ""),
                    "source": it.get("source", ""),
                    "url": it.get("url", ""),
                    "date": it.get("date", ""),
                    "dimension": it.get("dimension", ""),
                    "summary": it.get("summary", ""),
                }
                for it in s.get("items", [])
            ],
        })

    return {
        "date": date,
        "date_display": raw.get("date_display", ""),
        "title": raw.get("title", ""),
        "digest": raw.get("digest", ""),
        "total": raw.get("total", 0),
        "sections": sections,
        "editor_note": (raw.get("metadata") or {}).get("quality_notes", ""),
    }, date


def main():
    ap = argparse.ArgumentParser(description="outline → 小程序数据")
    ap.add_argument("--date", default=None, help="只导出指定日期，如 20260708")
    args = ap.parse_args()

    files = sorted(SRC_DIR.glob("outline_*.json"))
    if args.date:
        files = [f for f in files if DATE_RE.search(f.name) and DATE_RE.search(f.name).group(1) == args.date]
    if not files:
        print("未找到匹配的 outline_*.json")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index_issues = []

    for f in files:
        issue, date = transform(f)
        out = OUT_DIR / f"outline_{date}.json"
        out.write_text(json.dumps(issue, ensure_ascii=False, indent=2), "utf-8")
        index_issues.append({
            "date": date,
            "date_display": issue["date_display"],
            "title": issue["title"],
            "total": issue["total"],
        })
        print(f"导出 {out.name} ({issue['total']} 条)")

    index_issues.sort(key=lambda x: x["date"], reverse=True)
    index = {"updated_at": index_issues[0]["date"], "issues": index_issues}
    (OUT_DIR / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), "utf-8")
    print(f"导出 index.json ({len(index_issues)} 期)")

    # 生成随包打包用的 local.js 映射
    lines = ["// 由 scripts/export_mini.py 自动生成，请勿手动编辑", "module.exports = {"]
    lines.append('  _index: require("./index.json"),')
    for iss in index_issues:
        lines.append(f'  "{iss["date"]}": require("./outline_{iss["date"]}.json"),')
    lines.append("};")
    (OUT_DIR / "local.js").write_text("\n".join(lines) + "\n", "utf-8")
    print(f"导出 local.js ({len(index_issues)} 期)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
