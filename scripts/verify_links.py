#!/usr/bin/env python3
"""Verify all dashboard links are accessible via HTTP.

Usage:
    python3 verify_links.py <dashboard.html>
    python3 verify_links.py --urls url1 url2 url3 ...

Returns exit code 0 if all links pass, 1 if any fail.
Outputs a JSON report of results to stdout.
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

BLOCKED_DOMAINS = {
    # Curl may get 403/501 but browser works fine. These are allowed with warning.
    "openaccess.thecvf.com",
    "arxiv.org",
    "baijiahao.baidu.com",
}


def extract_urls_from_html(html_path: str) -> list[str]:
    with open(html_path) as f:
        html = f.read()
    urls = re.findall(r'href="(https://[^"]+)"', html)
    return urls


def check_url(url: str, timeout: int = 15) -> dict:
    result = {"url": url, "status": None, "final_url": url, "error": None}
    try:
        proc = subprocess.run(
            [
                "curl", "-s", "-L",
                "-A", USER_AGENT,
                "--max-time", str(timeout),
                "--noproxy", "*",
                "-o", "/dev/null",
                "-w", "%{http_code}\n%{url_effective}",
                url,
            ],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        lines = proc.stdout.strip().split("\n")
        result["status"] = int(lines[0]) if lines[0].isdigit() else None
        if len(lines) > 1:
            result["final_url"] = lines[1]

        if result["status"] is None:
            result["error"] = f"curl returned no status code: {proc.stdout[:200]}"
    except subprocess.TimeoutExpired:
        result["error"] = "timeout"
    except Exception as e:
        result["error"] = str(e)

    return result


def classify_result(result: dict) -> str:
    """Classify as 'ok', 'warn', or 'fail'."""
    if result["error"]:
        return "fail"

    status = result["status"]
    if status in (200, 301, 302):
        domain = urlparse(result["url"]).netloc
        if domain in BLOCKED_DOMAINS and status in (403, 501):
            return "warn"
        return "ok"

    if status in (403, 501):
        domain = urlparse(result["url"]).netloc
        if domain in BLOCKED_DOMAINS:
            return "warn"

    return "fail"


def main():
    if "--urls" in sys.argv:
        idx = sys.argv.index("--urls")
        urls = sys.argv[idx + 1:]
    elif len(sys.argv) > 1:
        urls = extract_urls_from_html(sys.argv[1])
    else:
        print("Usage: verify_links.py <dashboard.html> | --urls url1 url2 ...", file=sys.stderr)
        sys.exit(2)

    if not urls:
        print(json.dumps({"error": "no URLs found", "results": []}))
        sys.exit(1)

    results = []
    for url in urls:
        r = check_url(url)
        r["verdict"] = classify_result(r)
        results.append(r)
        # Brief delay to avoid rate-limiting
        time.sleep(0.3)

    ok = sum(1 for r in results if r["verdict"] == "ok")
    warn = sum(1 for r in results if r["verdict"] == "warn")
    fail = sum(1 for r in results if r["verdict"] == "fail")

    report = {
        "total": len(results),
        "ok": ok,
        "warn": warn,
        "fail": fail,
        "results": results,
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))

    if fail > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
