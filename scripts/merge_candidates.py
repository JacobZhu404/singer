#!/usr/bin/env python3
"""
阶段二·融合 — 合并 LLM 和脚本的候选，执行机械化质量检查。

流程：
  1. 合并两个来源的候选列表
  2. 标题+URL 去重（内部去重）
  3. 历史去重（与已发布的 outline 去重）
  4. 链接可达性验证（串行 HTTP 请求）
  5. 产出 merged_candidates + merge_report

用法：
    python3 scripts/merge_candidates.py \\
      --llm data/llm_candidates_YYYYMMDD.json \\
      --script data/script_candidates_YYYYMMDD.json \\
      --output data/merged_candidates_YYYYMMDD.json

    # 只传一个来源也行
    python3 scripts/merge_candidates.py \\
      --script data/script_candidates_YYYYMMDD.json

    # 跳过链接验证（更快）
    python3 scripts/merge_candidates.py \\
      --llm ... --script ... --skip-url-check
"""
import argparse
import json
import re
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from utils.candidate_schema import (
    Candidate, CandidateList, load_candidates, save_candidates,
)
from dedup import load_history_titles, is_duplicate, title_similarity

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"
TODAY = datetime.now()
TD_STR = TODAY.strftime("%Y%m%d")


# ─────────────── 内部去重 ───────────────

def _normalize_title(title: str) -> str:
    t = re.sub(r"\s+", "", title).lower()
    t = re.sub(r"[，。、：；！？""''（）\[\]【】｜|·—\-_,.:;!?\"'()​]", "", t)
    return t


def internal_dedup(candidates: List[Candidate]) -> Tuple[List[Candidate], List[dict]]:
    """内部去重：标题归一化 + URL 精确匹配。返回 (保留, 排除记录)。"""
    seen_titles = set()
    seen_urls = set()
    kept = []
    excluded = []

    for c in candidates:
        norm = _normalize_title(c.title)

        if norm in seen_titles:
            excluded.append({"title": c.title, "reason": "标题重复(内部)"})
            continue

        if c.url and c.url in seen_urls:
            excluded.append({"title": c.title, "reason": "URL重复(内部)"})
            continue

        # 模糊标题匹配（阈值 0.8，比历史去重更严格以减少误伤）
        dup_found = False
        for existing in kept:
            if title_similarity(c.title, existing.title) >= 0.8:
                excluded.append({
                    "title": c.title,
                    "reason": f"标题模糊重复 → {existing.title[:30]}",
                })
                dup_found = True
                break
        if dup_found:
            continue

        seen_titles.add(norm)
        if c.url:
            seen_urls.add(c.url)
        kept.append(c)

    return kept, excluded


# ─────────────── 历史去重 ───────────────

def history_dedup(
    candidates: List[Candidate], history_dir: Path, threshold: float = 0.6
) -> Tuple[List[Candidate], List[dict]]:
    """与历史 outline 去重。返回 (保留, 排除记录)。"""
    history = load_history_titles(history_dir)
    if not history:
        return candidates, []

    kept = []
    excluded = []
    for c in candidates:
        item = {"title": c.title, "url": c.url}
        dup, reason = is_duplicate(item, history, threshold)
        if dup:
            c.is_history_dup = True
            c.dup_reason = reason
            excluded.append({"title": c.title, "reason": f"历史重复: {reason}"})
        else:
            c.is_history_dup = False
            kept.append(c)

    return kept, excluded


# ─────────────── 链接验证 ───────────────

def verify_urls(candidates: List[Candidate]) -> int:
    """串行验证所有候选的 URL 可达性。返回不可达数量。"""
    from verify_links import check_url

    unreachable = 0
    total = len(candidates)
    for i, c in enumerate(candidates, 1):
        if not c.url:
            c.url_reachable = False
            c.url_status = None
            unreachable += 1
            print(f"  [{i}/{total}] ⚠️ 无URL: {c.title[:40]}")
            continue

        result = check_url(c.url)
        status = result.get("status")
        c.url_status = status
        c.url_reachable = status is not None and status < 400
        icon = "✅" if c.url_reachable else "❌"
        print(f"  [{i}/{total}] {icon} {status or 'ERR'} {c.title[:40]}")

        if not c.url_reachable:
            unreachable += 1

    return unreachable


# ─────────────── 报告生成 ───────────────

