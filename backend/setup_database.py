#!/usr/bin/env python3
"""
Create and seed the Scicommons prototype database.

Usage:
  DATABASE_URL="postgresql://user:password@host:5432/scicommons" python setup_database.py

The script intentionally drops and recreates the prototype tables on every run.
It does not create the database itself; create an empty Postgres database first,
then run this script against it.
"""

from __future__ import annotations

import os
import sys

try:
    import psycopg
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: psycopg. Install backend requirements first with "
        "`python -m pip install -r requirements.txt`."
    ) from exc


USERS = [
    {"id": "user-1", "email": "u1@example.com"},
    {"id": "user-2", "email": "u2@example.com"},
    {"id": "user-3", "email": "u3@example.com"},
]

TAGS = [
    {"id": "biology", "name": "biology"},
    {"id": "machine-learning", "name": "machine learning"},
    {"id": "chemistry", "name": "chemistry"},
    {"id": "medicine", "name": "medicine"},
    {"id": "physics", "name": "physics"},
]

USER_TAGS = [
    {"user_id": "user-1", "tag_id": "biology"},
    {"user_id": "user-2", "tag_id": "machine-learning"},
    {"user_id": "user-3", "tag_id": "chemistry"},
    {"user_id": "user-3", "tag_id": "medicine"},
]

ARTICLES = [
    {
        "id": "article-1",
        "title": "bio paper",
        "authors": "bio author",
        "source": "biorxiv",
        "url": "",
        "published_date": "2026-06-26",
        "abstract": "asdf biology mock abstract text",
    },
    {
        "id": "article-2",
        "title": "machine learning paper",
        "authors": "machine learning author",
        "source": "arxiv",
        "url": "",
        "published_date": "2026-06-26",
        "abstract": "asdf machine learning mock abstract text",
    },
    {
        "id": "article-3",
        "title": "chemistry paper",
        "authors": "chem author",
        "source": "pubmed",
        "url": "",
        "published_date": "2026-06-26",
        "abstract": "asdf chemistry mock abstract text",
    },
    {
        "id": "article-4",
        "title": "medicine paper",
        "authors": "medicine author",
        "source": "medrxiv",
        "url": "",
        "published_date": "2026-06-26",
        "abstract": "asdf medicine mock abstract text",
    },
    {
        "id": "article-5",
        "title": "physics paper",
        "authors": "physics author",
        "source": "arxiv",
        "url": "",
        "published_date": "2026-06-26",
        "abstract": "asdf physics mock abstract text",
    },
    {
        "id": "article-6",
        "title": "chem and bio paper",
        "authors": "chem author, bio author",
        "source": "biorxiv",
        "url": "test text",
        "published_date": "2026-06-26",
        "abstract": "asdf chemistry biology mock abstract text",
    },
    {
        "id": "article-7",
        "title": "bio and machine learning paper",
        "authors": "bio author, machine learning author",
        "source": "arxiv",
        "url": "test text",
        "published_date": "2026-06-26",
        "abstract": "asdf biology machine learning mock abstract text",
    },
    {
        "id": "article-8",
        "title": "medicine and chemistry paper",
        "authors": "medicine author, chem author",
        "source": "pubmed",
        "url": "test text",
        "published_date": "2026-06-26",
        "abstract": "asdf medicine chemistry mock abstract text",
    },
]

ARTICLE_TAGS = [
    {"article_id": "article-1", "tag_id": "biology"},
    {"article_id": "article-2", "tag_id": "machine-learning"},
    {"article_id": "article-3", "tag_id": "chemistry"},
    {"article_id": "article-4", "tag_id": "medicine"},
    {"article_id": "article-5", "tag_id": "physics"},
    {"article_id": "article-6", "tag_id": "chemistry"},
    {"article_id": "article-6", "tag_id": "biology"},
    {"article_id": "article-7", "tag_id": "biology"},
    {"article_id": "article-7", "tag_id": "machine-learning"},
    {"article_id": "article-8", "tag_id": "medicine"},
    {"article_id": "article-8", "tag_id": "chemistry"},
]

