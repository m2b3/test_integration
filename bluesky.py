"""
Fetch recent Bluesky posts for scientist accounts, journal accounts, or keyword mentions.

Required command format:
  python bluesky.py - scientist - "Nicole Rust" - days 1
  python bluesky.py - scientist - "Nicole Rust" - days 30
  python bluesky.py - journal - "Nature" - days 1
  python bluesky.py - journal - "Nature" - days 30
"""

from __future__ import annotations
import argparse
import json
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
import requests

API_BASE_URL = "https://public.api.bsky.app"
SEARCH_ACTORS_PATH = "/xrpc/app.bsky.actor.searchActors"
AUTHOR_FEED_PATH = "/xrpc/app.bsky.feed.getAuthorFeed"
SEARCH_POSTS_PATH = "/xrpc/app.bsky.feed.searchPosts"
USER_AGENT = "bluesky-scicomm-ingester/0.1"
SEARCH_POSTS_AUTH_MESSAGE = (
    "Bluesky searchPosts returned 401/403. This endpoint may require authentication in some situations. "
    "Login is not implemented in this version."
)
RATE_LIMIT_MESSAGE = "Rate limited by Bluesky API. Please wait and try again."

class UsageError(ValueError):
    pass

class BlueskyHTTPError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code

@dataclass
class CliArgs:
    mode: str
    query: str
    days: int
    journal_mode: str
    max_posts: int
    limit: int
    out_dir: Path


def print_usage(file: Any = sys.stdout) -> None:
    print(
        """Usage:
  python bluesky.py - scientist - "Nicole Rust" - days 1
  python bluesky.py - scientist - "Nicole Rust" - days 30
  python bluesky.py - journal - "Nature" - days 1
  python bluesky.py - journal - "Nature" - days 30

Optional:
  --journal-mode auto|account|keyword
  --max-posts 500
  --limit 100
  --out-dir data""",
        file=file,
    )


def parse_args(argv: list[str] | None = None) -> CliArgs:
    argv = list(sys.argv if argv is None else argv)
    if "--help" in argv or "-h" in argv:
        print_usage()
        raise SystemExit(0)

    if len(argv) < 8:
        raise UsageError("Invalid command: expected `python bluesky.py - <mode> - <query> - days <N>`.")
    if argv[1] != "-":
        raise UsageError('Invalid command: argv[1] must be "-".')

    mode = argv[2].strip().lower()
    if mode not in {"scientist", "journal"}:
        raise UsageError('Invalid mode: must be "scientist" or "journal".')
    if argv[3] != "-":
        raise UsageError('Invalid command: argv[3] must be "-".')

    query = argv[4].strip()
    if not query:
        raise UsageError("Invalid query: query cannot be empty.")
    if argv[5] != "-":
        raise UsageError('Invalid command: argv[5] must be "-".')
    if argv[6] != "days":
        raise UsageError('Invalid command: argv[6] must be the literal word "days".')

    try:
        days = int(argv[7])
    except ValueError as exc:
        raise UsageError("Invalid days value: must be a positive integer.") from exc
    if days <= 0:
        raise UsageError("Invalid days value: must be a positive integer.")

    optional_parser = argparse.ArgumentParser(add_help=False)
    optional_parser.add_argument("--journal-mode", choices=("auto", "account", "keyword"), default="auto")
    optional_parser.add_argument("--max-posts", type=int, default=500)
    optional_parser.add_argument("--limit", type=int, default=100)
    optional_parser.add_argument("--out-dir", default=".")
    try:
        optional_args = optional_parser.parse_args(argv[8:])
    except SystemExit as exc:
        raise UsageError("Invalid optional arguments.") from exc

    if optional_args.max_posts <= 0:
        raise UsageError("--max-posts must be a positive integer.")
    if optional_args.limit <= 0:
        raise UsageError("--limit must be a positive integer.")

    return CliArgs(
        mode=mode,
        query=query,
        days=days,
        journal_mode=optional_args.journal_mode,
        max_posts=optional_args.max_posts,
        limit=min(optional_args.limit, 100),
        out_dir=Path(optional_args.out_dir),
    )


