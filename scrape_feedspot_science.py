import json
import re
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
import feedparser
import requests
from bs4 import BeautifulSoup

FEEDSPOT_URL = "https://rss.feedspot.com/science_rss_feeds/#major-science-magazines-and-publications"

OUTPUT_ALL = "feedspot_science_all.json"
OUTPUT_ACTIVE = "feedspot_science_active_2025_2026.json"

# Keep feeds updated in these years only
ACTIVE_YEARS = {2025, 2026}

REQUEST_DELAY_SECONDS = 1.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; AcademicRSSCollector/1.0; "
        "+https://example.com/personal-academic-project)"
    )
}


def normalize_url(url: str) -> str:
    """
    Clean a URL string.
    """
    if not url:
        return ""

    url = url.strip()
    url = url.replace(" ", "%20")

    return url


def looks_like_feed_url(url: str) -> bool:
    """
    Check whether a URL looks like an RSS or Atom feed.
    """
    if not url:
        return False

    lower = url.lower()

    keywords = [
        "rss",
        "feed",
        "atom",
        ".xml",
        "feeds.",
        "/feeds/",
        "/feed/",
    ]

    return any(k in lower for k in keywords)


def should_skip_url(url: str) -> bool:
    """
    Skip links that should not be collected.
    """
    if not url:
        return True

    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    if not parsed.scheme.startswith("http"):
        return True

    # Skip Feedspot internal links
    if "feedspot.com" in domain:
        return True

    # Skip social media links
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


def clean_text(text: str) -> str:
    """
    Clean text from the page.
    """
    if not text:
        return ""

    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^\d+\.\s*", "", text)
    text = text.replace("RSS Feed", "").strip()
    text = text.replace("Follow RSS", "").strip()

    return text


def extract_title_near_link(a_tag) -> str:
    """
    Try to find a title near the RSS link.
    """
    link_text = clean_text(a_tag.get_text(" ", strip=True))

    # Do not use URL-like text as title
    if link_text and not looks_like_feed_url(link_text) and len(link_text) > 3:
        return link_text

    # Search parent blocks for headings
    parent = a_tag
    for _ in range(5):
        parent = parent.parent
        if parent is None:
            break

        for heading_name in ["h3", "h2", "h4"]:
            heading = parent.find(heading_name)
            if heading:
                title = clean_text(heading.get_text(" ", strip=True))
                if title:
                    return title

    # Use domain name as fallback title
    href = a_tag.get("href", "")
    domain = urlparse(href).netloc

    return domain or "Unknown Title"


def scrape_feedspot_public_feeds(url: str) -> list[dict]:
    """
    Scrape public RSS links from a Feedspot page.
    """
    print(f"Fetching Feedspot page:\n{url}\n")

    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    results = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = normalize_url(a["href"])

        if should_skip_url(href):
            continue

        if not looks_like_feed_url(href):
            continue

        if href in seen:
            continue

        seen.add(href)

        title = extract_title_near_link(a)

        results.append(
            {
                "title": title,
                "rss_url": href,
                "source": "feedspot_public_page",
                "source_page": url,
            }
        )

    return results


def parse_entry_datetime(entry) -> datetime | None:
    """
    Parse date from one RSS entry.
    """
    time_fields = [
        "published_parsed",
        "updated_parsed",
        "created_parsed",
        "expired_parsed",
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
    """
    Parse date from feed-level metadata.
    """
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
    """
    Check whether one RSS feed is valid and recently updated.
    """
    rss_url = feed["rss_url"]

    checked = dict(feed)
    checked["is_valid_feed"] = False
    checked["is_active_2025_2026"] = False
    checked["latest_item_date"] = None
    checked["latest_item_year"] = None
    checked["entries_count"] = 0
    checked["feed_title"] = None
    checked["check_error"] = None

    try:
        parsed = feedparser.parse(rss_url)

        checked["feed_title"] = parsed.feed.get("title")
        checked["entries_count"] = len(parsed.entries)

        if parsed.bozo:
            checked["check_error"] = str(parsed.bozo_exception)

        # No metadata and no entries means invalid feed
        if not parsed.feed and not parsed.entries:
            checked["check_error"] = checked["check_error"] or "No feed metadata and no entries found."
            return checked

        checked["is_valid_feed"] = True

        dates = []

        for entry in parsed.entries:
            dt = parse_entry_datetime(entry)
            if dt:
                dates.append(dt)

        # If entries have no dates, try feed-level date
        if not dates:
            feed_dt = parse_feed_level_datetime(parsed)
            if feed_dt:
                dates.append(feed_dt)

        if not dates:
            checked["check_error"] = checked["check_error"] or "No parsable published/updated dates found."
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
    """
    Check all feeds.

    Return:
    1. all_checked: all feed checking results
    2. active_feeds: feeds updated in 2025 or 2026
    """
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
    """
    Save data to a JSON file.
    """
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    feeds = scrape_feedspot_public_feeds(FEEDSPOT_URL)

    print(f"\nFound {len(feeds)} possible RSS feeds from Feedspot public page.\n")

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