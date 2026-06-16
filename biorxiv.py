"""
Fetch bioRxiv or medRxiv papers from the past 24 hours into SQLite.

Usage:
  python biorxiv.py --db biorxiv.sqlite
  python biorxiv.py --db biorxiv.sqlite --server biorxiv
  python biorxiv.py --db medrxiv.sqlite --server medrxiv
  python biorxiv.py --db biorxiv.sqlite --max 100

The bioRxiv/medRxiv details API is queried by date interval, then results are
filtered locally. The API metadata currently exposes article dates at day-level
precision, so date-only records are kept when their date falls within the UTC
query date interval.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError


API_URL_TEMPLATE = "https://api.biorxiv.org/details/{server}/{interval}/{cursor}"
PAGE_SIZE = 100
SUPPORTED_SERVERS = {"biorxiv", "medrxiv"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_space(value: Any) -> str:
    return " ".join(str(value or "").split())


def server_journal(server: str) -> str:
    if server == "medrxiv":
        return "medRxiv"
    return "bioRxiv"


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS biorxiv_articles (
            doi                         TEXT PRIMARY KEY,
            title                       TEXT,
            journal                     TEXT,
            pub_date                    TEXT,
            version                     TEXT,
            type                        TEXT,
            authors                     TEXT,
            abstract                    TEXT,
            category                    TEXT,
            server                      TEXT,
            url                         TEXT,
            pdf_url                     TEXT,
            fetched_at                  TEXT,
            raw_json                    TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_biorxiv_articles_pub_date
        ON biorxiv_articles(pub_date);
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_biorxiv_articles_server
        ON biorxiv_articles(server);
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_biorxiv_articles_category
        ON biorxiv_articles(category);
        """
    )
    conn.commit()
    return conn


def parse_authors(raw_authors: Any) -> list[str]:
    text = normalize_space(raw_authors)
    if not text:
        return []

    for separator in (";", "|"):
        if separator in text:
            authors = [normalize_space(part) for part in text.split(separator)]
            return [author for author in authors if author]

    if re.search(r"\s+and\s+", text, flags=re.IGNORECASE):
        authors = [
            normalize_space(part)
            for part in re.split(r"\s+and\s+", text, flags=re.IGNORECASE)
        ]
        if len(authors) > 1 and all(authors):
            return authors

    # Avoid splitting on commas because many API author strings use
    # "Last, First" or "Last, Initials" formatting.
    return [text]


def parse_api_datetime(value: Any) -> datetime | None:
    raw = normalize_space(value)
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0)


def parse_api_date(value: Any) -> date | None:
    raw = normalize_space(value)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def is_record_in_window(record: dict[str, Any], cutoff_utc: datetime, now_utc: datetime) -> bool:
    raw_date = record.get("date")
    dt = parse_api_datetime(raw_date)
    if dt is not None and ("T" in normalize_space(raw_date) or " " in normalize_space(raw_date)):
        return cutoff_utc <= dt <= now_utc

    article_date = parse_api_date(raw_date)
    if article_date is None:
        return False

    # The details endpoint generally provides date-level precision, not a
    # timestamp. Keep records within the interval and document the limitation
    # instead of inventing a time of day.
    return cutoff_utc.date() <= article_date <= now_utc.date()


def content_base_url(server: str) -> str:
    if server == "medrxiv":
        return "https://www.medrxiv.org/content"
    return "https://www.biorxiv.org/content"


def article_urls(server: str, doi: str, version: str) -> tuple[str, str]:
    if not doi or not version:
        return "", ""
    base = content_base_url(server)
    url = f"{base}/{doi}v{version}"
    return url, f"{url}.full.pdf"


def parse_record(record: dict[str, Any], server: str, fetched_at: str) -> dict[str, Any] | None:
    doi = normalize_space(record.get("doi"))
    if not doi:
        return None

    version = normalize_space(record.get("version"))
    url, pdf_url = article_urls(server, doi, version)
    record_server = normalize_space(record.get("server")).lower() or server
    if record_server not in SUPPORTED_SERVERS:
        record_server = server

    return {
        "doi": doi,
        "title": normalize_space(record.get("title")),
        "journal": server_journal(record_server),
        "pub_date": normalize_space(record.get("date")),
        "version": version,
        "type": normalize_space(record.get("type")),
        "authors": parse_authors(record.get("authors")),
        "abstract": normalize_space(record.get("abstract")),
        "category": normalize_space(record.get("category")),
        "server": record_server,
        "url": url,
        "pdf_url": pdf_url,
        "fetched_at": fetched_at,
        "raw_json": record,
    }