USER_DAILY_FEED = [
    {"user_id": "user-1", "article_id": "article-1", "feed_date": "2026-06-26"},
    {"user_id": "user-1", "article_id": "article-6", "feed_date": "2026-06-26"},
    {"user_id": "user-1", "article_id": "article-7", "feed_date": "2026-06-26"},
    {"user_id": "user-2", "article_id": "article-2", "feed_date": "2026-06-26"},
    {"user_id": "user-2", "article_id": "article-7", "feed_date": "2026-06-26"},
    {"user_id": "user-3", "article_id": "article-3", "feed_date": "2026-06-26"},
    {"user_id": "user-3", "article_id": "article-6", "feed_date": "2026-06-26"},
    {"user_id": "user-3", "article_id": "article-8", "feed_date": "2026-06-26"},
]


DROP_TABLES_SQL = """
DROP TABLE IF EXISTS user_recently_viewed;
DROP TABLE IF EXISTS user_daily_feed;
DROP TABLE IF EXISTS article_tags;
DROP TABLE IF EXISTS user_tags;
DROP TABLE IF EXISTS articles;
DROP TABLE IF EXISTS tags;
DROP TABLE IF EXISTS users;
"""


CREATE_TABLES_SQL = """
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE
);

CREATE TABLE tags (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE user_tags (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, tag_id)
);

CREATE TABLE articles (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,
    source TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    published_date DATE NOT NULL,
    abstract TEXT NOT NULL DEFAULT ''
);

CREATE TABLE article_tags (
    article_id TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (article_id, tag_id)
);

CREATE TABLE user_daily_feed (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    article_id TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    feed_date DATE NOT NULL,
    PRIMARY KEY (user_id, article_id, feed_date)
);

CREATE TABLE user_recently_viewed (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    article_key TEXT NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT,
    title TEXT NOT NULL,
    authors TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    published_date TEXT,
    abstract TEXT NOT NULL DEFAULT '',
    tags TEXT[] NOT NULL DEFAULT '{}',
    viewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, article_key)
);

CREATE INDEX idx_user_tags_tag_id ON user_tags(tag_id);
CREATE INDEX idx_article_tags_tag_id ON article_tags(tag_id);
CREATE INDEX idx_articles_published_date ON articles(published_date);
CREATE INDEX idx_articles_source ON articles(source);
CREATE INDEX idx_user_daily_feed_date ON user_daily_feed(feed_date);
CREATE INDEX idx_user_recently_viewed_viewed_at ON user_recently_viewed(user_id, viewed_at DESC);
"""


def require_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit(
            "DATABASE_URL is required, for example:\n"
            '  DATABASE_URL="postgresql://user:password@host:5432/scicommons" '
            "python setup_database.py"
        )
    return database_url


def insert_rows(cur: psycopg.Cursor, table_name: str, columns: list[str], rows: list[dict[str, str]]) -> None:
    placeholders = ", ".join(["%s"] * len(columns))
    column_sql = ", ".join(columns)
    values = [tuple(row[column] for column in columns) for row in rows]
    cur.executemany(
        f"INSERT INTO {table_name} ({column_sql}) VALUES ({placeholders})",
        values,
    )


def main() -> int:
    database_url = require_database_url()

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(DROP_TABLES_SQL)
            cur.execute(CREATE_TABLES_SQL)
            insert_rows(cur, "users", ["id", "email"], USERS)
            insert_rows(cur, "tags", ["id", "name"], TAGS)
            insert_rows(cur, "user_tags", ["user_id", "tag_id"], USER_TAGS)
            insert_rows(
                cur,
                "articles",
                ["id", "title", "authors", "source", "url", "published_date", "abstract"],
                ARTICLES,
            )
            insert_rows(cur, "article_tags", ["article_id", "tag_id"], ARTICLE_TAGS)
            insert_rows(cur, "user_daily_feed", ["user_id", "article_id", "feed_date"], USER_DAILY_FEED)

    print("Database tables recreated and seeded.")
    print(f"Inserted {len(USERS)} users, {len(TAGS)} tags, and {len(ARTICLES)} articles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
