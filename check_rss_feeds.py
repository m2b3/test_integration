import json
import os
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser
import requests


INPUT = Path("rss_feeds_raw.json")
OUTPUT = Path("rss_feeds_checked.json")
SUMMARY_OUTPUT = Path("rss_feeds_checked_summary.json")
BLOCKED_OUTPUT = Path("rss_feeds_blocked.json")

ACTIVE_YEARS = {2025, 2026}
REQUEST_DELAY_SECONDS = 0.5
REQUEST_TIMEOUT_SECONDS = 20
RSS_COOKIE = os.environ.get("RSS_COOKIE")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; RSSChecker/1.0; personal research)",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


def build_request_headers():
    headers = dict(HEADERS)

    if RSS_COOKIE:
        headers["Cookie"] = RSS_COOKIE

    return headers


def classify_block_response(response):
    content_type = response.headers.get("content-type") or ""

    if "html" not in content_type.lower():
        return None

    text = response.text.lower()

    cloudflare_markers = [
        "just a moment",
        "cloudflare",
        "cf-browser-verification",
        "cf-chl",
        "checking your browser",
    ]

    waf_markers = [
        "awswaf",
        "aws waf",
        "challenge.js",
        "verify that you're not a robot",
        "window.gokuprops",
    ]

    if any(marker in text for marker in cloudflare_markers):
        return "cloudflare_blocked"

    if any(marker in text for marker in waf_markers):
        return "waf_blocked"

    return None


def parse_datetime(value):
    if not value:
        return None

    try:
        return parsedate_to_datetime(value).replace(tzinfo=None)
    except Exception:
        pass

    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=None)
        except Exception:
            pass

    return None


def latest_entry_datetime(parsed_feed):
    dates = []

    for entry in parsed_feed.entries:
        for field in ("published", "updated", "created", "date"):
            dt = parse_datetime(entry.get(field))

            if dt:
                dates.append(dt)
                break

    return max(dates) if dates else None


def check_feed(item):
    result = dict(item)
    result.update(
        {
            "is_working": False,
            "is_active_2025_2026": False,
            "status": "unchecked",
            "http_status": None,
            "final_url": None,
            "content_type": None,
            "response_bytes": 0,
            "entries_count": 0,
            "feed_title": None,
            "latest_item_date": None,
            "latest_item_year": None,
            "check_error": None,
        }
    )

    try:
        response = requests.get(
            item["rss_url"],
            headers=build_request_headers(),
            timeout=REQUEST_TIMEOUT_SECONDS,
            allow_redirects=True,
        )

        result["http_status"] = response.status_code
        result["final_url"] = response.url
        result["content_type"] = response.headers.get("content-type")
        result["response_bytes"] = len(response.content)

        blocked_status = classify_block_response(response)

        if blocked_status:
            result["status"] = blocked_status
            result["check_error"] = (
                "Received an HTML challenge page instead of RSS XML"
            )
            return result

        if response.status_code >= 400:
            result["status"] = "http_error"
            result["check_error"] = f"HTTP {response.status_code}"
            return result

        if not response.content:
            result["status"] = "empty_response"
            result["check_error"] = "Empty response body"
            return result

        parsed = feedparser.parse(response.content)

        result["feed_title"] = parsed.feed.get("title")
        result["entries_count"] = len(parsed.entries)

        if parsed.bozo:
            result["check_error"] = str(parsed.bozo_exception)

        if not parsed.feed and not parsed.entries:
            result["status"] = "parse_error"
            result["check_error"] = result["check_error"] or "No feed metadata or entries"
            return result

        result["is_working"] = True
        result["status"] = "working"

        latest_dt = latest_entry_datetime(parsed)

        if latest_dt:
            result["latest_item_date"] = latest_dt.strftime("%Y-%m-%d")
            result["latest_item_year"] = latest_dt.year
            result["is_active_2025_2026"] = latest_dt.year in ACTIVE_YEARS

        return result

    except Exception as exc:
        result["status"] = "request_error"
        result["check_error"] = str(exc)
        return result


def build_summary(checked_feeds):
    total = len(checked_feeds)
    working = [item for item in checked_feeds if item["is_working"]]
    active = [item for item in checked_feeds if item["is_active_2025_2026"]]
    status_counts = {}

    for item in checked_feeds:
        status = item.get("status") or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "total_feeds": total,
        "working_feeds": len(working),
        "failed_feeds": total - len(working),
        "active_2025_2026_feeds": len(active),
        "blocked_feeds": sum(
            status_counts.get(status, 0)
            for status in ("cloudflare_blocked", "waf_blocked")
        ),
        "working_without_parsable_date": sum(
            1 for item in working if item["latest_item_year"] is None
        ),
        "active_years": sorted(ACTIVE_YEARS),
        "status_counts": status_counts,
    }


def main():
    with INPUT.open(encoding="utf-8") as f:
        feeds = json.load(f)

    checked = []
    total = len(feeds)

    for index, item in enumerate(feeds, start=1):
        print(f"[{index}/{total}] {item['rss_url']}")

        checked_item = check_feed(item)
        checked.append(checked_item)

        status = checked_item["status"].upper()
        latest = checked_item["latest_item_date"]
        error = checked_item["check_error"]

        print(f"  {status}: latest={latest}, error={error}")

        time.sleep(REQUEST_DELAY_SECONDS)

    summary = build_summary(checked)
    blocked = [
        item
        for item in checked
        if item["status"] in {"cloudflare_blocked", "waf_blocked"}
    ]

    with OUTPUT.open("w", encoding="utf-8") as f:
        json.dump(checked, f, ensure_ascii=False, indent=2)

    with SUMMARY_OUTPUT.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    with BLOCKED_OUTPUT.open("w", encoding="utf-8") as f:
        json.dump(blocked, f, ensure_ascii=False, indent=2)

    print("\nDone.")
    print(f"Checked feeds saved to: {OUTPUT}")
    print(f"Summary saved to: {SUMMARY_OUTPUT}")
    print(f"Blocked feeds saved to: {BLOCKED_OUTPUT}")
    print(f"Total RSS URLs: {summary['total_feeds']}")
    print(f"Working RSS URLs: {summary['working_feeds']}")
    print(f"Failed RSS URLs: {summary['failed_feeds']}")
    print(f"Cloudflare/WAF blocked RSS URLs: {summary['blocked_feeds']}")
    print(f"Latest item in 2025/2026: {summary['active_2025_2026_feeds']}")
    print(
        "Working but no parsable item date: "
        f"{summary['working_without_parsable_date']}"
    )
    print(f"Status counts: {summary['status_counts']}")


if __name__ == "__main__":
    main()
