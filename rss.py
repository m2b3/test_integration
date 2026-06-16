"""
Fetch RSS articles from the past 24 hours into SQLite.

Usage:
  python rss.py -x nature --feed-url "https://www.nature.com/nature.rss"
  python rss.py -x nature --feed-url "https://www.nature.com/nature.rss" --max 100
  python rss.py -x nature --feed-url "https://www.nature.com/nature.rss" --refresh
"""

from __future__ import annotations
import argparse
import calendar
import hashlib
import json
import re
import sqlite3
import sys
import time
import urllib.request
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable
from urllib.error import HTTPError, URLError


DOI_RE = re.compile(r"(?:doi:\s*|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.IGNORECASE)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_source_slug(source: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", source.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-_")
    if not slug:
        raise ValueError("--source must contain at least one letter or digit")
    return slug


def default_db_path_from_source(source: str) -> str:
    return f"{safe_source_slug(source)}.sqlite"


def chunked(seq: list[str], n: int) -> Iterable[list[str]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rss_articles (
            rss_id              TEXT PRIMARY KEY,
            source              TEXT,
            title               TEXT,
            journal             TEXT,
            pub_date            TEXT,
            updated_date        TEXT,
            doi                 TEXT,
            authors             TEXT,
            abstract            TEXT,
            categories          TEXT,
            primary_category    TEXT,
            url                 TEXT,
            pdf_url             TEXT,
            feed_url            TEXT,
            fetched_at          TEXT,
            raw_json            TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_rss_articles_source
        ON rss_articles(source);
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_rss_articles_pub_date
        ON rss_articles(pub_date);
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_rss_articles_updated_date
        ON rss_articles(updated_date);
        """
    )
    conn.commit()
    return conn


def existing_rss_ids(conn: sqlite3.Connection, rss_ids: list[str]) -> set[str]:
    if not rss_ids:
        return set()
    existing: set[str] = set()
    for block in chunked(rss_ids, 800):
        qmarks = ",".join(["?"] * len(block))
        rows = conn.execute(
            f"SELECT rss_id FROM rss_articles WHERE rss_id IN ({qmarks})",
            block,
        ).fetchall()
        existing.update(str(row[0]) for row in rows)
    return existing


def insert_articles(conn: sqlite3.Connection, records: list[dict[str, Any]]) -> int:
    if not records:
        return 0

    cur = conn.cursor()
    inserted = 0
    for record in records:
        try:
            cur.execute(
                """
                INSERT OR IGNORE INTO rss_articles
                (
                    rss_id, source, title, journal, pub_date, updated_date,
                    doi, authors, abstract, categories, primary_category, url,
                    pdf_url, feed_url, fetched_at, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("rss_id"),
                    record.get("source"),
                    record.get("title"),
                    record.get("journal"),
                    record.get("pub_date"),
                    record.get("updated_date"),
                    record.get("doi"),
                    json.dumps(record.get("authors", []), ensure_ascii=False),
                    record.get("abstract"),
                    json.dumps(record.get("categories", []), ensure_ascii=False),
                    record.get("primary_category"),
                    record.get("url"),
                    record.get("pdf_url"),
                    record.get("feed_url"),
                    record.get("fetched_at"),
                    json.dumps(record.get("raw_json", record), ensure_ascii=False),
                ),
            )
            if cur.rowcount == 1:
                inserted += 1
        except sqlite3.Error as exc:
            print(f"[error] insert failed for rss_id={record.get('rss_id')}: {exc}", file=sys.stderr)
    conn.commit()
    return inserted


def delete_articles_by_ids(conn: sqlite3.Connection, rss_ids: list[str]) -> int:
    deleted = 0
    for block in chunked(unique_values(rss_ids), 800):
        qmarks = ",".join(["?"] * len(block))
        cur = conn.execute(f"DELETE FROM rss_articles WHERE rss_id IN ({qmarks})", block)
        deleted += cur.rowcount
    conn.commit()
    return deleted


def normalize_space(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def unique_values(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_date_value(value: Any, parsed_value: Any = None) -> datetime | None:
    if parsed_value:
        try:
            return datetime.fromtimestamp(calendar.timegm(parsed_value), tz=timezone.utc).replace(microsecond=0)
        except (OverflowError, TypeError, ValueError):
            pass

    raw = normalize_space(value)
    if not raw:
        return None

    iso_value = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(iso_value)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def normalize_authors(entry: Mapping[str, Any]) -> list[str]:
    authors: list[str] = []
    raw_authors = entry.get("authors")
    if isinstance(raw_authors, list):
        for author in raw_authors:
            if isinstance(author, Mapping):
                name = author.get("name") or author.get("email")
            else:
                name = author
            normalized = normalize_space(name)
            if normalized:
                authors.append(normalized)

    if not authors:
        raw_author = normalize_space(entry.get("author"))
        if raw_author:
            authors.append(raw_author)
    return unique_values(authors)


def normalize_categories(entry: Mapping[str, Any]) -> list[str]:
    categories: list[str] = []
    for key in ("tags", "categories"):
        raw_tags = entry.get(key)
        if not isinstance(raw_tags, list):
            continue
        for tag in raw_tags:
            if isinstance(tag, Mapping):
                value = tag.get("term") or tag.get("label") or tag.get("scheme")
            else:
                value = tag
            normalized = normalize_space(value)
            if normalized:
                categories.append(normalized)
    return unique_values(categories)


def normalize_doi(value: Any) -> str | None:
    raw = normalize_space(value)
    if not raw:
        return None
    match = DOI_RE.search(raw)
    if not match:
        return None
    return match.group(1).rstrip(".,;").strip()


def extract_doi(entry: Mapping[str, Any]) -> str | None:
    for key in ("doi", "prism_doi", "dc_identifier", "identifier"):
        doi = normalize_doi(entry.get(key))
        if doi:
            return doi

    candidates = [
        entry.get("id"),
        entry.get("guid"),
        entry.get("link"),
        entry.get("title"),
        entry.get("summary"),
        entry.get("description"),
    ]
    for candidate in candidates:
        doi = normalize_doi(candidate)
        if doi:
            return doi
    return None


def normalize_link_value(value: Any) -> str | None:
    if isinstance(value, Mapping):
        return normalize_space(value.get("href") or value.get("url"))
    return normalize_space(value)


def extract_entry_url(entry: Mapping[str, Any]) -> str | None:
    link = normalize_link_value(entry.get("link"))
    if link:
        return link

    raw_links = entry.get("links")
    if isinstance(raw_links, list):
        for raw_link in raw_links:
            if not isinstance(raw_link, Mapping):
                continue
            rel = str(raw_link.get("rel") or "").lower()
            href = normalize_link_value(raw_link)
            if href and rel in {"alternate", ""}:
                return href
    return None


def extract_pdf_url(entry: Mapping[str, Any]) -> str | None:
    raw_links = entry.get("links")
    if not isinstance(raw_links, list):
        return None

    for raw_link in raw_links:
        if not isinstance(raw_link, Mapping):
            continue
        href = normalize_link_value(raw_link)
        if not href:
            continue
        link_type = str(raw_link.get("type") or "").lower()
        rel = str(raw_link.get("rel") or "").lower()
        title = str(raw_link.get("title") or "").lower()
        if link_type == "application/pdf" or href.lower().endswith(".pdf") or "pdf" in title:
            return href
        if rel == "enclosure" and "pdf" in link_type:
            return href
    return None


def extract_journal(entry: Mapping[str, Any], feed_title: str | None) -> str | None:
    raw_source = entry.get("source")
    if isinstance(raw_source, Mapping):
        title = normalize_space(raw_source.get("title") or raw_source.get("name"))
        if title:
            return title
    return normalize_space(entry.get("prism_publicationname") or feed_title)


def build_rss_id(
    *,
    doi: str | None,
    entry: Mapping[str, Any],
    url: str | None,
    title: str | None,
    pub_date: str | None,
) -> str:
    if doi:
        return f"doi:{doi.lower()}"

    entry_id = normalize_space(entry.get("id") or entry.get("guid"))
    if entry_id:
        return f"id:{entry_id}"
    if url:
        return f"url:{url}"

    digest_input = "|".join((title or "", url or "", pub_date or ""))
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    return f"hash:{digest}"


def json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, time.struct_time):
        return list(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def parse_rss_entry(
    entry: Mapping[str, Any],
    source: str,
    feed_url: str,
    feed_title: str | None = None,
) -> dict[str, Any]:
    title = normalize_space(entry.get("title"))
    url = extract_entry_url(entry)
    pub_dt = parse_date_value(entry.get("published"), entry.get("published_parsed"))
    updated_dt = parse_date_value(entry.get("updated"), entry.get("updated_parsed"))
    pub_date = datetime_to_iso(pub_dt)
    updated_date = datetime_to_iso(updated_dt)
    doi = extract_doi(entry)
    categories = normalize_categories(entry)

    return {
        "rss_id": build_rss_id(doi=doi, entry=entry, url=url, title=title, pub_date=pub_date),
        "source": source,
        "title": title,
        "journal": extract_journal(entry, feed_title),
        "pub_date": pub_date,
        "updated_date": updated_date,
        "doi": doi,
        "authors": normalize_authors(entry),
        "abstract": normalize_space(entry.get("summary") or entry.get("description")),
        "categories": categories,
        "primary_category": categories[0] if categories else None,
        "url": url,
        "pdf_url": extract_pdf_url(entry),
        "feed_url": feed_url,
        "fetched_at": utc_now_iso(),
        "raw_json": json_safe(entry),
    }


def load_feedparser() -> Any:
    try:
        import feedparser
    except ImportError as exc:
        raise RuntimeError("Missing dependency: install with `pip install feedparser`.") from exc
    return feedparser


def fetch_rss_feed(feed_url: str, user_agent: str, timeout: float = 60.0) -> Any:
    feedparser = load_feedparser()
    req = urllib.request.Request(feed_url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
    except HTTPError as exc:
        preview = ""
        try:
            preview = " ".join(exc.read(500).decode("utf-8", errors="replace").split())
        except OSError:
            pass
        detail = f": {preview}" if preview else ""
        raise RuntimeError(f"RSS request failed with HTTP {exc.code} {exc.reason}{detail}") from exc
    except (URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise RuntimeError(f"RSS request failed: {type(exc).__name__}: {exc}") from exc

    feed = feedparser.parse(payload)
    entries = getattr(feed, "entries", [])
    if getattr(feed, "bozo", False):
        bozo_error = getattr(feed, "bozo_exception", "malformed feed")
        if not entries:
            raise RuntimeError(f"RSS feed could not be parsed: {bozo_error}")
        print(f"[warn] RSS feed parser reported a malformed feed: {bozo_error}", file=sys.stderr)
    if not isinstance(entries, list):
        raise RuntimeError("RSS feed parser returned malformed entries")
    return feed


def filter_last_24h(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    cutoff_utc = now_utc - timedelta(hours=24)
    kept: list[dict[str, Any]] = []

    for record in records:
        filter_dt = parse_date_value(record.get("updated_date"))
        if filter_dt is None:
            filter_dt = parse_date_value(record.get("pub_date"))
        if filter_dt is None:
            print(
                f"[warn] keeping rss_id={record.get('rss_id')} because published/updated dates are missing",
                file=sys.stderr,
            )
            kept.append(record)
            continue
        if filter_dt >= cutoff_utc:
            kept.append(record)
    return kept


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-x", "--source", required=True, help="Short source slug used for x.sqlite")
    ap.add_argument("--feed-url", required=True, help="RSS feed URL")
    ap.add_argument("--max", type=int, default=0, help="Optional cap on feed entries processed (0 = no cap)")
    ap.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep after fetching the feed")
    ap.add_argument("--refresh", action="store_true", help="Replace existing rows for fetched rss_ids")
    ap.add_argument("--no-date-filter", action="store_true", help="Store all parsed feed entries")
    ap.add_argument("--user-agent", default="rss-paper-ingester/0.1", help="HTTP User-Agent for RSS requests")
    args = ap.parse_args()

    if args.max < 0:
        print("[error] --max must be >= 0", file=sys.stderr)
        return 1
    if args.sleep < 0:
        print("[error] --sleep must be >= 0", file=sys.stderr)
        return 1

    conn: sqlite3.Connection | None = None
    try:
        source = safe_source_slug(args.source)
        db_path = default_db_path_from_source(source)
        print(f"[info] source={source} feed_url={args.feed_url} db_path={db_path}")
        feed = fetch_rss_feed(args.feed_url, args.user_agent)
        if args.sleep:
            time.sleep(args.sleep)

        entries = list(getattr(feed, "entries", []))
        if args.max:
            entries = entries[: args.max]
        feed_meta = getattr(feed, "feed", {})
        feed_title = normalize_space(feed_meta.get("title")) if isinstance(feed_meta, Mapping) else None
        print(f"[page] source={source} fetched_entries={len(entries)}")

        records: list[dict[str, Any]] = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                print(f"[warn] skipping malformed RSS entry at index={index}", file=sys.stderr)
                continue
            try:
                records.append(parse_rss_entry(entry, source, args.feed_url, feed_title))
            except Exception as exc:
                print(f"[warn] failed to parse RSS entry at index={index}: {exc}", file=sys.stderr)

        total_seen = len(entries)
        kept_records = records if args.no_date_filter else filter_last_24h(records)
        total_kept_last24h = len(kept_records)

        conn = init_db(db_path)
        rss_ids = [str(record["rss_id"]) for record in kept_records if record.get("rss_id")]
        existing = existing_rss_ids(conn, rss_ids)
        if args.refresh:
            deleted = delete_articles_by_ids(conn, rss_ids)
            new_records = kept_records
            print(f"[info] refresh deleted={deleted} rows before insert")
        else:
            new_records = [record for record in kept_records if record.get("rss_id") not in existing]

        total_inserted = insert_articles(conn, new_records)
        print(f"[insert] parsed={len(records)} new={len(new_records)} inserted={total_inserted}")
        print(
            f"[done] source={source} feed_url={args.feed_url} db_path={db_path} "
            f"total_seen={total_seen} total_kept_last24h={total_kept_last24h} "
            f"total_new={len(new_records)} total_inserted={total_inserted} "
            f"timestamp={utc_now_iso()}"
        )
        return 0
    except KeyboardInterrupt:
        print("[warn] Interrupted by user.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
