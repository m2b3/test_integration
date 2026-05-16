"""
Fetch arXiv papers updated/submitted in the last 24 hours and store them into
a SQLite database with deduping by canonical arXiv ID.

- Uses the arXiv Atom API with sortBy=lastUpdatedDate
- Dedupe = arxiv_id primary key in SQLite
- Fetch window = locally filtered UTC cutoff of now - 24 hours
- Batches through results with start and max_results

Usage:
  python arxiv.py --db arxiv.sqlite
  python arxiv.py --db arxiv.sqlite --max 500

Notes:
  - arXiv's API can sort by lastUpdatedDate, but its query syntax does not
    perfectly express "updated in the past 24 hours" for every use case. This
    script therefore fetches pages sorted by update time and filters locally.
  - The default query uses a UTC submittedDate window to avoid asking arXiv to
    sort the whole archive, which can trigger HTTP 429 rate limits.
"""

from __future__ import annotations
import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError

ARXIV_API_URL = "https://export.arxiv.org/api/query"
DEFAULT_QUERY = ""
ATOM_NS = "http://www.w3.org/2005/Atom"
ARXIV_NS = "http://arxiv.org/schemas/atom"
OPENSEARCH_NS = "http://a9.com/-/spec/opensearch/1.1/"
NS = {"atom": ATOM_NS, "arxiv": ARXIV_NS, "opensearch": OPENSEARCH_NS}

