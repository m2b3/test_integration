"""Collect recently public OpenReview papers from active venues into SQLite."""
from __future__ import annotations
import argparse
import datetime as dt
import importlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

OPENREVIEW_API2_URL = "https://api2.openreview.net"
DEFAULT_DB_PATH = "openreview.sqlite"
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}

def import_openreview() -> Any:
    """Import the installed openreview-py package, avoiding local file shadowing."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    original_path = list(sys.path)
    original_module = sys.modules.get("openreview")
    local_openreview = os.path.join(script_dir, "openreview.py")

    if getattr(original_module, "__file__", None) == local_openreview:
        sys.modules.pop("openreview", None)

    try:
        sys.path = [
            path
            for path in sys.path
            if path not in ("", script_dir) and os.path.abspath(path or os.curdir) != script_dir
        ]
        return importlib.import_module("openreview")
    except ImportError as exc:
        raise RuntimeError("Missing dependency: install with `pip install openreview-py`.") from exc
    finally:
        sys.path = original_path
        if original_module is not None and "openreview" not in sys.modules:
            sys.modules["openreview"] = original_module


def create_client() -> Any:
    """Create an OpenReview API v2 client with optional environment auth."""
    openreview = import_openreview()
    username = os.environ.get("OPENREVIEW_USERNAME")
    password = os.environ.get("OPENREVIEW_PASSWORD")

    kwargs: dict[str, Any] = {"baseurl": OPENREVIEW_API2_URL}
    if username and password:
        kwargs.update({"username": username, "password": password})

    try:
        return openreview.api.OpenReviewClient(**kwargs)
    except AttributeError as exc:
        raise RuntimeError(
            "Installed openreview-py does not expose openreview.api.OpenReviewClient. "
            "Upgrade with `pip install -U openreview-py`."
        ) from exc


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS papers (
            id TEXT PRIMARY KEY,
            forum TEXT,
            venue_id TEXT,
            title TEXT,
            abstract TEXT,
            authors TEXT,
            keywords TEXT,
            pdf_url TEXT,
            html_url TEXT,
            odate INTEGER,
            tcdate INTEGER,
            tmdate INTEGER,
            pdate INTEGER,
            source TEXT,
            raw_json TEXT,
            fetched_at TEXT
        )
        """
    )
    conn.commit()
    return conn


def exception_status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        return status_code

    status_code = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status_code, int):
        return status_code

    text = str(exc)
    for code in RETRY_STATUS_CODES:
        if str(code) in text:
            return code
    return None


def retry_api_call(
    func: Callable[[], Any],
    *,
    attempts: int = 4,
    initial_delay: float = 1.0,
    verbose: bool = False,
) -> Any:
    delay = initial_delay
    last_exc: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:
            last_exc = exc
            status_code = exception_status_code(exc)
            retryable = status_code in RETRY_STATUS_CODES or status_code is None
            if attempt >= attempts or not retryable:
                raise
            if verbose:
                status = f"HTTP {status_code}" if status_code else "temporary API error"
                print(f"  Retry {attempt}/{attempts - 1} after {status}: {exc}")
            time.sleep(delay)
            delay *= 2

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("API call failed without an exception.")


def get_active_venues(client: Any, *, verbose: bool = False) -> list[str]:
    group = retry_api_call(lambda: client.get_group("active_venues"), verbose=verbose)
    members = getattr(group, "members", None) or []
    return [str(member) for member in members if str(member).strip()]


def get_content_value(note: Any, key: str, default: Any = None) -> Any:
    """Read OpenReview v2 content[key]['value'], with fallbacks for older formats."""
    content = getattr(note, "content", None)
    if not isinstance(content, dict) or key not in content:
        return default

    value = content.get(key)
    if isinstance(value, dict):
        if "value" in value:
            return default if value["value"] is None else value["value"]
        if "values" in value:
            return default if value["values"] is None else value["values"]

    return default if value is None else value


def json_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def note_raw_json(note: Any) -> str:
    if hasattr(note, "to_json") and callable(note.to_json):
        raw = note.to_json()
    else:
        raw = {
            key: value
            for key, value in vars(note).items()
            if not key.startswith("_")
        }
    return json.dumps(raw, ensure_ascii=False, default=str)


def note_to_record(note: Any, venue_id: str) -> dict[str, Any]:
    paper_id = str(getattr(note, "id", "") or "")
    now = dt.datetime.now(dt.UTC).isoformat()

    return {
        "id": paper_id,
        "forum": getattr(note, "forum", None),
        "venue_id": venue_id,
        "title": get_content_value(note, "title"),
        "abstract": get_content_value(note, "abstract"),
        "authors": json_string(get_content_value(note, "authors")),
        "keywords": json_string(get_content_value(note, "keywords")),
        "pdf_url": f"https://openreview.net/pdf?id={paper_id}" if paper_id else None,
        "html_url": f"https://openreview.net/forum?id={paper_id}" if paper_id else None,
        "odate": getattr(note, "odate", None),
        "tcdate": getattr(note, "tcdate", None),
        "tmdate": getattr(note, "tmdate", None),
        "pdate": getattr(note, "pdate", None),
        "source": "openreview",
        "raw_json": note_raw_json(note),
        "fetched_at": now,
    }


