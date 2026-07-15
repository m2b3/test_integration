#!/usr/bin/env python3
"""
Apply non-destructive Scicommons user-database migrations.

Usage:
  DATABASE_URL="postgresql://user:password@host:5432/scicommons" python migrate_database.py
"""

from __future__ import annotations

import os

try:
    import psycopg
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: psycopg. Install backend requirements first with "
        "`python -m pip install -r requirements.txt`."
    ) from exc


MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS user_daily_feed (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    feed_date DATE NOT NULL,
    article_key TEXT NOT NULL,
    rank INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, feed_date, article_key),
    UNIQUE (user_id, feed_date, rank)
);

CREATE INDEX IF NOT EXISTS idx_user_daily_feed_lookup
ON user_daily_feed(user_id, feed_date, rank);

CREATE INDEX IF NOT EXISTS idx_user_daily_feed_date
ON user_daily_feed(feed_date);
"""


def require_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit(
            "DATABASE_URL is required, for example:\n"
            '  DATABASE_URL="postgresql://user:password@host:5432/scicommons" '
            "python migrate_database.py"
        )
    return database_url


def main() -> int:
    with psycopg.connect(require_database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(MIGRATION_SQL)
    print("Database migrations applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
