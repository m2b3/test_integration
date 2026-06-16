"""
Fetch PsyArXiv papers from the past 24 hours into SQLite.

Usage:
  python psyarxiv.py --db psyarxiv.sqlite
  python psyarxiv.py --db psyarxiv.sqlite --query "cognitive bias"
  python psyarxiv.py --db psyarxiv.sqlite --hours 24 --max-results 500
"""

from __future__ import annotations
import argparse
import json
import sys
from osf_preprints import (
    OSFPreprintFetchError,
    fetch_osf_preprints_provider,
    fetch_osf_preprints_provider_with_stats,
    init_papers_db,
    insert_papers,
)
PROVIDER = "psyarxiv"

def fetch_psyarxiv_papers(
    hours: int = 24,
    query: str | None = None,
    max_results: int | None = None,
    page_size: int = 100,
) -> list[dict]:
    return fetch_osf_preprints_provider(
        PROVIDER,
        hours=hours,
        query=query,
        max_results=max_results,
        page_size=page_size,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="psyarxiv.sqlite", help="SQLite database path")
    ap.add_argument("--hours", type=int, default=24, help="UTC lookback window in hours")
    ap.add_argument("--query", default=None, help="Optional OSF preprint search query")
    ap.add_argument("--max-results", type=int, default=None, help="Optional cap on raw OSF records fetched")
    ap.add_argument("--page-size", type=int, default=100, help="OSF API page size")
    ap.add_argument("--dry-run", action="store_true", help="Print normalized records without inserting")
    args = ap.parse_args()

    conn = None
    inserted = 0
    try:
        papers, stats = fetch_osf_preprints_provider_with_stats(
            PROVIDER,
            hours=args.hours,
            query=args.query,
            max_results=args.max_results,
            page_size=args.page_size,
        )
        if args.dry_run:
            print(json.dumps(papers, ensure_ascii=False, indent=2))
        else:
            conn = init_papers_db(args.db)
            inserted = insert_papers(conn, papers)

        skipped = len(papers) - inserted if not args.dry_run else 0
        if not papers:
            print(f"[info] No {PROVIDER} records found in the last {args.hours} hours.")
        print(
            f"[done] provider={PROVIDER} db={args.db} raw_records={stats.raw_records_fetched} "
            f"retained_last_{args.hours}h={len(papers)} inserted={inserted} skipped_duplicates={skipped}"
        )
        return 0
    except (OSFPreprintFetchError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("[warn] Interrupted by user.", file=sys.stderr)
        return 130
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