def safe_filename(text: str) -> str:
    safe = (text or "").strip().replace(" ", "_")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "", safe)
    safe = re.sub(r"_+", "_", safe).strip("._-")
    safe = safe[:80].strip("._-")
    return safe or "bluesky_output"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def isoformat_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def bluesky_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    url = f"{API_BASE_URL}{path}"
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
    except requests.RequestException as exc:
        raise RuntimeError(f"Bluesky API request failed: {type(exc).__name__}: {exc}") from exc

    if response.status_code == 429:
        raise BlueskyHTTPError(429, RATE_LIMIT_MESSAGE)
    if response.status_code >= 400:
        preview = " ".join(response.text.split())[:500]
        message = f"Bluesky API returned HTTP {response.status_code}"
        if preview:
            message = f"{message}: {preview}"
        raise BlueskyHTTPError(response.status_code, message)

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Bluesky API returned malformed JSON.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Bluesky API returned an unexpected response shape.")
    return payload


def search_actors(query: str, limit: int = 10) -> list[dict[str, Any]]:
    data = bluesky_get(SEARCH_ACTORS_PATH, {"q": query, "limit": limit})
    actors = data.get("actors", [])
    if not isinstance(actors, list):
        raise RuntimeError("Bluesky actor search returned malformed actors.")
    return [actor for actor in actors if isinstance(actor, dict)]


def normalize_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").casefold())


def normalize_compact(text: str) -> str:
    return "".join(normalize_tokens(text))


def actor_followers(actor: dict[str, Any]) -> int:
    value = actor.get("followersCount", actor.get("followerCount", 0))
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def actor_summary(actor: dict[str, Any], score: float | None = None) -> dict[str, Any]:
    out = {
        "did": actor.get("did"),
        "handle": actor.get("handle"),
        "displayName": actor.get("displayName"),
        "description": actor.get("description"),
        "followersCount": actor_followers(actor),
    }
    if score is not None:
        out["score"] = score
    return out


def score_actor_for_person(actor: dict[str, Any], query: str) -> float:
    query_tokens = normalize_tokens(query)
    query_compact = normalize_compact(query)
    display = str(actor.get("displayName") or "")
    display_tokens = normalize_tokens(display)
    display_compact = normalize_compact(display)
    handle_compact = normalize_compact(str(actor.get("handle") or ""))
    description_tokens = normalize_tokens(str(actor.get("description") or ""))

    score = 0.0
    if display_compact == query_compact:
        score += 100
    elif query_compact and query_compact in display_compact:
        score += 70
    if query_tokens and all(token in display_tokens for token in query_tokens):
        score += 35
    if query_tokens and all(token in handle_compact for token in query_tokens):
        score += 25
    if query_tokens and all(token in description_tokens for token in query_tokens):
        score += 15
    score += min(actor_followers(actor) / 5000, 15)
    return score


def score_official_journal_account(actor: dict[str, Any], query: str) -> float:
    query_tokens = normalize_tokens(query)
    query_compact = normalize_compact(query)
    display = str(actor.get("displayName") or "")
    display_compact = normalize_compact(display)
    handle = str(actor.get("handle") or "")
    handle_compact = normalize_compact(handle)
    description = str(actor.get("description") or "")
    description_tokens = normalize_tokens(description)
    description_text = " ".join(description_tokens)

    score = 0.0
    if display_compact == query_compact:
        score += 70
    elif query_compact and query_compact in display_compact:
        score += 45
    if query_compact and query_compact in handle_compact:
        score += 35
    if query_tokens and all(token in description_tokens for token in query_tokens):
        score += 25

    official_words = {
        "official",
        "journal",
        "magazine",
        "publisher",
        "science",
        "research",
        "nature",
        "news",
        "association",
        "society",
        "conference",
        "meeting",
        "proceedings",
    }
    for word in official_words:
        if word in description_tokens:
            score += 6

    institutional_words = (
        "journal",
        "news",
        "science",
        "research",
        "press",
        "magazine",
        "nature",
        "cell",
        "lancet",
        "nejm",
        "bmj",
        "oup",
        "wiley",
        "springer",
        "association",
        "society",
        "conference",
        "meeting",
        "proceedings",
        "acl",
    )
    if any(word in handle_compact for word in institutional_words):
        score += 10
    personal_signals = ("personal account", "opinions my own", "my views", "my own")
    if not any(word in description_text for word in personal_signals):
        score += 5
    else:
        score -= 20
    score += min(actor_followers(actor) / 10000, 20)
    return score


