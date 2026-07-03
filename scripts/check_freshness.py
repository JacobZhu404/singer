#!/usr/bin/env python3
"""Check content freshness: filter items older than MAX_AGE_DAYS.

Input: JSON array of {url, date, ...} items
Output: filtered JSON + stale items list

Usage:
    python3 check_freshness.py <items.json> [--max-age 30] [--ref-date 2026-07-01]
"""

import json
import re
import sys
from datetime import datetime, timedelta

# Chinese date patterns to parse
DATE_PATTERNS = [
    (re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日"), "ymd_cn"),
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), "iso"),
    (re.compile(r"(\d{1,2})月(\d{1,2})日"), "md_cn"),  # assumes current year
]

MONTH_CN = {
    "一月": 1, "二月": 2, "三月": 3, "四月": 4,
    "五月": 5, "六月": 6, "七月": 7, "八月": 8,
    "九月": 9, "十月": 10, "十一月": 11, "十二月": 12,
}


def parse_date(text: str, default_year: int) -> datetime | None:
    for pat, style in DATE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        if style == "ymd_cn" or style == "iso":
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        elif style == "md_cn":
            return datetime(default_year, int(m.group(1)), int(m.group(2)))
    return None


def main():
    max_age = 30
    ref_date = datetime.now()
    input_path = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--max-age" and i + 1 < len(args):
            max_age = int(args[i + 1])
            i += 2
        elif args[i] == "--ref-date" and i + 1 < len(args):
            ref_date = datetime.strptime(args[i + 1], "%Y-%m-%d")
            i += 2
        elif not args[i].startswith("--"):
            input_path = args[i]
            i += 1
        else:
            i += 1

    if not input_path:
        print("Usage: check_freshness.py <items.json> [--max-age 30] [--ref-date 2026-07-01]", file=sys.stderr)
        sys.exit(2)

    with open(input_path) as f:
        items = json.load(f)

    cutoff = ref_date - timedelta(days=max_age)
    fresh = []
    stale = []

    for item in items:
        date_str = item.get("date", "")
        parsed = parse_date(date_str, ref_date.year)
        if parsed and parsed >= cutoff:
            fresh.append(item)
        elif parsed:
            item["_stale_since"] = parsed.isoformat()
            stale.append(item)
        else:
            # No parseable date: keep but warn
            item["_date_unparseable"] = True
            fresh.append(item)

    report = {
        "total": len(items),
        "fresh": len(fresh),
        "stale": len(stale),
        "cutoff_date": cutoff.strftime("%Y-%m-%d"),
        "fresh_items": fresh,
        "stale_items": stale,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if stale:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
