#!/usr/bin/env python3
"""
管线功能测试脚本 — 调用主 pipeline 的函数，产出写到 data/test/。

用法:
    python3 scripts/test_pipeline.py                    # 运行全部测试
    python3 scripts/test_pipeline.py --only html        # 只测 HTML 生成
    python3 scripts/test_pipeline.py --only schema      # 只测 schema 验证
    python3 scripts/test_pipeline.py --only links       # 只测链接页生成
    python3 scripts/test_pipeline.py --only dedup       # 只测去重
    python3 scripts/test_pipeline.py --only cover       # 只测封面生成
    python3 scripts/test_pipeline.py --only collect     # 只测热榜爬虫
    python3 scripts/test_pipeline.py --only dates       # 只测日期解析+截断排序(纯函数,无网络)
    python3 scripts/test_pipeline.py --only checklist   # 只测搜索计划

所有产出写入 data/test/，不污染 data/ 正式文件。
"""
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
TEST_DIR = DATA / "test"
TEST_DIR.mkdir(exist_ok=True)

TODAY = datetime.now().strftime("%Y%m%d")
PASS = 0
FAIL = 0


def _report(name, ok, detail=""):
    global PASS, FAIL
    icon = "PASS" if ok else "FAIL"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    msg = f"  [{icon}] {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)


def _find_latest_outline():
    outlines = sorted(DATA.glob("outline_*.json"), reverse=True)
    outlines = [o for o in outlines if "test" not in o.name]
    return outlines[0] if outlines else None


# ─────────────── schema ───────────────

def test_schema():
    print("\n=== Schema 验证 ===")
    from utils.outline_schema import Outline, load_outline

    src = _find_latest_outline()
    if not src:
        _report("load_outline", False, "data/ 下无 outline_*.json")
        return

    try:
        outline = load_outline(src)
        _report("load_outline", True, f"{src.name} → {outline.total} items")
    except Exception as e:
        _report("load_outline", False, str(e))
        return

    _report("sections count", len(outline.sections) == 5,
            f"期望5个版块, 实际{len(outline.sections)}")
    _report("all_urls populated",
            len(outline.metadata.all_urls) > 0,
            f"{len(outline.metadata.all_urls)} URLs")

    for sec in outline.sections:
        for item in sec.items:
            if not item.url:
                _report(f"item#{item.num} URL", False, f"「{item.title}」无 URL")


# ─────────────── html ───────────────

def test_html():
    print("\n=== HTML 生成 ===")
    from utils.outline_schema import load_outline
    from generate_html import render

    src = _find_latest_outline()
    if not src:
        _report("find outline", False, "data/ 下无 outline_*.json")
        return

    try:
        outline = load_outline(src)
        html_str = render(outline)
        out = TEST_DIR / f"test_html_{TODAY}.html"
        out.write_text(html_str, "utf-8")
        _report("render", True, f"→ {out.name} ({len(html_str)} bytes)")
    except Exception as e:
        _report("render", False, str(e))
        return

    _report("header present", '<div class="header">' in html_str)
    _report("overview present", '<div class="overview">' in html_str)
    actual_cards = html_str.count('class="card-title"')
    _report("card count matches",
            actual_cards == outline.total,
            f"期望{outline.total}, 实际{actual_cards}")


# ─────────────── links ───────────────

def test_links():
    print("\n=== 链接页生成 ===")
    from utils.parse_cards import extract_cards, group_by_section

    htmls = sorted(DATA.glob("自动驾驶晨报_*.html"), reverse=True)
    htmls = [h for h in htmls if "test" not in h.name]
    if not htmls:
        _report("find HTML", False, "data/ 下无晨报 HTML")
        return

    src = htmls[0]
    html_text = src.read_text("utf-8")

    try:
        cards = extract_cards(html_text)
        _report("extract_cards", len(cards) > 0, f"{len(cards)} 张卡片")
    except Exception as e:
        _report("extract_cards", False, str(e))
        return

    sections = group_by_section(cards)
    _report("group_by_section", len(sections) > 0, f"{len(sections)} 个版块")

    from generate_links_page import build_links_html
    links_html = build_links_html(sections, len(cards), "测试日期")
    out = TEST_DIR / f"test_links_{TODAY}.html"
    out.write_text(links_html, "utf-8")
    _report("build_links_html", True, f"→ {out.name}")


# ─────────────── dedup ───────────────

