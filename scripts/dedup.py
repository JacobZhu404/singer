#!/usr/bin/env python3
"""
历史去重：对比候选新闻与历史 outline/HTML，排除已收录内容。

用法:
    python3 scripts/dedup.py --candidates candidates.json [--history-dir data/] [--threshold 0.6]

输入:
    candidates.json — 当日候选新闻列表（数组，每项含 title / url 字段）
输出:
    deduped.json — 去重后的候选列表
    dedup_report.md — 排除报告（记录了哪些被排除及原因）
"""
import argparse
import json
import re
import sys
from pathlib import Path
from difflib import SequenceMatcher
from datetime import datetime
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from utils.outline_schema import Outline

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"


# ─────────────────────────── 历史加载 ───────────────────────────

def load_history_titles(history_dir: Path) -> List[dict]:
    """从 data/ 目录加载所有历史标题和URL，返回 [{title, url, date, source}]。"""
    records = []

    # 1) outline JSON（结构化，首选）
    for fp in sorted(history_dir.glob("outline_*.json")):
        try:
            o = Outline.from_file(fp)
            for item in o.flat_items():
                records.append({
                    "title": item.title,
                    "url": item.url,
                    "date": item.date,
                    "source": "outline",
                })
        except Exception as e:
            print(f"  ⚠️  outline 加载失败: {fp.name} — {e}")

    # 2) 仪表盘 HTML（无outline时的fallback）
    for fp in sorted(history_dir.glob("自动驾驶晨报_*.html")):
        date_str = re.search(r"_(\d{8})\.html", fp.name)
        if not date_str:
            continue
        fd = date_str.group(1)
        # 跳过已有 outline 的（避免重复）
        if (history_dir / f"outline_{fd}.json").exists():
            continue
        try:
            text = fp.read_text("utf-8")
            # 提取卡片标题
            titles = re.findall(r'<div class="card-title">([^<]+)</div>', text)
            urls = re.findall(r'<a class="card"[^>]*?href="([^"]+)"', text)
            for t, u in zip(titles, urls):
                records.append({
                    "title": t.strip(),
                    "url": u.strip(),
                    "date": fd,
                    "source": "html",
                })
        except Exception as e:
            print(f"  ⚠️  HTML 加载失败: {fp.name} — {e}")

    return records


# ─────────────────────────── 相似度匹配 ───────────────────────────

def normalize(s: str) -> str:
    """标准化：去空格、大小写、特殊字符。"""
    s = s.strip().lower()
    s = re.sub(r"[^\u4e00-\u9fff\w]", "", s)  # 只留中文和字母数字
    return s


def title_similarity(t1: str, t2: str) -> float:
    """标题相似度（0-1）。"""
    n1, n2 = normalize(t1), normalize(t2)
    if not n1 or not n2:
        return 0.0
    if n1 in n2 or n2 in n1:
        return min(len(n1), len(n2)) / max(len(n1), len(n2))
    return SequenceMatcher(None, n1, n2).ratio()


def is_duplicate(candidate: dict, history: List[dict], threshold: float = 0.6) -> Tuple[bool, str]:
    """
    判断 candidate 是否与历史重复。
    返回: (是否重复, 原因)
    """
    c_title = candidate.get("title", "")
    c_url = candidate.get("url", "")

    for h in history:
        # URL 精确匹配
        if c_url and h["url"] and c_url == h["url"]:
            return True, f"URL 精确匹配 → 历史#{h['title'][:30]}"
        # URL 相似（同一站点但路径不同）
        if c_url and h["url"]:
            c_u = c_url.rstrip("/").split("//")[-1].split("/")[0]
            h_u = h["url"].rstrip("/").split("//")[-1].split("/")[0]
            if c_u == h_u and c_url != h["url"]:
                # 同一域名下不同URL，检查标题
                sim = title_similarity(c_title, h["title"])
                if sim >= threshold:
                    return True, f"域名相同+标题相似 {sim:.2f} → 历史#{h['title'][:30]}"
        sim = title_similarity(c_title, h["title"])
        if sim >= threshold:
            return True, f"标题相似 {sim:.2f} → 历史#{h['title'][:30]}"

    return False, ""


# ─────────────────────────── 主流程 ───────────────────────────

def main():
    parser = argparse.ArgumentParser(description="历史去重")
    parser.add_argument("--candidates", required=True, help="候选 JSON 路径")
    parser.add_argument("--history-dir", default=str(DATA_DIR), help="历史 data 目录")
    parser.add_argument("--threshold", type=float, default=0.6, help="标题相似度阈值")
    parser.add_argument("--output", default=None, help="去重结果 JSON 路径")
    args = parser.parse_args()

    # 1) 加载候选
    candidates_path = Path(args.candidates)
    if not candidates_path.exists():
        print(f"❌ 候选文件不存在: {candidates_path}")
        return 1
    candidates = json.loads(candidates_path.read_text("utf-8"))
    if isinstance(candidates, dict):
        candidates = candidates.get("candidates", candidates.get("items", []))
    print(f"📥 候选: {len(candidates)} 条")

    # 2) 加载历史
    print(f"📚 加载历史: {args.history_dir}")
    history = load_history_titles(Path(args.history_dir))
    print(f"   历史记录: {len(history)} 条")

    # 3) 逐条去重
    kept = []
    excluded = []
    for c in candidates:
        dup, reason = is_duplicate(c, history, args.threshold)
        if dup:
            excluded.append({**c, "_exclude_reason": reason})
            print(f"  ❌ #{c.get('num', '?')} {c['title'][:30]} → {reason}")
        else:
            kept.append(c)
            print(f"  ✅ #{c.get('num', '?')} {c['title'][:30]}")

    # 4) 输出
    result = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_candidates": len(candidates),
        "kept": len(kept),
        "excluded": len(excluded),
        "items": kept,
    }
    out_path = Path(args.output) if args.output else candidates_path.parent / "deduped.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n📄 去重结果: {out_path}")
    print(f"   保留: {len(kept)}, 排除: {len(excluded)}")

    # 5) 报告
    if excluded:
        report = candidates_path.parent / "dedup_report.md"
        lines = [f"# 去重排除报告 ({result['date']})", "", f"候选 {len(candidates)} → 保留 {len(kept)} | 排除 {len(excluded)}", ""]
        for e in excluded:
            lines.append(f"- **#{e.get('num', '?')}** {e.get('title', '')}")
            lines.append(f"  - 原因: {e['_exclude_reason']}")
            if e.get("url"):
                lines.append(f"  - URL: {e['url']}")
            lines.append("")
        report.write_text("\n".join(lines), "utf-8")
        print(f"   排除报告: {report}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