def insert_articles(conn: sqlite3.Connection, records: list[dict[str, Any]]) -> int:
    if not records:
        return 0

    cur = conn.cursor()
    inserted = 0
    for record in records:
        try:
            cur.execute(
                """
                INSERT OR IGNORE INTO biorxiv_articles
                (
                    doi, title, journal, pub_date, version, type, authors,
                    abstract, category, server, url, pdf_url, fetched_at, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("doi"),
                    record.get("title"),
                    record.get("journal"),
                    record.get("pub_date"),
                    record.get("version"),
                    record.get("type"),
                    json.dumps(record.get("authors", []), ensure_ascii=False),
                    record.get("abstract"),
                    record.get("category"),
                    record.get("server"),
                    record.get("url"),
                    record.get("pdf_url"),
                    record.get("fetched_at"),
                    json.dumps(record.get("raw_json", record), ensure_ascii=False),
                ),
            )
            if cur.rowcount == 1:
                inserted += 1
        except sqlite3.Error as exc:
            print(f"[error] insert failed for doi={record.get('doi')}: {exc}", file=sys.stderr)
    conn.commit()
    return inserted


def fetch_json_with_retries(url: str, max_retries: int, label: str) -> dict[str, Any]:
    headers = {"User-Agent": "biorxiv_last24h_to_sqlite/1.0 (Python urllib)"}
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = resp.read().decode("utf-8")
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise ValueError("API response was not a JSON object")
            return data
        except HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if retryable and attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(
                    f"[warn] {label} failed with HTTP {exc.code}: {exc.reason}. "
                    f"Retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})...",
                    file=sys.stderr,
                )
                time.sleep(wait_time)
                continue
            print(f"[error] {label} failed with HTTP {exc.code}: {exc.reason}", file=sys.stderr)
            raise
        except (URLError, TimeoutError, ConnectionError, OSError, json.JSONDecodeError, ValueError) as exc:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(
                    f"[warn] {label} error: {type(exc).__name__}: {exc}. "
                    f"Retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})...",
                    file=sys.stderr,
                )
                time.sleep(wait_time)
                continue
            print(
                f"[error] {label} failed after {max_retries} attempts: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            raise
    raise RuntimeError(f"{label} failed after {max_retries} attempts.")


def parse_total_count(data: dict[str, Any]) -> int | None:
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    first = messages[0]
    if not isinstance(first, dict):
        return None
    for key in ("total", "count"):
        raw = first.get(key)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None


def fetch_biorxiv_last_24h(
    *,
    server: str,
    start_from: int = 0,
    max_records: int = 0,
    sleep_seconds: float = 1.0,
    max_retries: int = 3,
) -> list[dict[str, Any]]:
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    cutoff_utc = now_utc - timedelta(hours=24)
    interval = f"{cutoff_utc.date().isoformat()}/{now_utc.date().isoformat()}"
    records: list[dict[str, Any]] = []
    total_fetched = 0
    cursor = max(start_from, 0)
    fetched_at = utc_now_iso()

    print(f"[info] server={server}")
    print(f"[info] query date interval={interval}")
    print(f"[info] UTC now={now_utc.isoformat()} cutoff={cutoff_utc.isoformat()}")

    while True:
        if max_records and total_fetched >= max_records:
            print(f"[info] Applying cap --max={max_records}; stopping fetch.")
            break

        url = API_URL_TEMPLATE.format(server=server, interval=interval, cursor=cursor)
        data = fetch_json_with_retries(
            url,
            max_retries=max_retries,
            label=f"{server} details API cursor={cursor}",
        )
        collection = data.get("collection")
        if not isinstance(collection, list):
            collection = []

        if max_records:
            remaining = max_records - total_fetched
            collection = collection[:remaining]

        page_records: list[dict[str, Any]] = []
        page_kept = 0
        for raw_record in collection:
            if not isinstance(raw_record, dict):
                continue
            total_fetched += 1
            if not is_record_in_window(raw_record, cutoff_utc, now_utc):
                continue
            parsed = parse_record(raw_record, server, fetched_at)
            if parsed is None:
                continue
            page_records.append(parsed)
            page_kept += 1

        records.extend(page_records)
        total_count = parse_total_count(data)
        print(
            f"[page] cursor={cursor} fetched={len(collection)} kept={page_kept} "
            f"total_fetched={total_fetched} total_kept={len(records)}"
        )
        print(
            "[progress] To resume from this point if interrupted, use: "
            f"--start-from {cursor + PAGE_SIZE}",
            file=sys.stderr,
        )

        if not collection:
            print(f"[info] No records returned at cursor={cursor}; stopping.")
            break
        if len(collection) < PAGE_SIZE:
            print("[info] Reached final partial page; stopping.")
            break
        if total_count is not None and cursor + PAGE_SIZE >= total_count:
            print("[info] Reached end of API result set; stopping.")
            break

        cursor += PAGE_SIZE
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite database path")
    parser.add_argument(
        "--server",
        choices=sorted(SUPPORTED_SERVERS),
        default="biorxiv",
        help="Preprint server to query (default: biorxiv)",
    )
    parser.add_argument("--sleep", type=float, default=1.0, help="Seconds to sleep between paginated requests")
    parser.add_argument("--max-retries", type=int, default=3, help="Number of retries for failed API requests")
    parser.add_argument("--start-from", type=int, default=0, help="Start cursor offset, useful for resuming")
    parser.add_argument("--max", type=int, default=0, help="Optional cap on API records processed (0 = no cap)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.sleep < 0:
        print("[error] --sleep must be >= 0", file=sys.stderr)
        return 1
    if args.max_retries <= 0:
        print("[error] --max-retries must be > 0", file=sys.stderr)
        return 1
    if args.start_from < 0:
        print("[error] --start-from must be >= 0", file=sys.stderr)
        return 1
    if args.max < 0:
        print("[error] --max must be >= 0", file=sys.stderr)
        return 1

    conn: sqlite3.Connection | None = None
    try:
        conn = init_db(args.db)
        records = fetch_biorxiv_last_24h(
            server=args.server,
            start_from=args.start_from,
            max_records=args.max,
            sleep_seconds=args.sleep,
            max_retries=args.max_retries,
        )
        inserted = insert_articles(conn, records)
        print(f"[insert] kept={len(records)} inserted={inserted}")
        print(f"[done] db={args.db} at={utc_now_iso()}")
        return 0
    except KeyboardInterrupt:
        print("[warn] Interrupted by user.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[error] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