def test_dedup():
    print("\n=== 去重 ===")
    from dedup import title_similarity, is_duplicate, load_history_titles

    sim = title_similarity("Tesla Robotaxi 德州车队仅42辆", "Tesla Robotaxi德州车队仅42辆")
    _report("exact similarity", sim > 0.9, f"相似度={sim:.2f}")

    sim2 = title_similarity("完全不相关的新闻标题", "Tesla Robotaxi 德州车队")
    _report("unrelated similarity", sim2 < 0.3, f"相似度={sim2:.2f}")

    history = load_history_titles(DATA)
    _report("load_history", True, f"历史 {len(history)} 条")

    dup, reason = is_duplicate(
        {"title": "测试完全不重复的标题XYZ", "url": "https://example.com/unique"},
        history
    )
    _report("unique not flagged", not dup, reason if dup else "正确放行")


# ─────────────── cover ───────────────

def test_cover():
    print("\n=== 封面生成 (仅验证字体和裁剪逻辑) ===")
    from generate_cover import find_font, process_cover

    try:
        font = find_font(70)
        _report("find_font", True, f"字体: {font.path if hasattr(font, 'path') else 'OK'}")
    except Exception as e:
        _report("find_font", False, str(e))
        return

    bgs = sorted(DATA.glob("A_*.png")) + sorted(DATA.glob("科技封面*.png"))
    if not bgs:
        _report("background images", False, "data/ 下无背景图")
        return

    bg = bgs[0]
    try:
        result_path = process_cover(bg, "20260706", "测试晨报")
        if result_path.exists():
            import shutil
            dest = TEST_DIR / f"test_cover_{TODAY}.png"
            shutil.copy2(result_path, dest)
            _report("process_cover", True, f"→ {dest.name}")
        else:
            _report("process_cover", False, "输出文件不存在")
    except Exception as e:
        _report("process_cover", False, str(e))


# ─────────────── collect ───────────────

def test_collect():
    print("\n=== 阶段一·脚本采集 (dry run) ===")
    from collect_news import fetch_tophub, fetch_buzzing, dedup_candidates

    try:
        p1 = fetch_tophub()
        _report("fetch_tophub", True, f"{len(p1)} 条")
    except Exception as e:
        _report("fetch_tophub", False, str(e))
        p1 = []

    try:
        p2 = fetch_buzzing()
        _report("fetch_buzzing", True, f"{len(p2)} 条")
    except Exception as e:
        _report("fetch_buzzing", False, str(e))
        p2 = []

    merged = dedup_candidates(p1 + p2)[:5]
    _report("dedup_candidates", True, f"{len(merged)} 条")

    from utils.candidate_schema import CandidateList, save_candidates
    cl = CandidateList(date=TODAY, source_type="script_crawl",
                       total=len(merged), candidates=merged)
    out = TEST_DIR / f"test_collect_{TODAY}.json"
    save_candidates(cl, out)
    _report("candidates schema", True, f"→ {out.name}")


# ─────────────── merge ───────────────

def test_merge():
    print("\n=== 阶段二·融合 ===")
    from utils.candidate_schema import Candidate, CandidateList, save_candidates
    from merge_candidates import internal_dedup, history_dedup

    c1 = Candidate(title="测试新闻A：智驾芯片突破", url="https://example.com/a",
                   source_name="测试", origin="llm_search", confidence="high")
    c2 = Candidate(title="测试新闻A：智驾芯片突破！", url="https://example.com/a2",
                   source_name="测试", origin="script_crawl", confidence="high")
    c3 = Candidate(title="完全不同的新闻B", url="https://example.com/b",
                   source_name="测试", origin="script_crawl", confidence="low")
    c4 = Candidate(title="重复URL新闻", url="https://example.com/a",
                   source_name="测试", origin="script_crawl", confidence="high")

    kept, excluded = internal_dedup([c1, c2, c3, c4])
    _report("internal_dedup", len(kept) == 2,
            f"4条→{len(kept)}条保留, {len(excluded)}条排除")

    kept2, hist_excluded = history_dedup(kept, DATA)
    _report("history_dedup", len(kept2) <= len(kept),
            f"{len(kept)}条→{len(kept2)}条保留")

    cl = CandidateList(date=TODAY, source_type="merged",
                       total=len(kept2), candidates=kept2)
    out = TEST_DIR / f"test_merged_{TODAY}.json"
    save_candidates(cl, out)
    _report("save merged", out.exists(), f"→ {out.name}")


# ─────────────── checklist ───────────────

def test_checklist():
    print("\n=== 搜索计划 ===")
    from search_checklist import load_matrix

    try:
        matrix = load_matrix()
        groups = matrix.get("search_groups", [])
        hotlists = matrix.get("hotlist", [])
        _report("load_matrix", True,
                f"{len(groups)} 搜索组 + {len(hotlists)} 热榜")
        _report("has freshness", "freshness" in matrix,
                f"freshness keys: {list(matrix.get('freshness', {}).keys())}")
    except Exception as e:
        _report("load_matrix", False, str(e))


