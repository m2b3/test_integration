from __future__ import annotations

import os
import hashlib
import json
import re
import secrets
from datetime import date
from typing import Annotated, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from pydantic import BaseModel


DEFAULT_DATABASE_URL = "postgresql://scicommons:scicommons@localhost:5432/scicommons"
DEFAULT_ARTICLE_SERVICE_BASE_URL = "http://localhost:8100"
MatchMode = Literal["and", "or"]
SESSION_COOKIE_NAME = "scicommons_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
USER_DAILY_FEED_SIZE = 500
ARTICLE_SERVICE_PAGE_SIZE = 200


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
    username: str
    email: str
    create_account: bool = False


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


def article_service_base_url() -> str:
    return os.getenv("ARTICLE_SERVICE_BASE_URL", DEFAULT_ARTICLE_SERVICE_BASE_URL).rstrip("/")


def get_db():
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        yield conn


def parse_tags(tags: str | None) -> list[str]:
    if not tags:
        return []
    return [tag.strip() for tag in tags.split(",") if tag.strip()]


def parse_article_service_error(exc: HTTPError) -> str:
    body = exc.read().decode("utf-8", errors="replace")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return body or exc.reason
    return str(parsed.get("detail") or body or exc.reason)


def article_service_get(path: str, params: dict[str, object]) -> object:
    clean_params = {
        key: value
        for key, value in params.items()
        if value is not None and value != ""
    }
    query = urlencode(clean_params)
    url = f"{article_service_base_url()}{path}"
    if query:
        url = f"{url}?{query}"

    request = UrlRequest(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise HTTPException(status_code=exc.code, detail=parse_article_service_error(exc)) from exc
    except URLError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Article service unavailable: {exc.reason}",
        ) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Article service returned invalid JSON",
        ) from exc


def article_service_params(
    *,
    source: str,
    tags: list[str],
    tag_match: MatchMode,
    q: str,
    semantic_query: str,
    keyword_query: str,
    search_mode: str,
    date_filter: date | None,
    limit: int,
    offset: int,
    include_total: bool = False,
    scope_semantic_query: str = "",
    paper_keys: list[str] | None = None,
) -> dict[str, object]:
    effective_keyword_query = keyword_query.strip() or q.strip()
    return {
        "source": source,
        "paper_keys": ",".join(paper_keys or []),
        "tags": ",".join(tags),
        "tag_match": tag_match,
        "semantic_query": semantic_query.strip(),
        "keyword_query": effective_keyword_query,
        "search_mode": search_mode,
        "scope_semantic_query": scope_semantic_query.strip(),
        "date": date_filter.isoformat() if date_filter is not None else None,
        "limit": limit,
        "offset": offset,
        "include_total": "true" if include_total else "",
    }


def clean_unique_strings(values: list[str]) -> list[str]:
    return [
        value
        for value in dict.fromkeys(value.strip() for value in values)
        if value
    ]


def clean_login_values(payload: LoginRequest) -> tuple[str, str]:
    username = payload.username.strip()
    email = payload.email.strip().lower()

    if not username:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Username is required")
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Enter a valid email address")

    return username, email


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


def user_recommendation_query(cur: psycopg.Cursor, user_id: str) -> str:
    profile = get_profile(cur, user_id)
    values = [*profile["tags"], *profile["authors"]]
    return " ".join(clean_unique_strings(values))


def article_key(article: dict) -> str:
    return str(article.get("paper_key") or article.get("id") or "").strip()


def cleanup_expired_user_daily_feeds(cur: psycopg.Cursor, feed_date: date) -> None:
    cur.execute("DELETE FROM user_daily_feed WHERE feed_date < %s", [feed_date])


def invalidate_user_daily_feed(cur: psycopg.Cursor, user_id: str) -> None:
    cur.execute("DELETE FROM user_daily_feed WHERE user_id = %s", [user_id])


def get_stored_daily_feed_keys(cur: psycopg.Cursor, user_id: str, feed_date: date) -> list[str]:
    cur.execute(
        """
        SELECT article_key
        FROM user_daily_feed
        WHERE user_id = %s
          AND feed_date = %s
        ORDER BY rank ASC
        """,
        [user_id, feed_date],
    )
    return [row["article_key"] for row in cur.fetchall()]


def store_daily_feed_keys(
    cur: psycopg.Cursor,
    user_id: str,
    feed_date: date,
    article_keys: list[str],
) -> list[str]:
    unique_keys = list(dict.fromkeys(key for key in article_keys if key))
    cur.execute(
        "DELETE FROM user_daily_feed WHERE user_id = %s AND feed_date = %s",
        [user_id, feed_date],
    )
    if unique_keys:
        cur.executemany(
            """
            INSERT INTO user_daily_feed (user_id, feed_date, article_key, rank)
            VALUES (%s, %s, %s, %s)
            """,
            [
                (user_id, feed_date, key, index + 1)
                for index, key in enumerate(unique_keys)
            ],
        )
    return unique_keys


