"""
Fetch medRxiv preprints from the last 24 hours and store them into a SQLite
database with deduping by DOI.

- Uses the official medRxiv details API
- Dedupe = DOI primary key in SQLite
- Fetch window = yesterday/today UTC date interval, locally filtered when the
  API provides enough date precision
- Batches through details API cursor offsets

Usage:
  python medrxiv.py --db medrxiv.sqlite
  python medrxiv.py --db medrxiv.sqlite --max 500
  python medrxiv.py --db medrxiv.sqlite --sleep 1
  python medrxiv.py --db medrxiv.sqlite --days 1
  python medrxiv.py --db medrxiv.sqlite --start-date 2026-06-07 --end-date 2026-06-08

Notes:
  - medRxiv's details API is date-interval based and does not support arbitrary
    keyword search like the arXiv Atom API.
  - The details endpoint generally exposes article dates at day-level precision,
    not exact timestamps. Date-only records are kept when their date falls
    within the requested UTC date interval.
"""

from __future__ import annotations
import argparse
import json
import sqlite3
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError

MEDRXIV_API_URL_TEMPLATE = "https://api.medrxiv.org/details/medrxiv/{interval}/{cursor}/json"
MEDRXIV_SERVER = "medrxiv"
PAGE_SIZE = 100

@dataclass
class FetchStats:
    total_seen: int = 0
    total_kept_last24h: int = 0
    api_total_results: Optional[int] = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def chunked(seq: List[str], n: int) -> Iterable[List[str]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS medrxiv_articles (
            doi           TEXT PRIMARY KEY,
            title         TEXT,
            journal       TEXT,
            pub_date      TEXT,
            updated_date  TEXT,
            authors       TEXT,   -- JSON array of strings when normalized
            abstract      TEXT,
            category      TEXT,
            url           TEXT,
            pdf_url       TEXT,
            version       TEXT,
            type          TEXT,
            license       TEXT,
            server        TEXT,
            fetched_at    TEXT,
            raw_json      TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_medrxiv_articles_pub_date
        ON medrxiv_articles(pub_date);
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_medrxiv_articles_updated_date
        ON medrxiv_articles(updated_date);
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_medrxiv_articles_category
        ON medrxiv_articles(category);
        """
    )
    conn.commit()
    return conn


def existing_dois(conn: sqlite3.Connection, dois: List[str]) -> set[str]:
    if not dois:
        return set()
    out: set[str] = set()
    for block in chunked(dois, 800):
        qmarks = ",".join(["?"] * len(block))
        rows = conn.execute(f"SELECT doi FROM medrxiv_articles WHERE doi IN ({qmarks})", block).fetchall()
        out.update(r[0] for r in rows)
    return out


def insert_articles(conn: sqlite3.Connection, records: List[Dict[str, object]]) -> int:
    if not records:
        return 0
    cur = conn.cursor()
    inserted = 0
    for r in records:
        try:
            cur.execute(
                """
                INSERT OR IGNORE INTO medrxiv_articles
                (
                    doi, title, journal, pub_date, updated_date, authors,
                    abstract, category, url, pdf_url, version, type, license,
                    server, fetched_at, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    r.get("doi"),
                    r.get("title"),
                    r.get("journal"),
                    r.get("pub_date"),
                    r.get("updated_date"),
                    json.dumps(r.get("authors", []), ensure_ascii=False),
                    r.get("abstract"),
                    r.get("category"),
                    r.get("url"),
                    r.get("pdf_url"),
                    r.get("version"),
                    r.get("type"),
                    r.get("license"),
                    r.get("server"),
                    r.get("fetched_at"),
                    json.dumps(r.get("raw_json", r), ensure_ascii=False),
                ),
            )
            if cur.rowcount == 1:
                inserted += 1
        except sqlite3.Error as e:
            print(f"[error] insert failed for doi={r.get('doi')}: {e}", file=sys.stderr)
    conn.commit()
    return inserted


def normalize_space(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def normalize_authors(raw_authors: Any) -> List[str]:
    if raw_authors is None:
        return []
    if isinstance(raw_authors, list):
        authors = [normalize_space(author) for author in raw_authors]
        return [author for author in authors if author]

    text = normalize_space(raw_authors)
    if not text:
        return []

    for separator in (";", "|"):
        if separator in text:
            authors = [normalize_space(part) for part in text.split(separator)]
            return [author for author in authors if author]

    # Avoid splitting on commas because API author strings commonly use
    # "Last, First" formatting.
    return [text]


def parse_medrxiv_datetime(value: Any) -> Optional[datetime]:
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


def parse_medrxiv_date(value: Any) -> Optional[date]:
    raw = normalize_space(value)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def parse_cli_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{label} must be YYYY-MM-DD") from exc


def date_window_from_args(args: argparse.Namespace) -> Tuple[datetime, datetime, str]:
    if args.start_date or args.end_date:
        if not args.start_date or not args.end_date:
            raise ValueError("--start-date and --end-date must be supplied together")
        start_day = parse_cli_date(args.start_date, "--start-date")
        end_day = parse_cli_date(args.end_date, "--end-date")
        if end_day < start_day:
            raise ValueError("--end-date must be on or after --start-date")
        start_dt = datetime.combine(start_day, dt_time.min, tzinfo=timezone.utc)
        end_dt = datetime.combine(end_day, dt_time.max, tzinfo=timezone.utc).replace(microsecond=0)
        return start_dt, end_dt, "explicit date interval"

    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    start_dt = now_utc - timedelta(days=args.days)
    end_dt = now_utc
    return start_dt, end_dt, f"last {args.days} UTC day(s)"


def interval_for_window(start_dt: datetime, end_dt: datetime) -> str:
    return f"{start_dt.astimezone(timezone.utc).date().isoformat()}/{end_dt.astimezone(timezone.utc).date().isoformat()}"


def record_filter_datetime(record: Dict[str, Any]) -> Optional[datetime]:
    for key in ("version_date", "date", "published"):
        dt = parse_medrxiv_datetime(record.get(key))
        raw = normalize_space(record.get(key)) or ""
        if dt is not None and ("T" in raw or " " in raw):
            return dt
    return None


def record_filter_date(record: Dict[str, Any]) -> Optional[date]:
    for key in ("version_date", "date", "published"):
        parsed = parse_medrxiv_date(record.get(key))
        if parsed is not None:
            return parsed
    return None


def is_record_in_window(record: Dict[str, Any], start_dt: datetime, end_dt: datetime) -> bool:
    timestamp = record_filter_datetime(record)
    if timestamp is not None:
        return start_dt <= timestamp <= end_dt

    article_date = record_filter_date(record)
    if article_date is None:
        return True

    # medRxiv records usually provide YYYY-MM-DD dates only. With date-level
    # precision, keep records in the requested UTC date interval instead of
    # inventing a time of day for a strict 24-hour comparison.
    return start_dt.date() <= article_date <= end_dt.date()


def medrxiv_abs_url(doi: str) -> str:
    return f"https://www.medrxiv.org/content/{doi}"


def medrxiv_pdf_url(doi: str) -> str:
    return f"https://www.medrxiv.org/content/{doi}.full.pdf"


def parse_medrxiv_record(record: Dict[str, Any], fetched_at: str) -> Dict[str, object]:
    doi = normalize_space(record.get("doi"))
    if not doi:
        raise ValueError("missing DOI")

    pub_date = normalize_space(record.get("date"))
    updated_date = normalize_space(record.get("version_date")) or pub_date
    server = (normalize_space(record.get("server")) or MEDRXIV_SERVER).lower()
    if server != MEDRXIV_SERVER:
        server = MEDRXIV_SERVER

    return {
        "doi": doi,
        "title": normalize_space(record.get("title")),
        "journal": "medRxiv",
        "pub_date": pub_date,
        "updated_date": updated_date,
        "authors": normalize_authors(record.get("authors")),
        "abstract": normalize_space(record.get("abstract")),
        "category": normalize_space(record.get("category")),
        "url": medrxiv_abs_url(doi),
        "pdf_url": medrxiv_pdf_url(doi),
        "version": normalize_space(record.get("version")),
        "type": normalize_space(record.get("type")),
        "license": normalize_space(record.get("license")),
        "server": server,
        "fetched_at": fetched_at,
        "raw_json": record,
    }


def build_medrxiv_url(interval: str, cursor: int) -> str:
    return MEDRXIV_API_URL_TEMPLATE.format(interval=interval, cursor=cursor)


def fetch_url_with_retries(url: str, max_retries: int, label: str) -> bytes:
    headers = {"User-Agent": "medrxiv_last24h_to_sqlite/1.0 (Python urllib)"}
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except HTTPError as e:
            retryable = e.code == 429 or 500 <= e.code < 600
            if retryable and attempt < max_retries - 1:
                retry_after = e.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    wait_time = int(retry_after)
                elif e.code == 429:
                    wait_time = 10 * (2 ** attempt)
                else:
                    wait_time = 2 ** attempt
                print(
                    f"[warn] {label} failed with HTTP {e.code}: {e.reason}. "
                    f"Retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})...",
                    file=sys.stderr,
                )
                time.sleep(wait_time)
                continue
            print(f"[error] {label} failed with HTTP {e.code}: {e.reason}", file=sys.stderr)
            raise
        except (URLError, TimeoutError, ConnectionError, OSError) as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(
                    f"[warn] {label} connection error: {type(e).__name__}: {e}. "
                    f"Retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})...",
                    file=sys.stderr,
                )
                time.sleep(wait_time)
                continue
            print(f"[error] {label} failed after {max_retries} attempts: {type(e).__name__}: {e}", file=sys.stderr)
            raise
    raise RuntimeError(f"{label} failed after {max_retries} attempts.")


def parse_medrxiv_json(payload: bytes) -> Dict[str, Any]:
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        print(f"[error] JSON parse error: {e}", file=sys.stderr)
        raise
    if not isinstance(data, dict):
        raise ValueError("API response was not a JSON object")
    return data


def parse_api_messages(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    messages = data.get("messages")
    if not isinstance(messages, list):
        return []
    return [message for message in messages if isinstance(message, dict)]


def parse_total_results(data: Dict[str, Any]) -> Optional[int]:
    for message in parse_api_messages(data):
        for key in ("total", "count", "total_count", "count_new_papers"):
            raw = message.get(key)
            if raw is None:
                continue
            try:
                return int(raw)
            except (TypeError, ValueError):
                continue
    return None


def parse_api_errors(data: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for message in parse_api_messages(data):
        for key in ("error", "errors", "warning"):
            raw = message.get(key)
            text = normalize_space(raw)
            if text:
                errors.append(text)
    return errors


def fetch_medrxiv_last_24h(
    *,
    start_dt: datetime,
    end_dt: datetime,
    start_from: int = 0,
    max_records: int = 0,
    sleep_seconds: float = 1.0,
    max_retries: int = 3,
) -> Tuple[List[Dict[str, object]], FetchStats]:
    interval = interval_for_window(start_dt, end_dt)
    records: List[Dict[str, object]] = []
    stats = FetchStats()
    cursor = max(start_from, 0)
    fetched_at = utc_now_iso()

    print(f"[info] server={MEDRXIV_SERVER}")
    print(f"[info] date interval={interval}")
    print(f"[info] UTC window start={start_dt.isoformat()} end={end_dt.isoformat()}")
    print("[info] medRxiv date fields are usually date-level; date-only records are filtered by UTC date.")

    while True:
        if max_records and stats.total_seen >= max_records:
            print(f"[info] Applying cap --max={max_records}; stopping fetch.")
            break

        url = build_medrxiv_url(interval, cursor)
        label = f"medRxiv details API cursor={cursor}"
        payload = fetch_url_with_retries(url, max_retries=max_retries, label=label)
        data = parse_medrxiv_json(payload)

        errors = parse_api_errors(data)
        if errors:
            formatted = "; ".join(errors)
            print(f"[warn] API messages include warnings/errors: {formatted}", file=sys.stderr)

        if stats.api_total_results is None:
            stats.api_total_results = parse_total_results(data)

        collection = data.get("collection")
        if collection is None:
            collection = []
        if not isinstance(collection, list):
            raise ValueError("API response collection was not a JSON array")

        if max_records:
            remaining = max_records - stats.total_seen
            collection = collection[:remaining]

        page_records: List[Dict[str, object]] = []
        page_parse_errors = 0
        page_kept = 0

        for raw_record in collection:
            if not isinstance(raw_record, dict):
                page_parse_errors += 1
                print(f"[warn] Skipping non-object record at cursor={cursor}", file=sys.stderr)
                continue

            stats.total_seen += 1
            if not is_record_in_window(raw_record, start_dt, end_dt):
                continue

            try:
                record = parse_medrxiv_record(raw_record, fetched_at)
            except Exception as e:
                page_parse_errors += 1
                print(f"[warn] Failed to parse medRxiv record at cursor={cursor}: {e}", file=sys.stderr)
                continue

            page_records.append(record)
            page_kept += 1

        records.extend(page_records)
        stats.total_kept_last24h += page_kept

        next_cursor = cursor + len(collection)
        print(
            f"[page] cursor={cursor} fetched={len(collection)} kept_last24h={page_kept} "
            f"parse_errors={page_parse_errors} total_seen={stats.total_seen}"
        )
        print(f"[progress] To resume from this point if interrupted, use: --start-from {next_cursor}", file=sys.stderr)

        if not collection:
            print(f"[info] No records returned at cursor={cursor}; stopping.")
            break

        if max_records and stats.total_seen >= max_records:
            print(f"[info] Applying cap --max={max_records}; stopping fetch.")
            break

        if stats.api_total_results is not None and next_cursor >= stats.api_total_results:
            print("[info] Reached end of API result set; stopping.")
            break

        if len(collection) < PAGE_SIZE:
            print("[info] Reached final partial page; stopping.")
            break

        cursor = next_cursor or cursor + PAGE_SIZE
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return records, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="SQLite database path")
    ap.add_argument("--max", type=int, default=0, help="Optional cap on total API records processed (0 = no cap)")
    ap.add_argument("--sleep", type=float, default=1.0, help="Seconds to sleep between medRxiv API requests")
    ap.add_argument("--days", type=int, default=1, help="Number of UTC days back to fetch by date interval")
    ap.add_argument("--start-date", default="", help="Start date in YYYY-MM-DD UTC")
    ap.add_argument("--end-date", default="", help="End date in YYYY-MM-DD UTC")
    ap.add_argument("--start-from", type=int, default=0, help="Start cursor offset, useful for resuming")
    ap.add_argument("--max-retries", type=int, default=3, help="Number of retries for failed medRxiv API requests")
    args = ap.parse_args()

    if args.max < 0:
        print("[error] --max must be >= 0", file=sys.stderr)
        return 1
    if args.sleep < 0:
        print("[error] --sleep must be >= 0", file=sys.stderr)
        return 1
    if args.days <= 0:
        print("[error] --days must be > 0", file=sys.stderr)
        return 1
    if args.start_from < 0:
        print("[error] --start-from must be >= 0", file=sys.stderr)
        return 1
    if args.max_retries <= 0:
        print("[error] --max-retries must be > 0", file=sys.stderr)
        return 1

    conn: Optional[sqlite3.Connection] = None
    try:
        start_dt, end_dt, window_label = date_window_from_args(args)
        print(f"[info] window={window_label}")

        conn = init_db(args.db)
        records, stats = fetch_medrxiv_last_24h(
            start_dt=start_dt,
            end_dt=end_dt,
            start_from=args.start_from,
            max_records=args.max,
            sleep_seconds=args.sleep,
            max_retries=args.max_retries,
        )

        dois = [str(r["doi"]) for r in records if r.get("doi")]
        already = existing_dois(conn, dois)
        new_records = [r for r in records if r.get("doi") and r["doi"] not in already]
        total_inserted = insert_articles(conn, new_records)

        print(f"[insert] parsed={len(records)} new={len(new_records)} inserted={total_inserted}")
        print(
            f"[done] total_seen={stats.total_seen} "
            f"total_kept_last24h={stats.total_kept_last24h} "
            f"total_new={len(new_records)} total_inserted={total_inserted} "
            f"db={args.db} at={utc_now_iso()}"
        )
        return 0
    except KeyboardInterrupt:
        print("[warn] Interrupted by user.", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"[error] {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