# ─────────────── wechat ───────────────

def test_wechat():
    print("\n=== 微信正文生成 ===")
    from utils.outline_schema import load_outline
    from generate_wechat import render_wechat

    src = _find_latest_outline()
    if not src:
        _report("find outline", False, "data/ 下无 outline_*.json")
        return

    try:
        outline = load_outline(src)
        result = render_wechat(outline)
        out = TEST_DIR / f"test_wechat_{TODAY}.html"
        out.write_text(result["content"], "utf-8")
        _report("render_wechat", True,
                f"→ {out.name} ({len(result['content'])} bytes)")
    except Exception as e:
        _report("render_wechat", False, str(e))
        return

    _report("title format", result["title"].startswith("自动驾驶晨报"),
            result["title"])
    _report("digest format", "动态" in result["digest"],
            result["digest"])
    _report("digest bytes", len(result["digest"].encode()) <= 54,
            f"{len(result['digest'].encode())} bytes")

    content = result["content"]
    _report("header present", "AUTONOMOUS DRIVING" in content)
    _report("overview table", "今日概览" in content)
    card_count = content.count('font-size:16px;font-weight:bold;color:#111827')
    _report("card count", card_count == outline.total,
            f"期望{outline.total}, 实际{card_count}")


# ─────────────── 日期/排序纯函数（无网络） ───────────────

def test_dates():
    print("\n=== 日期解析 + 截断排序（纯函数） ===")
    from collect_news import _parse_rss_date, _parse_date_from_url, cap_per_source
    from utils.candidate_schema import Candidate

    # _parse_rss_date：RFC2822 / ISO / 空
    _report("rss date RFC2822",
            _parse_rss_date("Thu, 09 Jul 2026 03:45:00 GMT") == "2026-07-09")
    _report("rss date ISO",
            _parse_rss_date("2026-07-08T10:00:00Z") == "2026-07-08")
    _report("rss date empty", _parse_rss_date("") == "")

    # _parse_date_from_url：连字符式 / 斜杠分隔的8位紧凑式 / 无日期
    _report("url date sina(连字符)",
            _parse_date_from_url("https://finance.sina.com.cn/x/2026-07-09/doc-y.shtml") == "2026-07-09")
    _report("url date newscn(斜杠8位)",
            _parse_date_from_url("http://www.news.cn/tech/20260706/d611abc/c.html") == "2026-07-06")
    _report("url date none",
            _parse_date_from_url("https://example.com/article/abc.html") == "")
    # 已知局限：紧凑日期后紧跟字母(如腾讯 /rain/a/20260710A0..)不被识别，需靠 RSS/其他兜底
    _report("url date tencent紧凑无分隔→空(已知局限)",
            _parse_date_from_url("https://news.qq.com/rain/a/20260710A067TS00") == "")

    # 截断排序：同源多条，排序后 cap=1 应保留「高置信 + 最新」
    same = "新浪"
    items = [
        Candidate(title="低置信新文", url="u1", source_name=same, date="2026-07-10", origin="script_crawl", confidence="low"),
        Candidate(title="高置信旧文", url="u2", source_name=same, date="2026-07-05", origin="script_crawl", confidence="high"),
        Candidate(title="高置信新文", url="u3", source_name=same, date="2026-07-10", origin="script_crawl", confidence="high"),
        Candidate(title="高置信无日期", url="u4", source_name=same, date="", origin="script_crawl", confidence="high"),
    ]
    _rank = {"high": 0, "low": 1}
    items.sort(key=lambda c: c.date or "0000-00-00", reverse=True)
    items.sort(key=lambda c: _rank.get(c.confidence, 2))
    capped = cap_per_source(items, 1)
    _report("cap keeps newest high", len(capped) == 1 and capped[0].title == "高置信新文",
            capped[0].title if capped else "空")


# ─────────────── main ───────────────

ALL_TESTS = {
    "schema": test_schema,
    "html": test_html,
    "links": test_links,
    "dedup": test_dedup,
    "cover": test_cover,
    "collect": test_collect,
    "merge": test_merge,
    "checklist": test_checklist,
    "wechat": test_wechat,
    "dates": test_dates,
}


def main():
    parser = argparse.ArgumentParser(description="管线功能测试")
    parser.add_argument("--only", choices=list(ALL_TESTS.keys()),
                        help="只运行指定测试")
    args = parser.parse_args()

    print(f"管线测试 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"产出目录: {TEST_DIR}")

    if args.only:
        ALL_TESTS[args.only]()
    else:
        for fn in ALL_TESTS.values():
            fn()

    print(f"\n{'='*40}")
    print(f"结果: {PASS} PASS, {FAIL} FAIL")
    return 1 if FAIL > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
