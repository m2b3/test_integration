import json
import os
import re
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
import feedparser
import requests
from bs4 import BeautifulSoup


OUP_RSS_PAGE_URL = "https://global.oup.com/academic/connect/rss/?cc=ca&lang=en&"
LOCAL_HTML_FILE = "oup_rss.html"

OUTPUT_ALL = "oup_feeds_all.json"
OUTPUT_ACTIVE = "oup_feeds_active_2025_2026.json"

ACTIVE_YEARS = {2025, 2026}
REQUEST_DELAY_SECONDS = 1.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; AcademicRSSCollector/1.0; "
        "+personal academic project)"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

REQUEST_TIMEOUT_SECONDS = 30
OUP_COOKIE = os.environ.get("OUP_COOKIE")


def clean_text(text: str) -> str:
    """Clean text."""
    if not text:
        return ""

    text = re.sub(r"\s+", " ", text).strip()
    return text


def looks_like_feed_url(url: str) -> bool:
    """Check whether a URL looks like an RSS or Atom feed."""
    if not url:
        return False

    lower = url.lower()

    keywords = [
        "rss",
        "feed",
        "atom",
        ".xml",
        "/feeds/",
        "/feed/",
    ]

    return any(k in lower for k in keywords)


def should_skip_url(url: str) -> bool:
    """Skip bad or irrelevant links."""
    if not url:
        return True

    parsed = urlparse(url)

    if not parsed.scheme.startswith("http"):
        return True

    domain = parsed.netloc.lower()

    skip_domains = [
        "facebook.com",
        "twitter.com",
        "x.com",
        "linkedin.com",
        "instagram.com",
        "youtube.com",
        "pinterest.com",
    ]

    if any(d in domain for d in skip_domains):
        return True

    return False


def normalize_oup_rss_url(url: str) -> str:
    """Normalize OUP RSS URLs to the form browsers commonly receive."""
    parsed = urlparse(url)

    if not parsed.netloc.lower().endswith("oup.com"):
        return url

    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    query = dict(query_items)

    if query.get("view", "").lower() != "rss":
        return url

    normalized_query = [
        ("cc", query.get("cc") or "ca"),
        ("lang", query.get("lang") or "en"),
        ("view", "RSS"),
    ]

    for key, value in query_items:
        if key not in {"cc", "lang", "view"}:
            normalized_query.append((key, value))

    return urlunparse(
        (
            "https",
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(normalized_query),
            "",
        )
    )


def looks_like_waf_challenge(response_text: str) -> bool:
    """Detect the AWS WAF JavaScript challenge OUP sometimes returns."""
    lower = response_text.lower()

    waf_markers = [
        "awswaf",
        "challenge.js",
        "verify that you're not a robot",
        "window.gokuprops",
    ]

    return any(marker in lower for marker in waf_markers)


def build_request_headers() -> dict[str, str]:
    """Build headers for OUP requests, optionally reusing a browser cookie."""
    headers = dict(HEADERS)

    if OUP_COOKIE:
        headers["Cookie"] = OUP_COOKIE

    return headers


def extract_title_near_link(a_tag) -> str:
    """Try to find a title near a feed link."""
    link_text = clean_text(a_tag.get_text(" ", strip=True))

    if link_text and len(link_text) > 2 and not looks_like_feed_url(link_text):
        return link_text

    parent = a_tag

    for _ in range(6):
        parent = parent.parent

        if parent is None:
            break

        for tag_name in ["h1", "h2", "h3", "h4", "strong", "b"]:
            tag = parent.find(tag_name)

            if tag:
                title = clean_text(tag.get_text(" ", strip=True))

                if title:
                    return title

    href = a_tag.get("href", "")
    return urlparse(href).netloc or "Unknown Title"


def scrape_oup_feed_links_from_local_html(
    source_page_url: str,
    local_html_file: str,
) -> list[dict]:
    """Read OUP RSS links from a local HTML file."""
    print(f"Reading local OUP RSS page:\n{local_html_file}\n")

    with open(local_html_file, "r", encoding="utf-8") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")

    results = []
    seen = set()

    # Method 1: collect visible RSS feed list links.
    rss_list_links = soup.select("ul.rss_feed_list a[href]")
    candidate_links = rss_list_links or soup.find_all("a", href=True)

    for a in candidate_links:
        href = normalize_oup_rss_url(urljoin(source_page_url, a["href"].strip()))

        if should_skip_url(href):
            continue

        if not looks_like_feed_url(href):
            continue

        if href in seen:
            continue

        seen.add(href)

        results.append(
            {
                "title": extract_title_near_link(a),
                "rss_url": href,
                "source": "oup_rss_page",
                "source_page": source_page_url,
                "discovery_method": "local_html_a_href",
            }
        )

    # Method 2: collect <link rel="alternate" type="application/rss+xml">
    for link in soup.find_all("link", href=True):
        link_type = link.get("type", "").lower()
        rel = " ".join(link.get("rel", [])).lower()

        if "rss" not in link_type and "atom" not in link_type and "alternate" not in rel:
            continue

        href = urljoin(source_page_url, link["href"].strip())

        if should_skip_url(href):
            continue

        if href in seen:
            continue

        seen.add(href)

        results.append(
            {
                "title": clean_text(link.get("title", "")) or "OUP Feed",
                "rss_url": href,
                "source": "oup_rss_page",
                "source_page": source_page_url,
                "discovery_method": "local_html_link_alternate",
            }
        )

    return results


def parse_entry_datetime(entry) -> datetime | None:
    """Parse date from one RSS entry."""
    time_fields = [
        "published_parsed",
        "updated_parsed",
        "created_parsed",
    ]

    for field in time_fields:
        value = entry.get(field)

        if value:
            try:
                return datetime(*value[:6])
            except Exception:
                pass

    string_fields = [
        "published",
        "updated",
        "created",
        "date",
    ]

    for field in string_fields:
        value = entry.get(field)

        if value:
            try:
                return parsedate_to_datetime(value).replace(tzinfo=None)
            except Exception:
                pass

    return None