def insert_paper(conn: sqlite3.Connection, record: dict[str, Any]) -> bool:
    before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO papers (
            id, forum, venue_id, title, abstract, authors, keywords, pdf_url,
            html_url, odate, tcdate, tmdate, pdate, source, raw_json, fetched_at
        ) VALUES (
            :id, :forum, :venue_id, :title, :abstract, :authors, :keywords,
            :pdf_url, :html_url, :odate, :tcdate, :tmdate, :pdate, :source,
            :raw_json, :fetched_at
        )
        """,
        record,
    )
    return conn.total_changes > before


def fetch_recent_papers_for_venue(
    client: Any,
    venue_id: str,
    start_ms: int,
    end_ms: int,
    *,
    verbose: bool = False,
) -> list[Any]:
    notes = retry_api_call(
        lambda: list(client.get_all_notes(content={"venueid": venue_id})),
        verbose=verbose,
    )
    recent_notes = []
    for note in notes:
        odate = getattr(note, "odate", None)
        if odate is not None and start_ms <= int(odate) < end_ms:
            recent_notes.append(note)
    return recent_notes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect recently public OpenReview papers from active venues."
    )
    parser.add_argument("--days", type=int, default=1, help="Days back to fetch.")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite database path.")
    parser.add_argument("--sleep", type=float, default=0.5, help="Seconds between venues.")
    parser.add_argument(
        "--limit-venues",
        type=int,
        default=None,
        help="Only process the first N active venues.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print more details.")
    return parser.parse_args()


def utc_window_ms(days: int) -> tuple[int, int]:
    if days < 1:
        raise ValueError("--days must be at least 1.")
    end = dt.datetime.now(dt.UTC)
    start = end - dt.timedelta(days=days)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def process_venues(
    client: Any,
    conn: sqlite3.Connection,
    venues: Iterable[str],
    start_ms: int,
    end_ms: int,
    sleep_seconds: float,
    *,
    verbose: bool = False,
) -> dict[str, int]:
    stats = {
        "venues_processed": 0,
        "venues_failed": 0,
        "recent_papers_found": 0,
        "new_papers_inserted": 0,
        "duplicates_skipped": 0,
    }

    venue_list = list(venues)
    total = len(venue_list)
    for index, venue_id in enumerate(venue_list, start=1):
        print(f"[{index}/{total}] Processing venue: {venue_id}")
        try:
            papers = fetch_recent_papers_for_venue(
                client,
                venue_id,
                start_ms,
                end_ms,
                verbose=verbose,
            )
            stats["venues_processed"] += 1
            stats["recent_papers_found"] += len(papers)

            inserted = 0
            duplicates = 0
            for paper in papers:
                record = note_to_record(paper, venue_id)
                if insert_paper(conn, record):
                    inserted += 1
                else:
                    duplicates += 1
            conn.commit()

            stats["new_papers_inserted"] += inserted
            stats["duplicates_skipped"] += duplicates
            if verbose:
                print(
                    f"  Found {len(papers)} recent papers; "
                    f"inserted {inserted}, skipped {duplicates} duplicates."
                )
        except Exception as exc:
            stats["venues_failed"] += 1
            print(f"  Failed venue {venue_id}: {exc}", file=sys.stderr)
        finally:
            if sleep_seconds > 0 and index < total:
                time.sleep(sleep_seconds)

    return stats


def main() -> None:
    args = parse_args()
    start_ms, end_ms = utc_window_ms(args.days)

    db_path = str(Path(args.db))
    client = create_client()
    conn = init_db(db_path)

    try:
        venues = get_active_venues(client, verbose=args.verbose)
        active_venues_found = len(venues)
        print(f"Active venues found: {active_venues_found}")

        if args.limit_venues is not None:
            venues = venues[: args.limit_venues]
            if args.verbose:
                print(f"Limiting to first {len(venues)} venues.")

        stats = process_venues(
            client,
            conn,
            venues,
            start_ms,
            end_ms,
            args.sleep,
            verbose=args.verbose,
        )
    finally:
        conn.close()

    print()
    print("Summary")
    print(f"Active venues found: {active_venues_found}")
    print(f"Venues processed: {stats['venues_processed']}")
    print(f"Venues failed: {stats['venues_failed']}")
    print(f"Recent papers found: {stats['recent_papers_found']}")
    print(f"New papers inserted: {stats['new_papers_inserted']}")
    print(f"Duplicates skipped: {stats['duplicates_skipped']}")
    print(f"Database: {db_path}")


if __name__ == "__main__":
    main()
