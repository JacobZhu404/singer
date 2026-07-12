#!/usr/bin/env python3
"""搜索 Checklist — 把 search_matrix.yaml 变成可执行的进度追踪。

LLM 搜索前 init 生成计划，搜完一组 update 写回结果，最后 verify 检查覆盖率。
消除 LLM 自由搜索时的覆盖漂移问题。

用法:
    python3 scripts/search_checklist.py init   [--date YYYYMMDD]
    python3 scripts/search_checklist.py update  --date YYYYMMDD --group ID --results JSON
    python3 scripts/search_checklist.py verify  [--date YYYYMMDD]
    python3 scripts/search_checklist.py status  [--date YYYYMMDD]
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List

try:
    import yaml
except ImportError:
    print("需要 PyYAML: pip install pyyaml")
    sys.exit(1)

BASE = Path(__file__).resolve().parent.parent
MATRIX_PATH = BASE / "references" / "search_matrix.yaml"
DATA_DIR = BASE / "data"


def load_matrix():
    return yaml.safe_load(MATRIX_PATH.read_text("utf-8"))


def plan_path(date_str):
    return DATA_DIR / f"search_plan_{date_str}.json"


def load_plan(date_str):
    pp = plan_path(date_str)
    if not pp.exists():
        print(f"搜索计划不存在: {pp}")
        print(f"请先运行: python3 scripts/search_checklist.py init --date {date_str}")
        sys.exit(1)
    return json.loads(pp.read_text("utf-8"))


def save_plan(date_str, plan):
    completed = sum(1 for g in plan["groups"] if g["status"] == "done")
    plan["summary"]["completed"] = completed
    plan["summary"]["pending"] = plan["summary"]["total_groups"] - completed
    pp = plan_path(date_str)
    pp.write_text(json.dumps(plan, ensure_ascii=False, indent=2), "utf-8")


# ─────────────────────────── init ───────────────────────────

def cmd_init(args):
    matrix = load_matrix()
    groups = []  # type: List[dict]
    gid = 0

    for hl in matrix.get("hotlist", []):
        groups.append({
            "id": gid,
            "name": f"热榜{hl['rank']}-{hl['url'].split('//')[1].split('/')[0]}",
            "type": "hotlist",
            "method": hl.get("method", "WebFetch"),
            "url": hl["url"],
            "keywords": hl.get("keywords", []),
            "section": "按内容归入对应版块",
            "status": "pending",
            "results": [],
        })
        gid += 1

    for sg in matrix.get("search_groups", []):
        kws = list(sg.get("keywords", []))
        if "subgroups" in sg:
            for sub in sg["subgroups"]:
                kws.extend(sub.get("keywords", []))
        groups.append({
            "id": gid,
            "name": f"第{sg['id']}组-{sg['name']}",
            "type": "search",
            "method": "WebSearch",
            "keywords": kws,
            "section": sg.get("section", ""),
            "priority": sg.get("priority", 0),
            "notes": sg.get("notes", ""),
            "status": "pending",
            "results": [],
        })
        gid += 1

    plan = {
        "date": args.date,
        "created_at": datetime.now().isoformat(),
        "matrix_version": matrix.get("meta", {}).get("version", "?"),
        "freshness": matrix.get("freshness", {}),
        "groups": groups,
        "summary": {
            "total_groups": len(groups),
            "completed": 0,
            "pending": len(groups),
        },
    }

    save_plan(args.date, plan)
    pp = plan_path(args.date)
    print(f"搜索计划已生成: {pp}")
    print(f"共 {len(groups)} 组 (热榜 {sum(1 for g in groups if g['type']=='hotlist')} + 搜索 {sum(1 for g in groups if g['type']=='search')})")
    print()
    for g in groups:
        kw_preview = ", ".join(g["keywords"][:3])
        if len(g["keywords"]) > 3:
            kw_preview += f" ... (+{len(g['keywords'])-3})"
        print(f"  [{g['id']:2d}] {g['name']:<25} → {g['section']:<15} 关键词: {kw_preview}")
    print()
    print("LLM 搜索时逐组执行,每完成一组运行:")
    print(f"  python3 scripts/search_checklist.py update --date {args.date} --group <ID> --results '<JSON>'")


# ─────────────────────────── update ───────────────────────────

def cmd_update(args):
    plan = load_plan(args.date)

    target = None
    for g in plan["groups"]:
        if g["id"] == args.group:
            target = g
            break
    if not target:
        print(f"组 {args.group} 不存在 (有效范围: 0-{len(plan['groups'])-1})")
        return 1

    try:
        results = json.loads(args.results)
        if isinstance(results, dict):
            results = [results]
    except json.JSONDecodeError as e:
        print(f"JSON 解析失败: {e}")
        return 1

    target["status"] = "done"
    target["results"] = results
    target["completed_at"] = datetime.now().isoformat()

    save_plan(args.date, plan)

    completed = plan["summary"]["completed"]
    total = plan["summary"]["total_groups"]
    print(f"[{completed}/{total}] 第{args.group}组「{target['name']}」已完成,记录 {len(results)} 条结果")

    pending = [g for g in plan["groups"] if g["status"] == "pending"]
    if pending:
        nxt = pending[0]
        print(f"  下一组: [{nxt['id']}] {nxt['name']}")
    else:
        print("  所有组已完成! 运行 verify 检查覆盖率:")
        print(f"  python3 scripts/search_checklist.py verify --date {args.date}")
    return 0


# ─────────────────────────── 覆盖缺口检测 ───────────────────────────

def _company_aliases(entry):
    """把一个企业条目展开成匹配别名集合。

    大疆(卓驭) → {大疆, 卓驭}；百度Apollo → {百度Apollo, 百度, Apollo}
    """
    entry = entry.strip()
    aliases = set()
    m = re.match(r"^(.*?)[\(（](.*?)[\)）]\s*$", entry)
    if m:
        base, alt = m.group(1).strip(), m.group(2).strip()
        aliases.update(a for a in (base, alt) if a)
        core = base
    else:
        core = entry
    if core:
        aliases.add(core)
    parts = re.findall(r"[A-Za-z0-9.]+|[一-鿿]+", core)
    if len(parts) > 1:
        aliases.update(parts)
    return {a for a in aliases if a}


def _parse_companies(matrix):
    companies = []
    for line in matrix.get("companies", []):
        for c in str(line).split(","):
            c = c.strip()
            if c:
                companies.append(c)
    return companies


def _results_corpus(completed):
    chunks = []
    for g in completed:
        for r in g["results"]:
            if isinstance(r, dict):
                chunks.append(f"{r.get('title','')} {r.get('snippet','')} {r.get('source','')}")
            else:
                chunks.append(str(r))
    return " ".join(chunks)


# ─────────────────────────── verify ───────────────────────────

def cmd_verify(args):
    plan = load_plan(args.date)
    groups = plan["groups"]
    completed = [g for g in groups if g["status"] == "done"]
    pending = [g for g in groups if g["status"] == "pending"]
    total_results = sum(len(g["results"]) for g in completed)

    print(f"搜索覆盖率报告 — {args.date}")
    print(f"{'='*60}")
    print(f"总组数: {len(groups)}  已完成: {len(completed)}  未完成: {len(pending)}")
    print(f"总候选条数: {total_results}")
    print()

    if pending:
        print("未完成的组:")
        for g in pending:
            kw_preview = ", ".join(g["keywords"][:3])
            print(f"  [{g['id']:2d}] {g['name']:<25} 关键词: {kw_preview}")
        print()

    section_counts = {}
    for g in completed:
        sec = g.get("section", "未分类")
        section_counts.setdefault(sec, 0)
        section_counts[sec] += len(g["results"])

    if section_counts:
        print("各版块候选条数:")
        for sec, cnt in sorted(section_counts.items(), key=lambda x: -x[1]):
            bar = "█" * min(cnt, 30)
            print(f"  {sec:<20} {cnt:3d} {bar}")
        print()

    # 0 命中组: 已完成但一条结果都没有 → 覆盖率%掩盖不了的真实缺口
    zero_hit = [g for g in completed if len(g["results"]) == 0]
    if zero_hit:
        print("⚠️  0 命中的组 (已搜索但无结果,可能是关键词失效或该主题今日无新闻):")
        for g in zero_hit:
            kw_preview = ", ".join(g["keywords"][:3])
            print(f"  [{g['id']:2d}] {g['name']:<25} 关键词: {kw_preview}")
        print()

    # companies 覆盖: 矩阵点名要覆盖的企业里,今日候选一条都没提到的
    matrix = load_matrix()
    companies = _parse_companies(matrix)
    if companies:
        corpus = _results_corpus(completed).lower()
        missing = [c for c in companies
                   if not any(a.lower() in corpus for a in _company_aliases(c))]
        covered = len(companies) - len(missing)
        print(f"企业覆盖: {covered}/{len(companies)} 家在今日候选中出现")
        if missing:
            print("  未覆盖: " + "、".join(missing))
        print()

    if not pending:
        print("所有组已完成,可进入去重和 outline 生成阶段。")
        return 0
    else:
        pct = len(completed) / len(groups) * 100
        print(f"覆盖率: {pct:.0f}% — 还有 {len(pending)} 组未搜索,可能遗漏重要新闻!")
        return 1


# ─────────────────────────── status ───────────────────────────

def cmd_status(args):
    plan = load_plan(args.date)
    groups = plan["groups"]
    completed = plan["summary"]["completed"]
    total = plan["summary"]["total_groups"]
    print(f"[{completed}/{total}] {args.date} 搜索进度")
    for g in groups:
        icon = "✅" if g["status"] == "done" else "⬜"
        cnt = len(g["results"]) if g["status"] == "done" else ""
        cnt_str = f" ({cnt}条)" if cnt else ""
        print(f"  {icon} [{g['id']:2d}] {g['name']}{cnt_str}")


# ─────────────────────────── main ───────────────────────────

def main():
    parser = argparse.ArgumentParser(description="搜索 Checklist")
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="生成今日搜索计划")
    p_init.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))

    p_update = sub.add_parser("update", help="记录一组搜索结果")
    p_update.add_argument("--date", required=True)
    p_update.add_argument("--group", type=int, required=True, help="组ID")
    p_update.add_argument("--results", required=True, help="JSON 结果数组")

    p_verify = sub.add_parser("verify", help="检查搜索覆盖率")
    p_verify.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))

    p_status = sub.add_parser("status", help="查看当前进度")
    p_status.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    if args.command == "init":
        return cmd_init(args)
    elif args.command == "update":
        return cmd_update(args)
    elif args.command == "verify":
        return cmd_verify(args)
    elif args.command == "status":
        return cmd_status(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)