def select_best_actor(
    actors: list[dict[str, Any]],
    query: str,
    scorer: Callable[[dict[str, Any], str], float],
    min_score: float | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    scored = [(actor, scorer(actor, query)) for actor in actors]
    scored.sort(key=lambda item: (item[1], actor_followers(item[0])), reverse=True)
    candidates = [actor_summary(actor, score) for actor, score in scored]
    if not scored:
        return None, candidates
    best_actor, best_score = scored[0]
    if min_score is not None and best_score < min_score:
        return None, candidates
    return best_actor, candidates


def print_actor_candidates(candidates: list[dict[str, Any]], limit: int = 5) -> None:
    if not candidates:
        print("No actor candidates found.")
        return
    print("Top actor candidates:")
    for index, actor in enumerate(candidates[:limit], start=1):
        display = actor.get("displayName") or ""
        handle = actor.get("handle") or ""
        followers = actor.get("followersCount") or 0
        score = actor.get("score")
        score_text = f" score={score:.1f}" if isinstance(score, (int, float)) else ""
        description = str(actor.get("description") or "").replace("\n", " ")
        if len(description) > 120:
            description = f"{description[:117]}..."
        print(f"  {index}. @{handle} display={display!r} followers={followers}{score_text} description={description!r}")


def fetch_author_feed(
    actor: str,
    since_utc: datetime,
    max_posts: int,
    limit: int,
) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    cursor: str | None = None
    pages = 0
    max_pages = max(1, (max_posts // max(1, limit)) + 20)

    while len(posts) < max_posts and pages < max_pages:
        params: dict[str, Any] = {"actor": actor, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        try:
            data = bluesky_get(AUTHOR_FEED_PATH, params)
        except BlueskyHTTPError as exc:
            if exc.status_code == 429:
                print(RATE_LIMIT_MESSAGE, file=sys.stderr)
                break
            raise

        feed_items = data.get("feed", [])
        if not isinstance(feed_items, list):
            raise RuntimeError("Bluesky author feed returned malformed feed data.")
        if not feed_items:
            break

        pages += 1
        saw_recent_or_unknown = False
        for item in feed_items:
            if not isinstance(item, dict):
                continue
            post_view = item.get("post")
            if not isinstance(post_view, dict):
                continue
            created_at = parse_datetime((post_view.get("record") or {}).get("createdAt"))
            if created_at is None:
                print(f"[warn] keeping post with unparseable createdAt: {post_view.get('uri')}", file=sys.stderr)
                saw_recent_or_unknown = True
                posts.append(post_view)
            elif created_at >= since_utc:
                saw_recent_or_unknown = True
                posts.append(post_view)
            if len(posts) >= max_posts:
                break

        cursor = data.get("cursor") if isinstance(data.get("cursor"), str) else None
        if not cursor or not saw_recent_or_unknown:
            break
        time.sleep(0.2)

    return posts[:max_posts]


def search_posts(
    query: str,
    since_utc: datetime,
    max_posts: int,
    limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    posts: list[dict[str, Any]] = []
    cursor: str | None = None
    auth_limited = False
    pages = 0
    max_pages = max(1, (max_posts // max(1, limit)) + 20)

    while len(posts) < max_posts and pages < max_pages:
        params: dict[str, Any] = {
            "q": query,
            "since": isoformat_z(since_utc),
            "limit": limit,
            "sort": "latest",
        }
        if cursor:
            params["cursor"] = cursor
        try:
            data = bluesky_get(SEARCH_POSTS_PATH, params)
        except BlueskyHTTPError as exc:
            if exc.status_code in {401, 403}:
                print(SEARCH_POSTS_AUTH_MESSAGE, file=sys.stderr)
                auth_limited = True
                break
            if exc.status_code == 429:
                print(RATE_LIMIT_MESSAGE, file=sys.stderr)
                break
            raise

        page_posts = data.get("posts", [])
        if not isinstance(page_posts, list):
            raise RuntimeError("Bluesky searchPosts returned malformed posts data.")
        if not page_posts:
            break

        pages += 1
        for post_view in page_posts:
            if isinstance(post_view, dict):
                posts.append(post_view)
            if len(posts) >= max_posts:
                break

        cursor = data.get("cursor") if isinstance(data.get("cursor"), str) else None
        if not cursor:
            break
        time.sleep(0.2)

    return posts[:max_posts], auth_limited


def extract_rkey_from_uri(uri: str | None) -> str | None:
    if not uri:
        return None
    parts = str(uri).split("/")
    return parts[-1] if parts else None


def make_bsky_url(author_handle: str | None, uri: str | None) -> str | None:
    rkey = extract_rkey_from_uri(uri)
    if not author_handle or not rkey:
        return None
    return f"https://bsky.app/profile/{author_handle}/post/{rkey}"


def normalize_post(
    post_view: dict[str, Any],
    mode: str,
    result_source: str,
    query: str,
    selected_actor: dict[str, Any] | None,
) -> dict[str, Any]:
    record = post_view.get("record") if isinstance(post_view.get("record"), dict) else {}
    author = post_view.get("author") if isinstance(post_view.get("author"), dict) else {}
    author_handle = author.get("handle")
    uri = post_view.get("uri")
    selected_actor = selected_actor or {}

    return {
        "uri": uri,
        "cid": post_view.get("cid"),
        "mode": mode,
        "result_source": result_source,
        "source_query": query,
        "selected_actor_handle": selected_actor.get("handle"),
        "selected_actor_display_name": selected_actor.get("displayName"),
        "selected_actor_did": selected_actor.get("did"),
        "author_handle": author_handle,
        "author_display_name": author.get("displayName"),
        "author_did": author.get("did"),
        "text": record.get("text"),
        "created_at": record.get("createdAt"),
        "indexed_at": post_view.get("indexedAt"),
        "like_count": post_view.get("likeCount"),
        "repost_count": post_view.get("repostCount"),
        "reply_count": post_view.get("replyCount"),
        "quote_count": post_view.get("quoteCount"),
        "url": make_bsky_url(author_handle, uri),
        "raw": post_view,
    }


def save_json(
    path: Path,
    *,
    mode: str,
    journal_mode: str | None,
    result_source: str,
    query: str,
    days: int,
    since: datetime,
    selected_actor: dict[str, Any] | None,
    candidate_actors: list[dict[str, Any]],
    posts: list[dict[str, Any]],
) -> None:
    payload = {
        "mode": mode,
        "journal_mode": journal_mode,
        "result_source": result_source,
        "query": query,
        "days": days,
        "since": isoformat_z(since),
        "fetched_at": isoformat_z(utc_now()),
        "selected_actor": selected_actor,
        "candidate_actors": candidate_actors,
        "count": len(posts),
        "posts": posts,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def save_sqlite(path: Path, posts: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bluesky_posts (
                uri TEXT PRIMARY KEY,
                cid TEXT,
                mode TEXT,
                result_source TEXT,
                source_query TEXT,
                selected_actor_handle TEXT,
                selected_actor_display_name TEXT,
                selected_actor_did TEXT,
                author_handle TEXT,
                author_display_name TEXT,
                author_did TEXT,
                text TEXT,
                created_at TEXT,
                indexed_at TEXT,
                like_count INTEGER,
                repost_count INTEGER,
                reply_count INTEGER,
                quote_count INTEGER,
                url TEXT,
                raw_json TEXT
            );
            """
        )
        inserted = 0
        for post in posts:
            if not post.get("uri"):
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO bluesky_posts
                (
                    uri, cid, mode, result_source, source_query,
                    selected_actor_handle, selected_actor_display_name, selected_actor_did,
                    author_handle, author_display_name, author_did, text, created_at, indexed_at,
                    like_count, repost_count, reply_count, quote_count, url, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    post.get("uri"),
                    post.get("cid"),
                    post.get("mode"),
                    post.get("result_source"),
                    post.get("source_query"),
                    post.get("selected_actor_handle"),
                    post.get("selected_actor_display_name"),
                    post.get("selected_actor_did"),
                    post.get("author_handle"),
                    post.get("author_display_name"),
                    post.get("author_did"),
                    post.get("text"),
                    post.get("created_at"),
                    post.get("indexed_at"),
                    post.get("like_count"),
                    post.get("repost_count"),
                    post.get("reply_count"),
                    post.get("quote_count"),
                    post.get("url"),
                    json.dumps(post.get("raw", post), ensure_ascii=False),
                ),
            )
            inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


def filter_posts_by_since(posts: list[dict[str, Any]], since_utc: datetime) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for post in posts:
        created_at = parse_datetime(post.get("created_at"))
        if created_at is None:
            print(f"[warn] keeping normalized post with unparseable created_at: {post.get('uri')}", file=sys.stderr)
            filtered.append(post)
        elif created_at >= since_utc:
            filtered.append(post)
    return filtered


def run_scientist(args: CliArgs, since_utc: datetime) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    actors = search_actors(args.query, limit=10)
    selected_actor, candidates = select_best_actor(actors, args.query, score_actor_for_person)
    print_actor_candidates(candidates)
    if selected_actor is None:
        raise RuntimeError(f"No Bluesky actor found for scientist query: {args.query}")

    print(
        "Selected account: "
        f"@{selected_actor.get('handle')} display={selected_actor.get('displayName')!r} did={selected_actor.get('did')}"
    )
    actor_ref = str(selected_actor.get("did") or selected_actor.get("handle") or "")
    post_views = fetch_author_feed(actor_ref, since_utc, args.max_posts, args.limit)
    normalized = [
        normalize_post(post, args.mode, "scientist_account", args.query, selected_actor)
        for post in post_views
    ]
    return "scientist_account", actor_summary(selected_actor), candidates, filter_posts_by_since(normalized, since_utc)


def run_journal(args: CliArgs, since_utc: datetime) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    if args.journal_mode == "keyword":
        post_views, _auth_limited = search_posts(args.query, since_utc, args.max_posts, args.limit)
        normalized = [
            normalize_post(post, args.mode, "journal_keyword", args.query, None)
            for post in post_views
        ]
        return "journal_keyword", None, [], filter_posts_by_since(normalized, since_utc)

    actors = search_actors(args.query, limit=10)
    selected_actor, candidates = select_best_actor(
        actors,
        args.query,
        score_official_journal_account,
        min_score=40,
    )
    print_actor_candidates(candidates)

    if selected_actor is None:
        raise RuntimeError(
            f"No likely official journal account found for query: {args.query}. "
            "Journal mode only fetches posts from an official-looking account unless "
            "--journal-mode keyword is explicitly provided."
        )

    print(
        "Selected account: "
        f"@{selected_actor.get('handle')} display={selected_actor.get('displayName')!r} did={selected_actor.get('did')}"
    )
    actor_ref = str(selected_actor.get("did") or selected_actor.get("handle") or "")
    post_views = fetch_author_feed(actor_ref, since_utc, args.max_posts, args.limit)
    normalized = [
        normalize_post(post, args.mode, "journal_account", args.query, selected_actor)
        for post in post_views
    ]
    return "journal_account", actor_summary(selected_actor), candidates, filter_posts_by_since(normalized, since_utc)


def main() -> int:
    try:
        args = parse_args()
    except UsageError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        print_usage(file=sys.stderr)
        return 2

    now = utc_now()
    since_utc = now - timedelta(days=args.days)
    output_base = safe_filename(args.query)
    sqlite_path = args.out_dir / f"{output_base}.sqlite"
    json_path = args.out_dir / f"{output_base}.json"

    try:
        if args.mode == "scientist":
            result_source, selected_actor, candidates, posts = run_scientist(args, since_utc)
            journal_mode: str | None = None
        else:
            result_source, selected_actor, candidates, posts = run_journal(args, since_utc)
            journal_mode = args.journal_mode

        saved_count = save_sqlite(sqlite_path, posts)
        save_json(
            json_path,
            mode=args.mode,
            journal_mode=journal_mode,
            result_source=result_source,
            query=args.query,
            days=args.days,
            since=since_utc,
            selected_actor=selected_actor,
            candidate_actors=candidates,
            posts=posts,
        )

        print("Summary:")
        print(f"  mode: {args.mode}")
        print(f"  query: {args.query}")
        print(f"  days: {args.days}")
        if args.mode == "journal":
            print(f"  journal_mode: {args.journal_mode}")
        print(f"  result_source: {result_source}")
        if selected_actor:
            print(
                "  selected_actor: "
                f"@{selected_actor.get('handle')} display={selected_actor.get('displayName')!r} "
                f"did={selected_actor.get('did')}"
            )
        print(f"  posts_saved: {saved_count}")
        print(f"  sqlite: {sqlite_path}")
        print(f"  json: {json_path}")
        return 0
    except BlueskyHTTPError as exc:
        if exc.status_code == 429:
            print(RATE_LIMIT_MESSAGE, file=sys.stderr)
        else:
            print(f"[error] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("[warn] Interrupted by user.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
