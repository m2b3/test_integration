"""
Discover a journal RSS/Atom feed and ingest it with the rss.py SQLite schema.

Usage:
  python discover_journal_rss.py -journal "nature"
  python discover_journal_rss.py --journal "nature" --no-date-filter
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections.abc import Iterable, Mapping
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from rss import (
    default_db_path_from_source,
    delete_articles_by_ids,
    existing_rss_ids,
    fetch_rss_feed,
    filter_last_24h,
    init_db,
    insert_articles,
    parse_rss_entry,
    safe_source_slug,
    utc_now_iso,
)


CROSSREF_BASE_URL = "https://api.crossref.org"
FEED_TYPES = {
    "application/rss+xml",
    "application/atom+xml",
    "application/feed+json",
}

VENUE_HOMEPAGE_HINTS = {
    "acl": ["https://aclanthology.org/venues/acl/", "https://www.aclweb.org/"],
    "emnlp": ["https://aclanthology.org/venues/emnlp/"],
    "naacl": ["https://www.naacl.org/", "https://aclanthology.org/venues/naacl/"],
    "eacl": ["https://aclanthology.org/venues/eacl/", "https://eacl.org/"],
    "coling": ["https://aclanthology.org/venues/coling/"],
    "tacl": ["https://aclanthology.org/venues/tacl/", "https://transacl.org/"],
}


def normalize_space(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def normalize_title(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def unique_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def build_headers(user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "application/json, text/html, application/xhtml+xml, application/xml;q=0.9, */*;q=0.8",
    }


def crossref_get(
    session: requests.Session,
    endpoint: str,
    params: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    url = f"{CROSSREF_BASE_URL}{endpoint}"
    response = session.get(url, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Crossref returned a non-object JSON payload")
    return payload


def candidate_from_journal_item(item: Mapping[str, Any]) -> dict[str, Any] | None:
    title = normalize_space(item.get("title"))
    if not title:
        return None
    issns = item.get("ISSN") or item.get("issn") or []
    if not isinstance(issns, list):
        issns = [issns]
    return {
        "title": title,
        "publisher": normalize_space(item.get("publisher")),
        "issn": [str(issn) for issn in issns if issn],
        "metadata": dict(item),
        "source": "journals",
    }


def candidate_from_work_item(item: Mapping[str, Any]) -> dict[str, Any] | None:
    titles = item.get("container-title") or item.get("short-container-title") or []
    if isinstance(titles, str):
        titles = [titles]
    if not isinstance(titles, list) or not titles:
        return None
    title = normalize_space(titles[0])
    if not title:
        return None
    issns = item.get("ISSN") or []
    if not isinstance(issns, list):
        issns = [issns]
    return {
        "title": title,
        "publisher": normalize_space(item.get("publisher")),
        "issn": [str(issn) for issn in issns if issn],
        "metadata": dict(item),
        "source": "works",
    }


def crossref_message_items(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    message = payload.get("message")
    if not isinstance(message, Mapping):
        return []
    items = message.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, Mapping)]


def dedupe_candidates(candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for candidate in candidates:
        key = (
            normalize_title(candidate.get("title")),
            ",".join(candidate.get("issn") or []),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def has_exact_title_match(query: str, candidates: Iterable[dict[str, Any]]) -> bool:
    query_norm = normalize_title(query)
    return any(normalize_title(candidate.get("title")) == query_norm for candidate in candidates)


def query_crossref(
    journal: str,
    session: requests.Session,
    headers: dict[str, str],
    mailto: str | None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"query": journal, "rows": 5}
    if mailto:
        params["mailto"] = mailto

    print("[info] querying Crossref journals endpoint...")
    try:
        payload = crossref_get(session, "/journals", params, headers)
    except requests.RequestException as exc:
        print(f"[warn] Crossref journals endpoint failed: {exc}", file=sys.stderr)
        payload = {}

    candidates = [
        candidate
        for item in crossref_message_items(payload)
        if (candidate := candidate_from_journal_item(item)) is not None
    ]
    if candidates and has_exact_title_match(journal, candidates):
        return dedupe_candidates(candidates)
    if candidates:
        print("[info] no exact title match from journals endpoint; querying works fallback...")

    if not candidates:
        print("[info] querying Crossref works fallback...")
    fallback_params: dict[str, Any] = {"query.container-title": journal, "rows": 5}
    if mailto:
        fallback_params["mailto"] = mailto
    try:
        payload = crossref_get(session, "/works", fallback_params, headers)
    except requests.RequestException as exc:
        raise RuntimeError(f"Crossref lookup failed: {exc}") from exc

    fallback_candidates = [
        candidate
        for item in crossref_message_items(payload)
        if (candidate := candidate_from_work_item(item)) is not None
    ]
    return dedupe_candidates([*candidates, *fallback_candidates])


def print_crossref_candidates(candidates: list[dict[str, Any]]) -> None:
    for candidate in candidates:
        issn = ",".join(candidate.get("issn") or []) or "unknown"
        publisher = candidate.get("publisher") or "unknown"
        print(
            f"[crossref] candidate title={candidate.get('title')} "
            f"publisher={publisher} issn={issn}"
        )


def title_match_score(query: str, title: str | None) -> float:
    query_norm = normalize_title(query)
    title_norm = normalize_title(title)
    if not query_norm or not title_norm:
        return 0.0
    if query_norm == title_norm:
        return 1.0
    if query_norm in title_norm or title_norm in query_norm:
        return 0.88
    return SequenceMatcher(None, query_norm, title_norm).ratio()


def select_best_journal(query: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        raise RuntimeError("Crossref lookup returned no journal candidates")

    query_norm = normalize_title(query)
    for candidate in candidates:
        if normalize_title(candidate.get("title")) == query_norm:
            return candidate

    scored = [(title_match_score(query, candidate.get("title")), candidate) for candidate in candidates]
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_candidate = scored[0]
    if best_score < 0.45:
        print(
            f"[warn] selected low-confidence Crossref candidate with score={best_score:.2f}",
            file=sys.stderr,
        )
    return best_candidate


def extract_urls(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in {"url", "href", "link"}:
                urls.extend(extract_urls(item))
            elif isinstance(item, (Mapping, list, tuple)):
                urls.extend(extract_urls(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            urls.extend(extract_urls(item))
    elif isinstance(value, str):
        if value.startswith(("http://", "https://")):
            urls.append(value)
    return urls


def looks_like_homepage(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    lowered = url.lower()
    lowered_path = parsed.path.lower()
    blocked_hosts = ("api.crossref.org", "doi.org", "dx.doi.org", "portal.acm.org")
    if any(host in parsed.netloc.lower() for host in blocked_hosts):
        return False
    if parsed.netloc.lower().endswith("elsevier.com") and "/tdm/" in lowered_path:
        return False
    if parsed.netloc.lower() == "linkinghub.elsevier.com" and "/retrieve/pii/" in lowered_path:
        return False
    if lowered_path.endswith(".pdf") or "/articles/" in lowered_path or "/article/" in lowered_path:
        return False
    if parsed.netloc.lower().endswith("acm.org") and "citation.cfm" in lowered:
        return False
    if lowered_path.rstrip("/") in {"/tdm", "/rightslink"}:
        return False
    return not looks_like_feed_url(lowered)


def looks_like_feed_url(url: str) -> bool:
    lowered = url.lower()
    return lowered.endswith((".rss", ".atom", ".xml", ".json")) or "rss" in lowered or "atom" in lowered


def nature_slug_candidates(query: str, selected: Mapping[str, Any]) -> list[str]:
    title = str(selected.get("title") or query)
    slug = safe_source_slug(title)
    query_slug = safe_source_slug(query)
    candidates = [query_slug, slug]
    if normalize_title(title) == "nature" or normalize_title(query) == "nature":
        candidates.insert(0, "nature")
    return unique_preserve_order(candidates)


def issn_digits(issn: str) -> str:
    return re.sub(r"[^0-9Xx]+", "", issn)


def title_url_slug(title: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", title.lower())).strip("-")


def should_try_elsevier_candidates(selected: Mapping[str, Any]) -> bool:
    text = " ".join(
        str(part or "")
        for part in (
            selected.get("title"),
            selected.get("publisher"),
        )
    ).lower()
    return "elsevier" in text or "sciencedirect" in text


def elsevier_candidate_urls(selected: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    title = normalize_space(selected.get("title"))
    homepage_candidates: list[str] = []
    direct_feed_candidates: list[str] = []

    if title:
        homepage_candidates.append(f"https://www.sciencedirect.com/journal/{title_url_slug(title)}")

    for issn in selected.get("issn") or []:
        digits = issn_digits(str(issn))
        if digits:
            direct_feed_candidates.append(f"https://rss.sciencedirect.com/publication/science/{digits}")

    return homepage_candidates, direct_feed_candidates


def should_try_nature_candidates(query: str, selected: Mapping[str, Any]) -> bool:
    text = " ".join(
        str(part or "")
        for part in (
            query,
            selected.get("title"),
            selected.get("publisher"),
        )
    ).lower()
    return any(marker in text for marker in ("nature", "springer"))


def venue_homepage_hints(query: str, selected: Mapping[str, Any]) -> list[str]:
    candidates = [query, str(selected.get("title") or "")]
    urls: list[str] = []
    for candidate in candidates:
        tokens = normalize_title(candidate).split()
        for token in tokens:
            urls.extend(VENUE_HOMEPAGE_HINTS.get(token, []))
    return unique_preserve_order(urls)


def build_candidate_urls(
    query: str,
    selected: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    homepage_candidates: list[str] = []
    direct_feed_candidates: list[str] = []

    for url in extract_urls(selected.get("metadata")):
        if looks_like_feed_url(url):
            direct_feed_candidates.append(url)
        elif looks_like_homepage(url):
            homepage_candidates.append(url)

    if should_try_nature_candidates(query, selected):
        for slug in nature_slug_candidates(query, selected):
            homepage_candidates.append(f"https://www.nature.com/{slug}/")
            direct_feed_candidates.append(f"https://www.nature.com/{slug}.rss")

    if should_try_elsevier_candidates(selected):
        elsevier_homepages, elsevier_feeds = elsevier_candidate_urls(selected)
        homepage_candidates.extend(elsevier_homepages)
        direct_feed_candidates.extend(elsevier_feeds)

    homepage_candidates.extend(venue_homepage_hints(query, selected))

    return unique_preserve_order(homepage_candidates), unique_preserve_order(direct_feed_candidates)


def fetch_homepage(
    homepage_url: str,
    session: requests.Session,
    headers: dict[str, str],
) -> requests.Response | None:
    try:
        response = session.get(homepage_url, headers=headers, timeout=30)
    except requests.RequestException as exc:
        print(f"[homepage] candidate={homepage_url}")
        print(f"[homepage] error={exc}")
        return None

    content_type = response.headers.get("content-type", "")
    print(f"[homepage] candidate={homepage_url}")
    print(f"[homepage] status={response.status_code} content_type={content_type or 'unknown'}")
    if response.status_code >= 400:
        return None
    if "html" not in content_type.lower() and not response.text.lstrip().lower().startswith(("<!doctype html", "<html")):
        return None
    return response


def discover_feed_urls(
    homepage_url: str,
    session: requests.Session,
    headers: dict[str, str],
) -> list[str]:
    print(f"[discover] homepage={homepage_url}")
    response = session.get(homepage_url, headers=headers, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    feed_urls: list[str] = []
    for link in soup.find_all("link"):
        rel_values = link.get("rel") or []
        if isinstance(rel_values, str):
            rel_values = rel_values.split()
        rels = {str(rel).lower() for rel in rel_values}
        link_type = str(link.get("type") or "").lower().strip()
        href = link.get("href")
        if "alternate" not in rels or link_type not in FEED_TYPES or not href:
            continue
        feed_url = urljoin(homepage_url, str(href))
        feed_urls.append(feed_url)
        print(f"[discover] found_feed={feed_url}")

    return unique_preserve_order(feed_urls)


def validate_feed(feed_url: str, user_agent: str) -> dict[str, Any]:
    print(f"[validate] checking_feed={feed_url}")
    try:
        feed = fetch_rss_feed(feed_url, user_agent)
        entries = list(getattr(feed, "entries", []))
        feed_meta = getattr(feed, "feed", {})
        feed_title = None
        if isinstance(feed_meta, Mapping):
            feed_title = normalize_space(feed_meta.get("title"))
        sample_title = None
        if entries and isinstance(entries[0], Mapping):
            sample_title = normalize_space(entries[0].get("title"))
        result = {
            "status": "ok" if entries else "bad",
            "feed_url": feed_url,
            "feed_title": feed_title,
            "entries_count": len(entries),
            "sample_title": sample_title,
            "error": None if entries else "feed has no entries",
        }
    except Exception as exc:
        result = {
            "status": "bad",
            "feed_url": feed_url,
            "feed_title": None,
            "entries_count": 0,
            "sample_title": None,
            "error": str(exc),
        }

    if result["status"] == "ok":
        print(
            f"[validate] ok feed_title={result['feed_title'] or 'unknown'} "
            f"entries={result['entries_count']} sample_title={result['sample_title'] or 'unknown'}"
        )
    else:
        print(f"[validate] bad error={result['error']}")
    return result


def choose_feed(valid_feeds: list[dict[str, Any]], source_slug: str) -> dict[str, Any]:
    if not valid_feeds:
        raise RuntimeError("no valid RSS/Atom feeds found")
    for feed in valid_feeds:
        print(
            f"[validate] valid_feed={feed['feed_url']} "
            f"title={feed.get('feed_title') or 'unknown'} entries={feed.get('entries_count', 0)}"
        )

    slug = source_slug.lower()
    matching = [feed for feed in valid_feeds if slug in str(feed.get("feed_url", "")).lower()]
    return matching[0] if matching else valid_feeds[0]


def ingest_feed(
    *,
    source: str,
    feed_url: str,
    user_agent: str,
    max_entries: int,
    refresh: bool,
    no_date_filter: bool,
) -> tuple[str, int, int, int, int]:
    db_path = default_db_path_from_source(source)
    feed = fetch_rss_feed(feed_url, user_agent)
    entries = list(getattr(feed, "entries", []))
    if max_entries:
        entries = entries[:max_entries]

    feed_meta = getattr(feed, "feed", {})
    feed_title = normalize_space(feed_meta.get("title")) if isinstance(feed_meta, Mapping) else None
    print(f"[page] source={source} fetched_entries={len(entries)}")

    records: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            print(f"[warn] skipping malformed RSS entry at index={index}", file=sys.stderr)
            continue
        try:
            records.append(parse_rss_entry(entry, source, feed_url, feed_title))
        except Exception as exc:
            print(f"[warn] failed to parse RSS entry at index={index}: {exc}", file=sys.stderr)

    kept_records = records if no_date_filter else filter_last_24h(records)
    if no_date_filter:
        print(f"[filter] date_filter=disabled kept={len(kept_records)}")
    else:
        print(f"[filter] kept_last24h={len(kept_records)}")

    conn: sqlite3.Connection | None = None
    try:
        conn = init_db(db_path)
        rss_ids = [str(record["rss_id"]) for record in kept_records if record.get("rss_id")]
        existing = existing_rss_ids(conn, rss_ids)
        if refresh:
            deleted = delete_articles_by_ids(conn, rss_ids)
            new_records = kept_records
            print(f"[info] refresh deleted={deleted} rows before insert")
        else:
            new_records = [record for record in kept_records if record.get("rss_id") not in existing]
        inserted = insert_articles(conn, new_records)
    finally:
        if conn is not None:
            conn.close()

    print(f"[insert] parsed={len(records)} new={len(new_records)} inserted={inserted}")
    return db_path, len(entries), len(records), len(new_records), inserted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("-journal", "--journal", required=True, help="Journal name to discover")
    parser.add_argument("--max", type=int, default=0, help="Max feed entries to process (0 = no cap)")
    parser.add_argument("--refresh", action="store_true", help="Replace existing rows for fetched rss_ids")
    parser.add_argument("--no-date-filter", action="store_true", help="Store all feed entries")
    parser.add_argument("--mailto", help="Email address for Crossref polite pool")
    parser.add_argument("--user-agent", default="journal-rss-discoverer/0.1", help="HTTP User-Agent")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max < 0:
        print("[error] --max must be >= 0", file=sys.stderr)
        return 1

    source = safe_source_slug(args.journal)
    headers = build_headers(args.user_agent)
    print(f"[info] journal_query={args.journal}")

    try:
        with requests.Session() as session:
            candidates = query_crossref(args.journal, session, headers, args.mailto)
            if not candidates:
                print("[error] Crossref lookup returned no useful results", file=sys.stderr)
                return 1

            print_crossref_candidates(candidates)
            selected = select_best_journal(args.journal, candidates)
            print(f"[info] selected_journal={selected.get('title')}")

            homepage_candidates, direct_feed_candidates = build_candidate_urls(args.journal, selected)
            if not homepage_candidates:
                print("[error] no homepage candidates could be inferred", file=sys.stderr)
                return 1

            feed_candidates: list[str] = []
            working_homepages: list[str] = []
            attempted_homepages: list[str] = []
            for homepage_url in homepage_candidates:
                attempted_homepages.append(homepage_url)
                response = fetch_homepage(homepage_url, session, headers)
                if response is None:
                    continue
                working_homepages.append(homepage_url)
                try:
                    feed_candidates.extend(discover_feed_urls(homepage_url, session, headers))
                except requests.RequestException as exc:
                    print(f"[warn] RSS autodiscovery failed for {homepage_url}: {exc}", file=sys.stderr)

            if not working_homepages:
                if direct_feed_candidates:
                    print("[warn] no homepage candidates worked; trying direct feed candidates...", file=sys.stderr)
                    for homepage_url in attempted_homepages:
                        print(f"[warn] attempted_homepage={homepage_url}", file=sys.stderr)
                else:
                    print("[error] no homepage candidates worked", file=sys.stderr)
                    for homepage_url in attempted_homepages:
                        print(f"[error] attempted_homepage={homepage_url}", file=sys.stderr)
                    return 1

            feed_candidates = unique_preserve_order(feed_candidates)
            if not feed_candidates:
                print("[info] RSS autodiscovery found no feeds; trying direct feed candidates...")
                feed_candidates = unique_preserve_order(direct_feed_candidates)
            else:
                for feed_url in direct_feed_candidates:
                    if feed_url not in feed_candidates:
                        feed_candidates.append(feed_url)

            if not feed_candidates:
                print("[error] no RSS/Atom feed candidates could be inferred", file=sys.stderr)
                return 1

            validation_results = [validate_feed(feed_url, args.user_agent) for feed_url in feed_candidates]
            valid_feeds = [result for result in validation_results if result.get("status") == "ok"]
            if not valid_feeds:
                print("[error] no valid feed worked", file=sys.stderr)
                for result in validation_results:
                    print(
                        f"[error] attempted_feed={result['feed_url']} error={result.get('error')}",
                        file=sys.stderr,
                    )
                return 1

            selected_feed = choose_feed(valid_feeds, source)
            selected_feed_url = str(selected_feed["feed_url"])
            print(f"[fetch] selected_feed_url={selected_feed_url}")
            db_path, _total_seen, _parsed, _new, _inserted = ingest_feed(
                source=source,
                feed_url=selected_feed_url,
                user_agent=args.user_agent,
                max_entries=args.max,
                refresh=args.refresh,
                no_date_filter=args.no_date_filter,
            )
            print(
                f"[done] source={source} db_path={db_path} "
                f"feed_url={selected_feed_url} timestamp={utc_now_iso()}"
            )
            return 0
    except KeyboardInterrupt:
        print("[warn] Interrupted by user.", file=sys.stderr)
        return 130
    except requests.RequestException as exc:
        print(f"[error] network request failed: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
