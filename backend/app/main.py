from __future__ import annotations

import os
import hashlib
import secrets
from datetime import date
from typing import Annotated, Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from pydantic import BaseModel


DEFAULT_DATABASE_URL = "postgresql://scicommons:scicommons@localhost:5432/scicommons"
MatchMode = Literal["and", "or"]
SESSION_COOKIE_NAME = "scicommons_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30


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


class UserProfileUpdateRequest(BaseModel):
    username: str | None = None
    email: str | None = None
    tags: list[str] | None = None
    authors: list[str] | None = None


class RecentlyViewedRequest(BaseModel):
    article_key: str | None = None
    id: str | None = None
    source: str
    external_id: str | None = None
    title: str
    authors: str = ""
    url: str = ""
    published_date: str | None = None
    abstract: str = ""
    tags: list[str] = []


def database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_db():
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        yield conn


def parse_tags(tags: str | None) -> list[str]:
    if not tags:
        return []
    return [tag.strip() for tag in tags.split(",") if tag.strip()]


def clean_unique_strings(values: list[str]) -> list[str]:
    return [
        value
        for value in dict.fromkeys(value.strip() for value in values)
        if value
    ]


def session_cookie_secure() -> bool:
    return os.getenv("SESSION_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}


def session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(cur: psycopg.Cursor, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    cur.execute(
        """
        INSERT INTO user_sessions (token_hash, user_id, expires_at)
        VALUES (%s, %s, now() + (%s || ' seconds')::interval)
        """,
        [session_token_hash(token), user_id, SESSION_MAX_AGE_SECONDS],
    )
    return token


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=session_cookie_secure(),
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        httponly=True,
        secure=session_cookie_secure(),
        samesite="lax",
        path="/",
    )


def get_session_user_id(request: Request, cur: psycopg.Cursor) -> str:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not logged in")

    cur.execute(
        """
        UPDATE user_sessions
        SET last_seen_at = now()
        WHERE token_hash = %s
          AND expires_at > now()
        RETURNING user_id
        """,
        [session_token_hash(token)],
    )
    session = cur.fetchone()
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return session["user_id"]


def require_user_session(request: Request, cur: psycopg.Cursor, user_id: str) -> None:
    session_user_id = get_session_user_id(request, cur)
    if session_user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot access another user")


def normalize_article(row: dict) -> dict:
    return {
        **row,
        "paper_key": row["id"],
        "published_date": row["published_date"].isoformat()
        if hasattr(row["published_date"], "isoformat")
        else row["published_date"],
        "tags": row["tags"] or [],
    }


def normalize_recently_viewed(row: dict) -> dict:
    return {
        "id": row["article_key"],
        "paper_key": row["article_key"],
        "source": row["source"],
        "external_id": row["external_id"],
        "title": row["title"],
        "authors": row["authors"],
        "url": row["url"],
        "published_date": row["published_date"],
        "abstract": row["abstract"],
        "tags": row["tags"] or [],
        "viewed_at": row["viewed_at"].isoformat()
        if hasattr(row["viewed_at"], "isoformat")
        else row["viewed_at"],
    }


def get_profile(cur: psycopg.Cursor, user_id: str) -> dict:
    cur.execute("SELECT id, email, username FROM users WHERE id = %s", [user_id])
    user = cur.fetchone()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    cur.execute(
        """
        SELECT tag_id
        FROM user_tags
        WHERE user_id = %s
        ORDER BY tag_id ASC
        """,
        [user_id],
    )
    tags = [row["tag_id"] for row in cur.fetchall()]

    cur.execute(
        """
        SELECT author_name
        FROM user_authors
        WHERE user_id = %s
        ORDER BY author_name ASC
        """,
        [user_id],
    )
    authors = [row["author_name"] for row in cur.fetchall()]

    return {
        "user_id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "tags": tags,
        "authors": authors,
    }


def replace_user_tags(cur: psycopg.Cursor, user_id: str, tags: list[str]) -> list[str]:
    unique_tags = clean_unique_strings(tags)

    if unique_tags:
        cur.executemany(
            """
            INSERT INTO tags (id, name)
            VALUES (%s, %s)
            ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
            """,
            [(tag, tag) for tag in unique_tags],
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
    return unique_tags


def replace_user_authors(cur: psycopg.Cursor, user_id: str, authors: list[str]) -> list[str]:
    unique_authors = clean_unique_strings(authors)
    cur.execute("DELETE FROM user_authors WHERE user_id = %s", [user_id])
    if unique_authors:
        cur.executemany(
            """
            INSERT INTO user_authors (user_id, author_name)
            VALUES (%s, %s)
            """,
            [(user_id, author_name) for author_name in unique_authors],
        )
    return unique_authors


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
    request: Request,
    conn: Annotated[psycopg.Connection, Depends(get_db)],
    tags: str | None = None,
    match: MatchMode = "or",
    source: str = "all",
    q: str = "",
    date_filter: date | None = Query(default=None, alias="date"),
) -> list[dict]:
    with conn.cursor() as cur:
        require_user_session(request, cur, user_id)

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
def get_user_tags(
    user_id: str,
    request: Request,
    conn: Annotated[psycopg.Connection, Depends(get_db)],
) -> dict:
    with conn.cursor() as cur:
        require_user_session(request, cur, user_id)
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


@app.get("/users/{user_id}/profile")
def get_user_profile(
    user_id: str,
    request: Request,
    conn: Annotated[psycopg.Connection, Depends(get_db)],
) -> dict:
    with conn.cursor() as cur:
        require_user_session(request, cur, user_id)
        return get_profile(cur, user_id)


@app.post("/login")
def login(
    payload: LoginRequest,
    response: Response,
    conn: Annotated[psycopg.Connection, Depends(get_db)],
) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT id, email, username FROM users WHERE email = %s", [payload.email])
        user = cur.fetchone()

        if user is None:
            user_id = "demo-" + payload.email.lower().replace("@", "-").replace(".", "-")
            cur.execute(
                """
                INSERT INTO users (id, email, username)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email
                RETURNING id, email, username
                """,
                [user_id, payload.email, payload.username],
            )
            user = cur.fetchone()
        elif payload.username.strip():
            cur.execute(
                """
                UPDATE users
                SET username = %s
                WHERE id = %s
                RETURNING id, email, username
                """,
                [payload.username.strip(), user["id"]],
            )
            user = cur.fetchone()

        token = create_session(cur, user["id"])
        set_session_cookie(response, token)
        return get_profile(cur, user["id"])


@app.get("/me")
def get_current_user(request: Request, conn: Annotated[psycopg.Connection, Depends(get_db)]) -> dict:
    with conn.cursor() as cur:
        user_id = get_session_user_id(request, cur)
        return get_profile(cur, user_id)


@app.post("/logout")
def logout(
    request: Request,
    response: Response,
    conn: Annotated[psycopg.Connection, Depends(get_db)],
) -> dict:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_sessions WHERE token_hash = %s", [session_token_hash(token)])

    clear_session_cookie(response)
    return {"status": "ok"}


@app.put("/users/{user_id}/tags")
def update_user_tags(
    user_id: str,
    payload: TagsUpdateRequest,
    request: Request,
    conn: Annotated[psycopg.Connection, Depends(get_db)],
) -> dict:
    with conn.cursor() as cur:
        require_user_session(request, cur, user_id)
        cur.execute("SELECT 1 FROM users WHERE id = %s", [user_id])
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found")
        unique_tags = replace_user_tags(cur, user_id, payload.tags)

    return {
        "user_id": user_id,
        "tags": unique_tags,
        "match_mode": payload.match_mode,
    }


@app.put("/users/{user_id}/profile")
def update_user_profile(
    user_id: str,
    payload: UserProfileUpdateRequest,
    request: Request,
    conn: Annotated[psycopg.Connection, Depends(get_db)],
) -> dict:
    with conn.cursor() as cur:
        require_user_session(request, cur, user_id)
        cur.execute("SELECT 1 FROM users WHERE id = %s", [user_id])
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found")

        if payload.username is not None:
            cur.execute(
                "UPDATE users SET username = %s WHERE id = %s",
                [payload.username.strip(), user_id],
            )
        if payload.email is not None:
            cur.execute(
                "UPDATE users SET email = %s WHERE id = %s",
                [payload.email.strip(), user_id],
            )
        if payload.tags is not None:
            replace_user_tags(cur, user_id, payload.tags)
        if payload.authors is not None:
            replace_user_authors(cur, user_id, payload.authors)

        return get_profile(cur, user_id)


@app.get("/users/{user_id}/recently-viewed")
def get_recently_viewed(
    user_id: str,
    request: Request,
    conn: Annotated[psycopg.Connection, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict]:
    with conn.cursor() as cur:
        require_user_session(request, cur, user_id)
        cur.execute("SELECT 1 FROM users WHERE id = %s", [user_id])
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found")

        cur.execute(
            """
            SELECT
                article_key,
                source,
                external_id,
                title,
                authors,
                url,
                published_date,
                abstract,
                tags,
                viewed_at
            FROM user_recently_viewed
            WHERE user_id = %s
            ORDER BY viewed_at DESC
            LIMIT %s
            """,
            [user_id, limit],
        )
        return [normalize_recently_viewed(row) for row in cur.fetchall()]


@app.post("/users/{user_id}/recently-viewed")
def add_recently_viewed(
    user_id: str,
    payload: RecentlyViewedRequest,
    request: Request,
    conn: Annotated[psycopg.Connection, Depends(get_db)],
) -> dict:
    article_key = (payload.article_key or payload.id or "").strip()
    if not article_key:
        raise HTTPException(status_code=400, detail="article_key or id is required")

    with conn.cursor() as cur:
        require_user_session(request, cur, user_id)
        cur.execute("SELECT 1 FROM users WHERE id = %s", [user_id])
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found")

        cur.execute(
            """
            INSERT INTO user_recently_viewed (
                user_id,
                article_key,
                source,
                external_id,
                title,
                authors,
                url,
                published_date,
                abstract,
                tags,
                viewed_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (user_id, article_key)
            DO UPDATE SET
                source = EXCLUDED.source,
                external_id = EXCLUDED.external_id,
                title = EXCLUDED.title,
                authors = EXCLUDED.authors,
                url = EXCLUDED.url,
                published_date = EXCLUDED.published_date,
                abstract = EXCLUDED.abstract,
                tags = EXCLUDED.tags,
                viewed_at = now()
            RETURNING
                article_key,
                source,
                external_id,
                title,
                authors,
                url,
                published_date,
                abstract,
                tags,
                viewed_at
            """,
            [
                user_id,
                article_key,
                payload.source,
                payload.external_id,
                payload.title,
                payload.authors,
                payload.url,
                payload.published_date,
                payload.abstract,
                payload.tags,
            ],
        )
        row = cur.fetchone()

    return normalize_recently_viewed(row)