def parse_feed_level_datetime(parsed_feed) -> datetime | None:
    """Parse date from feed-level metadata."""
    feed = parsed_feed.feed

    time_fields = [
        "updated_parsed",
        "published_parsed",
    ]

    for field in time_fields:
        value = feed.get(field)

        if value:
            try:
                return datetime(*value[:6])
            except Exception:
                pass

    string_fields = [
        "updated",
        "published",
        "date",
    ]

    for field in string_fields:
        value = feed.get(field)

        if value:
            try:
                return parsedate_to_datetime(value).replace(tzinfo=None)
            except Exception:
                pass

    return None


def check_one_feed(feed: dict) -> dict:
    """Check whether one feed works and is recently updated."""
    checked = dict(feed)

    checked["is_valid_feed"] = False
    checked["is_active_2025_2026"] = False
    checked["latest_item_date"] = None
    checked["latest_item_year"] = None
    checked["entries_count"] = 0
    checked["feed_title"] = None
    checked["check_error"] = None
    checked["http_status"] = None
    checked["final_url"] = None
    checked["content_type"] = None
    checked["response_bytes"] = 0

    rss_url = checked["rss_url"]

    try:
        response = requests.get(
            rss_url,
            headers=build_request_headers(),
            timeout=REQUEST_TIMEOUT_SECONDS,
            allow_redirects=True,
        )

        checked["http_status"] = response.status_code
        checked["final_url"] = response.url
        checked["content_type"] = response.headers.get("content-type")
        checked["response_bytes"] = len(response.content)

        if response.status_code >= 400:
            checked["check_error"] = f"HTTP {response.status_code}"
            return checked

        if not response.content:
            checked["check_error"] = (
                f"Empty response body. HTTP status={response.status_code}, "
                f"content_type={checked['content_type']}"
            )

            if response.status_code == 202 and not OUP_COOKIE:
                checked["check_error"] += (
                    " OUP likely requires a browser-validated WAF cookie; "
                    "set OUP_COOKIE to retry with your browser session."
                )

            return checked

        if looks_like_waf_challenge(response.text):
            checked["check_error"] = "OUP returned an AWS WAF JavaScript challenge, not RSS XML."

            if not OUP_COOKIE:
                checked["check_error"] += " Set OUP_COOKIE to retry with your browser session."

            return checked

        parsed = feedparser.parse(response.content)

        checked["feed_title"] = parsed.feed.get("title")
        checked["entries_count"] = len(parsed.entries)

        if parsed.bozo:
            checked["check_error"] = str(parsed.bozo_exception)

        if not parsed.feed and not parsed.entries:
            checked["check_error"] = checked["check_error"] or "No feed metadata and no entries found."
            return checked

        checked["is_valid_feed"] = True

        dates = []

        for entry in parsed.entries:
            dt = parse_entry_datetime(entry)

            if dt:
                dates.append(dt)

        if not dates:
            feed_dt = parse_feed_level_datetime(parsed)

            if feed_dt:
                dates.append(feed_dt)

        if not dates:
            checked["check_error"] = checked["check_error"] or "No parsable dates found."
            return checked

        latest_dt = max(dates)

        checked["latest_item_date"] = latest_dt.strftime("%Y-%m-%d")
        checked["latest_item_year"] = latest_dt.year

        if latest_dt.year in ACTIVE_YEARS:
            checked["is_active_2025_2026"] = True

        return checked

    except Exception as e:
        checked["check_error"] = str(e)
        return checked


def check_feeds(feeds: list[dict]) -> tuple[list[dict], list[dict]]:
    """Check all feeds and keep active feeds only."""
    all_checked = []
    active_feeds = []

    total = len(feeds)

    for i, feed in enumerate(feeds, start=1):
        print(f"[{i}/{total}] Checking: {feed['rss_url']}")

        checked = check_one_feed(feed)
        all_checked.append(checked)

        if checked["is_valid_feed"] and checked["is_active_2025_2026"]:
            active_feeds.append(checked)

            print(
                f"  KEEP: latest={checked['latest_item_date']}, "
                f"title={checked.get('feed_title') or checked.get('title')}"
            )
        else:
            print(
                f"  SKIP: latest={checked['latest_item_date']}, "
                f"error={checked.get('check_error')}"
            )

        time.sleep(REQUEST_DELAY_SECONDS)

    return all_checked, active_feeds


def save_json(data: list[dict], filepath: str) -> None:
    """Save data to JSON."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    feeds = scrape_oup_feed_links_from_local_html(
        source_page_url=OUP_RSS_PAGE_URL,
        local_html_file=LOCAL_HTML_FILE,
    )

    print(f"\nFound {len(feeds)} possible OUP feeds.\n")

    all_checked, active_feeds = check_feeds(feeds)

    save_json(all_checked, OUTPUT_ALL)
    save_json(active_feeds, OUTPUT_ACTIVE)

    print("\nDone.")
    print(f"All checked feeds saved to: {OUTPUT_ALL}")
    print(f"Active 2025/2026 feeds saved to: {OUTPUT_ACTIVE}")
    print(f"Total possible feeds: {len(feeds)}")
    print(f"Active 2025/2026 feeds kept: {len(active_feeds)}")

    print("\nPreview active feeds:")
    for item in active_feeds[:10]:
        print(
            f"- {item.get('feed_title') or item.get('title')} | "
            f"{item['rss_url']} | latest={item['latest_item_date']}"
        )


if __name__ == "__main__":
    main()
