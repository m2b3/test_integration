from __future__ import annotations

import os
from datetime import date
from typing import Annotated, Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from pydantic import BaseModel


DEFAULT_DATABASE_URL = "postgresql://scicommons:scicommons@localhost:5432/scicommons"
MatchMode = Literal["and", "or"]


app = FastAPI(title="Scicommons Prototype API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://134.87.8.193",
        "http://134.87.8.193:5173",
        "http://192.168.167.59",
        "http://192.168.167.59:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginRequest(BaseModel):
    username: str = "Demo User"
    email: str


class TagsUpdateRequest(BaseModel):
    tags: list[str]
    match_mode: MatchMode = "or"


def database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_db():
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        yield conn


def parse_tags(tags: str | None) -> list[str]:
    if not tags:
        return []
    return [tag.strip() for tag in tags.split(",") if tag.strip()]


def normalize_article(row: dict) -> dict:
    return {
        **row,
        "paper_key": row["id"],
        "published_date": row["published_date"].isoformat()
        if hasattr(row["published_date"], "isoformat")
        else row["published_date"],
        "tags": row["tags"] or [],
    }


def article_query(
    *,
    user_id: str | None = None,
    tags: list[str] | None = None,
    match: MatchMode = "or",
    source: str = "all",
    q: str = "",
    article_date: date | None = None,
) -> tuple[str, list[object]]:
    selected_tags = tags or []
    params: list[object] = []
    where_clauses: list[str] = []
    joins = ""

    if user_id is not None:
        joins += "JOIN user_daily_feed udf ON udf.article_id = a.id "
        where_clauses.append("udf.user_id = %s")
        params.append(user_id)
        if article_date is not None:
            where_clauses.append("udf.feed_date = %s")
            params.append(article_date)

    if article_date is not None and user_id is None:
        where_clauses.append("a.published_date = %s")
        params.append(article_date)

    if source != "all":
        where_clauses.append("a.source = %s")
        params.append(source)

    if q.strip():
        where_clauses.append("(a.title ILIKE %s OR a.authors ILIKE %s)")
        search = f"%{q.strip()}%"
        params.extend([search, search])

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    tag_filter_sql = ""

    if selected_tags:
        if match == "and":
            tag_filter_sql = "WHERE %s::text[] <@ tags"
        else:
            tag_filter_sql = "WHERE %s::text[] && tags"
        params.append(selected_tags)

    sql = f"""
        WITH article_rows AS (
            SELECT DISTINCT
                a.id,
                a.title,
                a.authors,
                a.source,
                a.url,
                a.published_date,
                a.abstract
            FROM articles a
            {joins}
            {where_sql}
        ),
        tagged AS (
            SELECT
                ar.id,
                ar.title,
                ar.authors,
                ar.source,
                ar.url,
                ar.published_date,
                ar.abstract,
                COALESCE(
                    array_agg(at.tag_id ORDER BY at.tag_id)
                    FILTER (WHERE at.tag_id IS NOT NULL),
                    '{{}}'
                ) AS tags
            FROM article_rows ar
            LEFT JOIN article_tags at ON at.article_id = ar.id
            GROUP BY
                ar.id,
                ar.title,
                ar.authors,
                ar.source,
                ar.url,
                ar.published_date,
                ar.abstract
        )
        SELECT *
        FROM tagged
        {tag_filter_sql}
        ORDER BY published_date DESC, id ASC
    """

    return sql, params


@app.get("/health")
def health(conn: Annotated[psycopg.Connection, Depends(get_db)]) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 AS ok")
        cur.fetchone()
    return {"status": "ok"}


@app.get("/tags")
def get_tags(conn: Annotated[psycopg.Connection, Depends(get_db)]) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                t.id,
                t.name,
                COUNT(at.article_id)::int AS count
            FROM tags t
            LEFT JOIN article_tags at ON at.tag_id = t.id
            GROUP BY t.id, t.name
            ORDER BY t.name ASC
            """
        )
        return list(cur.fetchall())


@app.get("/sources")
def get_sources(conn: Annotated[psycopg.Connection, Depends(get_db)]) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT source FROM articles ORDER BY source ASC")
        return [row["source"] for row in cur.fetchall()]


@app.get("/articles")
def get_articles(
    conn: Annotated[psycopg.Connection, Depends(get_db)],
    tags: str | None = None,
    match: MatchMode = "or",
    source: str = "all",
    q: str = "",
    date_filter: date | None = Query(default=None, alias="date"),
) -> list[dict]:
    sql, params = article_query(
        tags=parse_tags(tags),
        match=match,
        source=source,
        q=q,
        article_date=date_filter,
    )
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [normalize_article(row) for row in cur.fetchall()]


@app.get("/users/{user_id}/feed")
def get_user_feed(
    user_id: str,
    conn: Annotated[psycopg.Connection, Depends(get_db)],
    tags: str | None = None,
    match: MatchMode = "or",
    source: str = "all",
    q: str = "",
    date_filter: date = Query(default_factory=date.today, alias="date"),
) -> list[dict]:
    sql, params = article_query(
        user_id=user_id,
        tags=parse_tags(tags),
        match=match,
        source=source,
        q=q,
        article_date=date_filter,
    )
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [normalize_article(row) for row in cur.fetchall()]


@app.get("/users/{user_id}/tags")
def get_user_tags(user_id: str, conn: Annotated[psycopg.Connection, Depends(get_db)]) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE id = %s", [user_id])
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found")

        cur.execute(
            """
            SELECT t.id, t.name
            FROM user_tags ut
            JOIN tags t ON t.id = ut.tag_id
            WHERE ut.user_id = %s
            ORDER BY t.name ASC
            """,
            [user_id],
        )
        return {"user_id": user_id, "tags": list(cur.fetchall())}


@app.post("/login")
def login(payload: LoginRequest, conn: Annotated[psycopg.Connection, Depends(get_db)]) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT id, email FROM users WHERE email = %s", [payload.email])
        user = cur.fetchone()

        if user is None:
            user_id = "demo-" + payload.email.lower().replace("@", "-").replace(".", "-")
            cur.execute(
                """
                INSERT INTO users (id, email)
                VALUES (%s, %s)
                ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email
                RETURNING id, email
                """,
                [user_id, payload.email],
            )
            user = cur.fetchone()

        cur.execute(
            """
            SELECT tag_id
            FROM user_tags
            WHERE user_id = %s
            ORDER BY tag_id ASC
            """,
            [user["id"]],
        )
        user_tags = [row["tag_id"] for row in cur.fetchall()]

    return {
        "user_id": user["id"],
        "username": payload.username,
        "email": user["email"],
        "tags": user_tags,
    }


@app.put("/users/{user_id}/tags")
def update_user_tags(
    user_id: str,
    payload: TagsUpdateRequest,
    conn: Annotated[psycopg.Connection, Depends(get_db)],
) -> dict:
    unique_tags = list(dict.fromkeys(payload.tags))

    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE id = %s", [user_id])
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found")

        if unique_tags:
            cur.execute("SELECT id FROM tags WHERE id = ANY(%s)", [unique_tags])
            valid_tags = {row["id"] for row in cur.fetchall()}
            missing_tags = [tag for tag in unique_tags if tag not in valid_tags]
            if missing_tags:
                raise HTTPException(
                    status_code=400,
                    detail={"message": "Unknown tag IDs", "tags": missing_tags},
                )

        cur.execute("DELETE FROM user_tags WHERE user_id = %s", [user_id])
        if unique_tags:
            cur.executemany(
                """
                INSERT INTO user_tags (user_id, tag_id)
                VALUES (%s, %s)
                """,
                [(user_id, tag_id) for tag_id in unique_tags],
            )

    return {
        "user_id": user_id,
        "tags": unique_tags,
        "match_mode": payload.match_mode,
    }