@dataclass
class FetchStats:
    total_seen: int = 0
    total_kept_last24h: int = 0
    api_total_results: Optional[int] = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def arxiv_query_datetime(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M")


def default_query_for_window(cutoff_utc: datetime, now_utc: datetime) -> str:
    return f"submittedDate:[{arxiv_query_datetime(cutoff_utc)} TO {arxiv_query_datetime(now_utc)}]"


def chunked(seq: List[str], n: int) -> Iterable[List[str]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS arxiv_articles (
            arxiv_id          TEXT PRIMARY KEY,
            title             TEXT,
            journal           TEXT,
            pub_date          TEXT,
            updated_date      TEXT,
            doi               TEXT,
            authors           TEXT,   -- JSON array of strings
            abstract          TEXT,
            categories        TEXT,   -- JSON array of strings
            primary_category  TEXT,
            url               TEXT,
            pdf_url           TEXT,
            fetched_at        TEXT,
            raw_json          TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_arxiv_articles_updated_date
        ON arxiv_articles(updated_date);
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_arxiv_articles_pub_date
        ON arxiv_articles(pub_date);
        """
    )
    conn.commit()
    return conn


def existing_arxiv_ids(conn: sqlite3.Connection, arxiv_ids: List[str]) -> set[str]:
    if not arxiv_ids:
        return set()
    out: set[str] = set()
    for block in chunked(arxiv_ids, 800):
        qmarks = ",".join(["?"] * len(block))
        rows = conn.execute(f"SELECT arxiv_id FROM arxiv_articles WHERE arxiv_id IN ({qmarks})", block).fetchall()
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
                INSERT OR IGNORE INTO arxiv_articles
                (
                    arxiv_id, title, journal, pub_date, updated_date, doi,
                    authors, abstract, categories, primary_category, url,
                    pdf_url, fetched_at, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    r.get("arxiv_id"),
                    r.get("title"),
                    r.get("journal"),
                    r.get("pub_date"),
                    r.get("updated_date"),
                    r.get("doi"),
                    json.dumps(r.get("authors", []), ensure_ascii=False),
                    r.get("abstract"),
                    json.dumps(r.get("categories", []), ensure_ascii=False),
                    r.get("primary_category"),
                    r.get("url"),
                    r.get("pdf_url"),
                    r.get("fetched_at"),
                    json.dumps(r, ensure_ascii=False),
                ),
            )
            if cur.rowcount == 1:
                inserted += 1
        except sqlite3.Error as e:
            print(f"[error] insert failed for arxiv_id={r.get('arxiv_id')}: {e}", file=sys.stderr)
    conn.commit()
    return inserted


def normalize_space(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    value = " ".join(text.split())
    return value or None


def parse_arxiv_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    raw = value.strip()
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


def datetime_to_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def normalize_arxiv_id(raw_id: Optional[str]) -> Optional[str]:
    if not raw_id:
        return None
    value = raw_id.strip()
    if not value:
        return None
    if "/abs/" in value:
        value = value.rsplit("/abs/", 1)[1]
    elif "/pdf/" in value:
        value = value.rsplit("/pdf/", 1)[1]
    value = value.split("?", 1)[0].split("#", 1)[0].strip().strip("/")
    value = re.sub(r"\.pdf$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"v\d+$", "", value)
    return value or None


def parse_arxiv_entry(entry: ET.Element) -> Dict[str, object]:
    entry_id = normalize_space(entry.findtext("atom:id", namespaces=NS))
    if entry_id and "/api/errors" in entry_id:
        summary = normalize_space(entry.findtext("atom:summary", namespaces=NS)) or "arXiv API error entry"
        raise ValueError(summary)

    arxiv_id = normalize_arxiv_id(entry_id)
    if not arxiv_id:
        raise ValueError("missing arXiv ID")

    published_dt = parse_arxiv_datetime(entry.findtext("atom:published", namespaces=NS))
    updated_dt = parse_arxiv_datetime(entry.findtext("atom:updated", namespaces=NS))

    authors = [
        name
        for name in (
            normalize_space(author.findtext("atom:name", namespaces=NS))
            for author in entry.findall("atom:author", namespaces=NS)
        )
        if name
    ]

    categories = [
        term
        for term in (
            normalize_space(category.attrib.get("term"))
            for category in entry.findall("atom:category", namespaces=NS)
        )
        if term
    ]

    primary_el = entry.find("arxiv:primary_category", namespaces=NS)
    primary_category = normalize_space(primary_el.attrib.get("term")) if primary_el is not None else None

    pdf_url = None
    url = entry_id
    for link in entry.findall("atom:link", namespaces=NS):
        href = normalize_space(link.attrib.get("href"))
        if not href:
            continue
        title = (link.attrib.get("title") or "").lower()
        link_type = (link.attrib.get("type") or "").lower()
        rel = (link.attrib.get("rel") or "").lower()
        if rel == "alternate" and not url:
            url = href
        if title == "pdf" or link_type == "application/pdf":
            pdf_url = href

    record: Dict[str, object] = {
        "arxiv_id": arxiv_id,
        "title": normalize_space(entry.findtext("atom:title", namespaces=NS)),
        "journal": normalize_space(entry.findtext("arxiv:journal_ref", namespaces=NS)),
        "pub_date": datetime_to_iso(published_dt),
        "updated_date": datetime_to_iso(updated_dt),
        "doi": normalize_space(entry.findtext("arxiv:doi", namespaces=NS)),
        "authors": authors,
        "abstract": normalize_space(entry.findtext("atom:summary", namespaces=NS)),
        "categories": categories,
        "primary_category": primary_category,
        "url": url,
        "pdf_url": pdf_url,
        "fetched_at": utc_now_iso(),
    }
    return record


def build_arxiv_url(query: str, start: int, max_results: int) -> str:
    params = {
        "search_query": query,
        "start": str(start),
        "max_results": str(max_results),
        "sortBy": "lastUpdatedDate",
        "sortOrder": "descending",
    }
    return f"{ARXIV_API_URL}?{urllib.parse.urlencode(params)}"


def fetch_url_with_retries(url: str, max_retries: int, label: str) -> bytes:
    headers = {"User-Agent": "arxiv_last24h_to_sqlite/1.0 (Python urllib)"}
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


def parse_total_results(feed: ET.Element) -> Optional[int]:
    raw = feed.findtext("opensearch:totalResults", namespaces=NS)
    if not raw:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def parse_arxiv_feed(xml_bytes: bytes) -> ET.Element:
    try:
        return ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        print(f"[error] XML parse error: {e}", file=sys.stderr)
        raise


def filter_date_for_record(record: Dict[str, object]) -> Optional[datetime]:
    updated_dt = parse_arxiv_datetime(str(record.get("updated_date") or ""))
    if updated_dt is not None:
        return updated_dt
    return parse_arxiv_datetime(str(record.get("pub_date") or ""))


def fetch_arxiv_last_24h(
    query: str,
    *,
    start_from: int = 0,
    fetch_batch: int = 100,
    max_records: int = 0,
    sleep_seconds: float = 3.0,
    max_retries: int = 3,
) -> Tuple[List[Dict[str, object]], FetchStats]:
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    cutoff_utc = now_utc - timedelta(hours=24)
    query = query.strip() or default_query_for_window(cutoff_utc, now_utc)
    records: List[Dict[str, object]] = []
    stats = FetchStats()

    retstart = max(start_from, 0)
    fetch_batch = max(1, min(fetch_batch, 2000))

    print(f"[info] Query: {query}")
    print(f"[info] UTC now={now_utc.isoformat()} cutoff={cutoff_utc.isoformat()}")
    print("[info] Using arXiv sortBy=lastUpdatedDate sortOrder=descending; filtering dates locally.")
    if query.startswith("submittedDate:["):
        print("[info] Default query is limited by submittedDate to avoid broad archive-wide API scans.")

    while True:
        remaining = max_records - stats.total_seen if max_records and max_records > 0 else fetch_batch
        if max_records and remaining <= 0:
            print(f"[info] Applying cap --max={max_records}; stopping fetch.")
            break

        this_batch = min(fetch_batch, remaining) if max_records and max_records > 0 else fetch_batch
        url = build_arxiv_url(query, retstart, this_batch)
        label = f"arXiv API start={retstart} max_results={this_batch}"
        xml_bytes = fetch_url_with_retries(url, max_retries=max_retries, label=label)
        feed = parse_arxiv_feed(xml_bytes)

        if stats.api_total_results is None:
            stats.api_total_results = parse_total_results(feed)

        entries = feed.findall("atom:entry", namespaces=NS)
        if not entries:
            print(f"[warn] No entries returned at start={retstart}; stopping.")
            break

        page_records: List[Dict[str, object]] = []
        page_dates: List[datetime] = []
        page_parse_errors = 0

        for entry in entries:
            stats.total_seen += 1
            try:
                record = parse_arxiv_entry(entry)
            except Exception as e:
                page_parse_errors += 1
                print(f"[warn] Failed to parse arXiv entry at start={retstart}: {e}", file=sys.stderr)
                continue

            filter_dt = filter_date_for_record(record)
            if filter_dt is not None:
                page_dates.append(filter_dt)
            if filter_dt is not None and filter_dt >= cutoff_utc:
                page_records.append(record)

        records.extend(page_records)
        stats.total_kept_last24h += len(page_records)

        print(
            f"[page] start={retstart} fetched={len(entries)} kept_last24h={len(page_records)} "
            f"parse_errors={page_parse_errors} total_seen={stats.total_seen}"
        )
        print(f"[progress] To resume from this point if interrupted, use: --start-from {retstart + len(entries)}", file=sys.stderr)

        if max_records and stats.total_seen >= max_records:
            print(f"[info] Applying cap --max={max_records}; stopping fetch.")
            break

        if page_dates and all(dt < cutoff_utc for dt in page_dates):
            print("[info] Reached entries older than the 24h cutoff; stopping.")
            break

        if stats.api_total_results is not None and retstart + len(entries) >= stats.api_total_results:
            print("[info] Reached end of arXiv result set; stopping.")
            break

        retstart += len(entries)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return records, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="SQLite database path")
    ap.add_argument(
        "--query",
        default=DEFAULT_QUERY,
        help=(
            "arXiv API search query. Default is a UTC submittedDate window for the last 24 hours; "
            "results are sorted by lastUpdatedDate and locally filtered."
        ),
    )
    ap.add_argument("--fetch-batch", type=int, default=100, help="Batch size for arXiv API requests")
    ap.add_argument("--max", type=int, default=0, help="Optional cap on total API entries processed (0 = no cap)")
    ap.add_argument("--sleep", type=float, default=3.0, help="Seconds to sleep between arXiv API requests")
    ap.add_argument("--start-from", type=int, default=0, help="Start fetching from this offset (useful for resuming)")
    ap.add_argument("--max-retries", type=int, default=3, help="Number of retries for failed arXiv API requests")
    args = ap.parse_args()

    if args.fetch_batch <= 0:
        print("[error] --fetch-batch must be > 0", file=sys.stderr)
        return 1
    if args.fetch_batch > 2000:
        print("[warn] arXiv API slices are limited to 2000; using fetch_batch=2000.", file=sys.stderr)
    if args.max < 0:
        print("[error] --max must be >= 0", file=sys.stderr)
        return 1
    if args.start_from < 0:
        print("[error] --start-from must be >= 0", file=sys.stderr)
        return 1
    if args.max_retries <= 0:
        print("[error] --max-retries must be > 0", file=sys.stderr)
        return 1

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = init_db(args.db)
        records, stats = fetch_arxiv_last_24h(
            args.query,
            start_from=args.start_from,
            fetch_batch=args.fetch_batch,
            max_records=args.max,
            sleep_seconds=args.sleep,
            max_retries=args.max_retries,
        )

        arxiv_ids = [str(r["arxiv_id"]) for r in records if r.get("arxiv_id")]
        already = existing_arxiv_ids(conn, arxiv_ids)
        new_records = [r for r in records if r.get("arxiv_id") and r["arxiv_id"] not in already]
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