def generate_report(
    date_str: str,
    input_counts: dict,
    after_internal_dedup: int,
    internal_excluded: List[dict],
    after_history_dedup: int,
    history_excluded: List[dict],
    unreachable: int,
    final_count: int,
    output_path: Path,
):
    lines = [
        f"# 融合报告 — {date_str}",
        "",
        "## 流程统计",
        "",
        f"| 阶段 | 数量 |",
        f"|------|------|",
        f"| LLM 候选 | {input_counts.get('llm', 0)} |",
        f"| 脚本候选 | {input_counts.get('script', 0)} |",
        f"| 合并总计 | {input_counts.get('total', 0)} |",
        f"| 内部去重后 | {after_internal_dedup} (-{input_counts.get('total', 0) - after_internal_dedup}) |",
        f"| 历史去重后 | {after_history_dedup} (-{after_internal_dedup - after_history_dedup}) |",
        f"| 链接不可达 | {unreachable} |",
        f"| **最终候选** | **{final_count}** |",
        "",
    ]

    if internal_excluded:
        lines.extend(["## 内部去重排除", ""])
        for e in internal_excluded:
            lines.append(f"- {e['title'][:60]} — {e['reason']}")
        lines.append("")

    if history_excluded:
        lines.extend(["## 历史去重排除", ""])
        for e in history_excluded:
            lines.append(f"- {e['title'][:60]} — {e['reason']}")
        lines.append("")

    report_path = output_path.parent / f"merge_report_{date_str}.md"
    report_path.write_text("\n".join(lines), "utf-8")
    return report_path


# ─────────────── main ───────────────

def main():
    parser = argparse.ArgumentParser(description="阶段二·融合")
    parser.add_argument("--llm", default=None, help="LLM 候选 JSON")
    parser.add_argument("--script", default=None, help="脚本候选 JSON")
    parser.add_argument("--output", default=None, help="输出路径")
    parser.add_argument("--history-dir", default=str(DATA_DIR), help="历史目录")
    parser.add_argument("--skip-url-check", action="store_true", help="跳过链接验证")
    args = parser.parse_args()

    if not args.llm and not args.script:
        print("❌ 至少需要 --llm 或 --script 之一")
        return 1

    print(f"🔗 阶段二·融合 — {TODAY.strftime('%Y-%m-%d %H:%M')}\n")

    # 1. 加载
    all_candidates: List[Candidate] = []
    input_counts = {"llm": 0, "script": 0, "total": 0}

    if args.llm:
        llm_cl = load_candidates(args.llm)
        all_candidates.extend(llm_cl.candidates)
        input_counts["llm"] = len(llm_cl.candidates)
        print(f"  📥 LLM 候选: {len(llm_cl.candidates)} 条")

    if args.script:
        script_cl = load_candidates(args.script)
        all_candidates.extend(script_cl.candidates)
        input_counts["script"] = len(script_cl.candidates)
        print(f"  📥 脚本候选: {len(script_cl.candidates)} 条")

    input_counts["total"] = len(all_candidates)
    print(f"  📊 合并总计: {len(all_candidates)} 条\n")

    # 2. 内部去重
    print("── 内部去重 ──")
    kept, internal_excluded = internal_dedup(all_candidates)
    print(f"  保留 {len(kept)}, 排除 {len(internal_excluded)}\n")

    # 3. 历史去重
    print("── 历史去重 ──")
    kept, history_excluded = history_dedup(kept, Path(args.history_dir))
    print(f"  保留 {len(kept)}, 排除 {len(history_excluded)}\n")

    # 4. 链接验证
    unreachable = 0
    if not args.skip_url_check and kept:
        print("── 链接验证 ──")
        unreachable = verify_urls(kept)
        print(f"  可达 {len(kept) - unreachable}, 不可达 {unreachable}\n")

    # 5. 产出
    date_str = TD_STR
    if args.llm:
        date_str = load_candidates(args.llm).date
    elif args.script:
        date_str = load_candidates(args.script).date

    result = CandidateList(
        date=date_str,
        source_type="merged",
        total=len(kept),
        candidates=kept,
    )

    out_path = Path(args.output) if args.output else DATA_DIR / f"merged_candidates_{date_str}.json"
    save_candidates(result, out_path)

    report_path = generate_report(
        date_str=date_str,
        input_counts=input_counts,
        after_internal_dedup=len(kept) + len(history_excluded),
        internal_excluded=internal_excluded,
        after_history_dedup=len(kept),
        history_excluded=history_excluded,
        unreachable=unreachable,
        final_count=len(kept),
        output_path=out_path,
    )

    print(f"📄 融合结果: {out_path}")
    print(f"   共 {len(kept)} 条候选")
    print(f"📋 融合报告: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
