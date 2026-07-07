#!/usr/bin/env python3
"""Check URL quality: reject homepages, login walls, and aggregator pages.

Usage:
    python3 check_url_quality.py --urls url1 url2 url3 ...
    python3 check_url_quality.py <urls.json>   # JSON array of URL strings
"""

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

def extract_urls_from_html(html_path: str) -> list[str]:
    with open(html_path) as f:
        html = f.read()
    urls = re.findall(r'href="(https://[^"]+)"', html)
    return urls

# Domains that are typically login-gated or social-media walls
LOGIN_WALL_DOMAINS = {
    "weibo.com", "sso.weibo.com", "passport.weibo.com",
    "zhihu.com",  # often requires login for full content
}

# Domains that are aggregator/index pages (not original articles)
AGGREGATOR_DOMAINS = {
    "papernotes.org",
}

# URL patterns that look like homepages (no article-specific path)
HOMEPAGE_PATTERNS = [
    re.compile(r"^https?://[^/]+/?$"),                          # domain only
    re.compile(r"^https?://[^/]+/(index\.(html|php|jsp))?$"),   # domain/index
    re.compile(r"^https?://[^/]+/press/$"),                      # press listing
    re.compile(r"^https?://[^/]+/news/$"),                       # news listing
]

# Minimum path depth for article URLs (number of / segments after domain)
MIN_PATH_DEPTH = 2


def _domain_matches(domain: str, target: str) -> bool:
    """域名后缀匹配,避免 fake-weibo.com 被误判为 weibo.com。"""
    return domain == target or domain.endswith("." + target)


def check_url(url: str) -> dict:
    result = {"url": url, "issues": [], "verdict": "ok"}
    parsed = urlparse(url)
    domain = parsed.netloc.lower().replace("www.", "")

    # Check login walls
    for ld in LOGIN_WALL_DOMAINS:
        if _domain_matches(domain, ld):
            result["issues"].append(f"login_wall:{ld}")
            result["verdict"] = "reject"
            break

    # Check aggregator
    for ad in AGGREGATOR_DOMAINS:
        if _domain_matches(domain, ad):
            result["issues"].append(f"aggregator:{ad}")
            result["verdict"] = "reject"
            break

    # Check homepage patterns
    for pat in HOMEPAGE_PATTERNS:
        if pat.match(url):
            result["issues"].append("homepage")
            result["verdict"] = "reject"
            break

    # Check path depth
    path = parsed.path.rstrip("/")
    segments = [s for s in path.split("/") if s]
    if len(segments) < MIN_PATH_DEPTH and result["verdict"] != "reject":
        result["issues"].append(f"shallow_path: depth={len(segments)}")
        result["verdict"] = "warn"

    return result


def main():
    urls = []
    if "--urls" in sys.argv:
        idx = sys.argv.index("--urls")
        urls = sys.argv[idx + 1:]
    elif len(sys.argv) > 1:
        path = sys.argv[1]
        if Path(path).suffix.lower() in (".html", ".htm"):
            urls = extract_urls_from_html(path)
        else:
            with open(path) as f:
                data = json.load(f)
                urls = data if isinstance(data, list) else list(data.keys())
    else:
        print("Usage: check_url_quality.py <dashboard.html> | --urls url1 url2 ... | <urls.json>", file=sys.stderr)
        sys.exit(2)

    results = [check_url(u) for u in urls]
    ok_count = sum(1 for r in results if r["verdict"] == "ok")
    warn_count = sum(1 for r in results if r["verdict"] == "warn")
    reject_count = sum(1 for r in results if r["verdict"] == "reject")

    report = {
        "total": len(results),
        "ok": ok_count,
        "warn": warn_count,
        "reject": reject_count,
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if reject_count > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
