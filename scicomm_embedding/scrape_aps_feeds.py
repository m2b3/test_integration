import json
import re
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse
import feedparser
import requests
from bs4 import BeautifulSoup

APS_FEEDS_URL = "https://journals.aps.org/feeds"

OUTPUT_ALL = "aps_feeds_all.json"
OUTPUT_ACTIVE = "aps_feeds_active_2025_2026.json"

ACTIVE_YEARS = {2025, 2026}
REQUEST_DELAY_SECONDS = 1.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; AcademicRSSCollector/1.0; "
        "+personal academic project)"
    )
}


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

    skip_domains = [
        "facebook.com",
        "twitter.com",
        "x.com",
        "linkedin.com",
        "youtube.com",
        "instagram.com",
    ]

    domain = parsed.netloc.lower()

    if any(d in domain for d in skip_domains):
        return True

    return False


def extract_title_near_link(a_tag) -> str:
    """Try to get a useful title near a feed link."""
    link_text = clean_text(a_tag.get_text(" ", strip=True))

    if link_text and len(link_text) > 2 and not looks_like_feed_url(link_text):
        return link_text

    parent = a_tag

    for _ in range(5):
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


def scrape_aps_feed_links(url: str, local_html: str = "aps_feeds.html") -> list[dict]:
    """Read APS feeds from a local HTML file."""
    print(f"Reading local APS feeds page:\n{local_html}\n")

    with open(local_html, "r", encoding="utf-8") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")

    results = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"].strip())

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
                "source": "aps_feeds_page",
                "source_page": url,
                "discovery_method": "local_html_a_href",
            }
        )

    for link in soup.find_all("link", href=True):
        link_type = link.get("type", "").lower()
        rel = " ".join(link.get("rel", [])).lower()

        if "rss" not in link_type and "atom" not in link_type and "alternate" not in rel:
            continue

        href = urljoin(url, link["href"].strip())

        if should_skip_url(href):
            continue

        if href in seen:
            continue

        seen.add(href)

        results.append(
            {
                "title": clean_text(link.get("title", "")) or "APS Feed",
                "rss_url": href,
                "source": "aps_feeds_page",
                "source_page": url,
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

    rss_url = checked["rss_url"]

    try:
        parsed = feedparser.parse(rss_url)

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
    feeds = scrape_aps_feed_links(APS_FEEDS_URL)

    print(f"\nFound {len(feeds)} possible APS feeds.\n")

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