def generate_daily_feed_keys(recommendation_query: str) -> list[str]:
    query = recommendation_query.strip()
    if not query:
        return []

    keys: list[str] = []
    offset = 0
    while len(keys) < USER_DAILY_FEED_SIZE:
        page_limit = min(ARTICLE_SERVICE_PAGE_SIZE, USER_DAILY_FEED_SIZE - len(keys))
        page = article_service_get(
            "/articles",
            {
                "source": "all",
                "semantic_query": query,
                "search_mode": "semantic",
                "limit": page_limit,
                "offset": offset,
            },
        )
        if not isinstance(page, list) or not page:
            break

        keys.extend(article_key(article) for article in page)
        offset += len(page)
        if len(page) < page_limit:
            break

    return list(dict.fromkeys(key for key in keys if key))


def ensure_user_daily_feed(
    cur: psycopg.Cursor,
    user_id: str,
    recommendation_query: str,
    feed_date: date,
) -> list[str]:
    cleanup_expired_user_daily_feeds(cur, feed_date)
    stored_keys = get_stored_daily_feed_keys(cur, user_id, feed_date)
    if stored_keys:
        return stored_keys

    generated_keys = generate_daily_feed_keys(recommendation_query)
    return store_daily_feed_keys(cur, user_id, feed_date, generated_keys)


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
                COUNT(ut.user_id)::int AS count
            FROM tags t
            LEFT JOIN user_tags ut ON ut.tag_id = t.id
            GROUP BY t.id, t.name
            ORDER BY t.name ASC
            """
        )
        return list(cur.fetchall())


@app.get("/sources")
def get_sources() -> list[str]:
    return article_service_get("/sources", {})  # type: ignore[return-value]


@app.get("/articles")
def get_articles(
    tags: str | None = None,
    match: MatchMode = "or",
    source: str = "all",
    q: str = "",
    semantic_query: str = "",
    keyword_query: str = "",
    search_mode: str = "auto",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    include_total: bool = False,
    date_filter: date | None = Query(default=None, alias="date"),
) -> list[dict] | dict:
    selected_tags = parse_tags(tags)
    params = article_service_params(
        source=source,
        tags=selected_tags,
        tag_match=match,
        q=q,
        semantic_query=semantic_query,
        keyword_query=keyword_query,
        search_mode=search_mode,
        date_filter=date_filter,
        limit=limit,
        offset=offset,
        include_total=include_total,
    )
    return article_service_get("/articles", params)  # type: ignore[return-value]


@app.get("/users/{user_id}/feed")
def get_user_feed(
    user_id: str,
    request: Request,
    conn: Annotated[psycopg.Connection, Depends(get_db)],
    tags: str | None = None,
    match: MatchMode = "or",
    source: str = "all",
    q: str = "",
    semantic_query: str = "",
    keyword_query: str = "",
    search_mode: str = "auto",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    include_total: bool = False,
    date_filter: date | None = Query(default=None, alias="date"),
) -> list[dict] | dict:
    with conn.cursor() as cur:
        require_user_session(request, cur, user_id)
        recommendation_query = user_recommendation_query(cur, user_id)
        feed_date = date_filter or date.today()
        feed_keys = ensure_user_daily_feed(cur, user_id, recommendation_query, feed_date)

    if not feed_keys:
        empty_response: dict[str, object] = {"items": [], "total": 0}
        return empty_response if include_total else []

    selected_tags = parse_tags(tags)
    explicit_query = bool(semantic_query.strip() or keyword_query.strip() or q.strip())
    effective_semantic_query = semantic_query.strip()
    if not explicit_query:
        search_mode = "none"

    params = article_service_params(
        source=source,
        tags=selected_tags,
        tag_match=match,
        q=q,
        semantic_query=effective_semantic_query,
        keyword_query=keyword_query,
        search_mode=search_mode,
        date_filter=date_filter,
        limit=limit,
        offset=offset,
        include_total=include_total,
        paper_keys=feed_keys,
    )
    return article_service_get("/articles", params)  # type: ignore[return-value]


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
    username, email = clean_login_values(payload)

    with conn.cursor() as cur:
        cur.execute("SELECT id, email, username FROM users WHERE email = %s", [email])
        user = cur.fetchone()

        if payload.create_account:
            if user is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="An account already exists for this email. Log in instead.",
                )

            user_id = "user-" + secrets.token_hex(8)
            cur.execute(
                """
                INSERT INTO users (id, email, username)
                VALUES (%s, %s, %s)
                RETURNING id, email, username
                """,
                [user_id, email, username],
            )
            user = cur.fetchone()
        elif user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No matching email and username pair exists.",
            )
        elif user["username"] != username:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Username does not match this email.",
            )

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
        invalidate_user_daily_feed(cur, user_id)

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
        if payload.tags is not None or payload.authors is not None:
            invalidate_user_daily_feed(cur, user_id)

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
