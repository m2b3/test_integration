"""
Fetch recent public Mastodon statuses for scientist/person names or journal names.

Required command format:
  python mastodon.py - <mode> - <query> - days <N> [options]

Recommended scientist/person format when the Mastodon account is known:
  python mastodon.py - scientist - "Terence Tao" - days 10 --account @tao@mathstodon.xyz
  python mastodon.py - scientist - "Satrevik" - days 10 --account @satrevik@fediscience.org

Search-based formats:
  python mastodon.py - scientist - "Nicole Rust" - days 1
  python mastodon.py - journal - "Nature" - days 1
  python mastodon.py - journal - "Nature" - days 30
"""

from __future__ import annotations
import argparse
import html
from html.parser import HTMLParser
import json
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse
import requests

DEFAULT_INSTANCES = [
    "mastodon.social",
    "mstdn.social",
    "fediscience.org",
    "scholar.social",
    "mathstodon.xyz",
    "mstdn.science",
    "scicomm.xyz",
]
USER_AGENT = "mastodon-scicomm-ingester/0.1"
PERSON_ACCOUNT_THRESHOLD = 55.0
JOURNAL_ACCOUNT_THRESHOLD = 50.0


class UsageError(ValueError):
    pass


class MastodonHTTPError(RuntimeError):
    def __init__(self, instance: str, status_code: int, message: str) -> None:
        super().__init__(message)
        self.instance = instance
        self.status_code = status_code


class MastodonTimeoutError(RuntimeError):
    def __init__(self, instance: str) -> None:
        super().__init__(f"Timeout from {instance}. Skipping.")
        self.instance = instance


@dataclass
class CliArgs:
    mode: str
    query: str
    days: int
    instances: Path
    account: str | None
    journal_mode: str
    max_posts: int
    max_accounts: int
    limit: int
    out_dir: Path
    timeout: int


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"br", "p", "div", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"p", "div", "li"}:
            self.parts.append(" ")

    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self.parts)).strip()


def print_usage(file: Any = sys.stdout) -> None:
    print(
        """Usage:
  python mastodon.py - <mode> - <query> - days <N> [options]

Recommended scientist/person account format:
  python mastodon.py - scientist - "Terence Tao" - days 10 --account @tao@mathstodon.xyz
  python mastodon.py - scientist - "Satrevik" - days 10 --account @satrevik@fediscience.org

Search-based examples:
  python mastodon.py - scientist - "Nicole Rust" - days 1
  python mastodon.py - journal - "Nature" - days 1
  python mastodon.py - journal - "Nature" - days 30

Optional:
  --instances mastodon_instances.json
  --account @tao@mathstodon.xyz
  --journal-mode auto|account|keyword
  --max-posts 500
  --max-accounts 5
  --limit 40
  --out-dir data
  --timeout 15""",
        file=file,
    )


def parse_args(argv: list[str] | None = None) -> CliArgs:
    argv = list(sys.argv if argv is None else argv)
    if "--help" in argv or "-h" in argv:
        print_usage()
        raise SystemExit(0)

    if len(argv) < 8:
        raise UsageError("Invalid command: expected `python mastodon.py - <mode> - <query> - days <N>`.")
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
    optional_parser.add_argument("--instances", default="mastodon_instances.json")
    optional_parser.add_argument("--account")
    optional_parser.add_argument("--journal-mode", choices=("auto", "account", "keyword"), default="auto")
    optional_parser.add_argument("--max-posts", type=int, default=500)
    optional_parser.add_argument("--max-accounts", type=int, default=5)
    optional_parser.add_argument("--limit", type=int, default=40)
    optional_parser.add_argument("--out-dir", default=".")
    optional_parser.add_argument("--timeout", type=int, default=15)
    try:
        optional_args = optional_parser.parse_args(argv[8:])
    except SystemExit as exc:
        raise UsageError("Invalid optional arguments.") from exc

    if optional_args.max_posts <= 0:
        raise UsageError("--max-posts must be a positive integer.")
    if optional_args.max_accounts <= 0:
        raise UsageError("--max-accounts must be a positive integer.")
    if optional_args.limit <= 0:
        raise UsageError("--limit must be a positive integer.")
    if optional_args.timeout <= 0:
        raise UsageError("--timeout must be a positive integer.")
    if optional_args.account and mode == "journal" and optional_args.journal_mode == "keyword":
        raise UsageError("--account cannot be used with --journal-mode keyword.")

    return CliArgs(
        mode=mode,
        query=query,
        days=days,
        instances=Path(optional_args.instances),
        account=optional_args.account,
        journal_mode=optional_args.journal_mode,
        max_posts=optional_args.max_posts,
        max_accounts=optional_args.max_accounts,
        limit=min(optional_args.limit, 80),
        out_dir=Path(optional_args.out_dir),
        timeout=optional_args.timeout,
    )


def normalize_instance(instance: str) -> str:
    value = str(instance or "").strip()
    value = re.sub(r"^https?://", "", value, flags=re.IGNORECASE)
    value = value.split("/", 1)[0].strip().rstrip("/")
    return value


def parse_account_spec(account: str) -> tuple[str, str]:
    value = str(account or "").strip()
    if value.startswith("@"):
        value = value[1:]
    if "@" not in value:
        raise UsageError("Invalid --account value: expected @user@instance or user@instance.")
    username, instance = value.split("@", 1)
    username = username.strip().lstrip("@")
    instance = normalize_instance(instance)
    if not username or not instance:
        raise UsageError("Invalid --account value: expected @user@instance or user@instance.")
    return username, instance


def load_instances(path: str | Path) -> list[str]:
    config_path = Path(path)
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"instances": DEFAULT_INSTANCES}, indent=2) + "\n", encoding="utf-8")

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid instance config: could not read JSON from {config_path}") from exc

    if isinstance(data, dict):
        raw_instances = data.get("instances")
    elif isinstance(data, list):
        raw_instances = data
    else:
        raise ValueError("Invalid instance config: expected an object with 'instances' or a list.")
    if not isinstance(raw_instances, list):
        raise ValueError("Invalid instance config: 'instances' must be a list.")

    seen: set[str] = set()
    instances: list[str] = []
    for raw in raw_instances:
        if not isinstance(raw, str):
            continue
        instance = normalize_instance(raw)
        if instance and instance not in seen:
            seen.add(instance)
            instances.append(instance)
    if not instances:
        raise ValueError("Invalid instance config: no usable Mastodon instances found.")
    return instances


def safe_filename(text: str) -> str:
    safe = (text or "").strip().replace(" ", "_")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "", safe)
    safe = re.sub(r"_+", "_", safe).strip("._-")
    safe = safe[:80].strip("._-")
    return safe or "mastodon_output"


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


def mastodon_get(
    instance: str,
    path_or_url: str,
    params: dict[str, Any] | None = None,
    timeout: int = 15,
) -> tuple[Any, dict[str, str]]:
    url = path_or_url if path_or_url.startswith("http") else f"https://{instance}{path_or_url}"
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=timeout)
    except requests.Timeout as exc:
        raise MastodonTimeoutError(instance) from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"Request failed for {instance}: {type(exc).__name__}: {exc}") from exc

    if response.status_code == 429:
        raise MastodonHTTPError(instance, 429, f"Rate limited by {instance}. Skipping this instance for now.")
    if response.status_code >= 400:
        preview = " ".join(response.text.split())[:300]
        message = f"HTTP {response.status_code} from {instance}"
        if preview:
            message = f"{message}: {preview}"
        raise MastodonHTTPError(instance, response.status_code, message)

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Malformed JSON from {instance}.") from exc
    return payload, dict(response.headers)


def extract_account_search_results(instance: str, data: Any, endpoint: str) -> list[dict[str, Any]]:
    if endpoint == "v2":
        accounts = data.get("accounts") if isinstance(data, dict) else None
    else:
        accounts = data if isinstance(data, list) else None
    if not isinstance(accounts, list):
        print(f"Malformed account search response from {instance} on /api/{endpoint}. Skipping.", file=sys.stderr)
        return []

    out: list[dict[str, Any]] = []
    for account in accounts:
        if isinstance(account, dict):
            copy = dict(account)
            copy["_source_instance"] = instance
            copy["_search_endpoint"] = endpoint
            out.append(copy)
    return out


def lookup_account_on_instance(instance: str, username: str, timeout: int) -> dict[str, Any] | None:
    params = {"acct": username}
    try:
        data, _headers = mastodon_get(instance, "/api/v1/accounts/lookup", params=params, timeout=timeout)
    except MastodonTimeoutError:
        print(f"Timeout from {instance}. Skipping direct account lookup.", file=sys.stderr)
        return None
    except MastodonHTTPError as exc:
        if exc.status_code in {401, 403}:
            print(f"Direct account lookup blocked by {instance} (HTTP {exc.status_code}).", file=sys.stderr)
        elif exc.status_code == 404:
            print(f"Direct account not found on {instance}: @{username}@{instance}", file=sys.stderr)
        elif exc.status_code == 429:
            print(f"Rate limited by {instance}. Skipping this instance for now.", file=sys.stderr)
        else:
            print(f"{exc}. Skipping direct account lookup.", file=sys.stderr)
        return None
    except RuntimeError as exc:
        print(f"{exc}. Skipping direct account lookup.", file=sys.stderr)
        return None

    if not isinstance(data, dict):
        print(f"Malformed account lookup response from {instance}.", file=sys.stderr)
        return None
    account = dict(data)
    account["_source_instance"] = instance
    account["_search_endpoint"] = "lookup"
    account["_direct_account"] = True
    if not account.get("acct"):
        account["acct"] = username
    return account


def search_accounts_v1_on_instance(instance: str, query: str, timeout: int) -> list[dict[str, Any]]:
    params = {"q": query, "limit": 10, "resolve": "true"}
    try:
        data, _headers = mastodon_get(instance, "/api/v1/accounts/search", params=params, timeout=timeout)
    except MastodonTimeoutError:
        print(f"Timeout from {instance}. Skipping.", file=sys.stderr)
        return []
    except MastodonHTTPError as exc:
        if exc.status_code in {401, 403}:
            print(f"Account search blocked by {instance} on /api/v1/accounts/search (HTTP {exc.status_code}). Skipping.", file=sys.stderr)
        elif exc.status_code == 404:
            print(f"Account search endpoint not found on {instance}. Skipping.", file=sys.stderr)
        elif exc.status_code == 429:
            print(f"Rate limited by {instance}. Skipping this instance for now.", file=sys.stderr)
        else:
            print(f"{exc}. Skipping.", file=sys.stderr)
        return []
    except RuntimeError as exc:
        print(f"{exc}. Skipping.", file=sys.stderr)
        return []
    return extract_account_search_results(instance, data, "v1")


def search_accounts_on_instance(instance: str, query: str, timeout: int) -> list[dict[str, Any]]:
    params = {"q": query, "type": "accounts", "limit": 10, "resolve": "true"}
    try:
        data, _headers = mastodon_get(instance, "/api/v2/search", params=params, timeout=timeout)
    except MastodonTimeoutError:
        print(f"Timeout from {instance}. Skipping.", file=sys.stderr)
        return []
    except MastodonHTTPError as exc:
        if exc.status_code in {401, 403}:
            print(
                f"Search blocked by {instance} on /api/v2/search (HTTP {exc.status_code}). "
                "Trying /api/v1/accounts/search.",
                file=sys.stderr,
            )
            return search_accounts_v1_on_instance(instance, query, timeout)
        elif exc.status_code == 404:
            print(f"Search endpoint not found on {instance}. Trying /api/v1/accounts/search.", file=sys.stderr)
            return search_accounts_v1_on_instance(instance, query, timeout)
        elif exc.status_code == 429:
            print(f"Rate limited by {instance}. Skipping this instance for now.", file=sys.stderr)
        else:
            print(f"{exc}. Skipping.", file=sys.stderr)
        return []
    except RuntimeError as exc:
        print(f"{exc}. Skipping.", file=sys.stderr)
        return []

    return extract_account_search_results(instance, data, "v2")


def search_statuses_on_instance(
    instance: str,
    query: str,
    since_utc: datetime,
    max_posts: int,
    limit: int,
    timeout: int,
) -> list[dict[str, Any]]:
    params = {"q": query, "type": "statuses", "limit": limit, "resolve": "false"}
    try:
        data, _headers = mastodon_get(instance, "/api/v2/search", params=params, timeout=timeout)
    except MastodonTimeoutError:
        print(f"Timeout from {instance}. Skipping.", file=sys.stderr)
        return []
    except MastodonHTTPError as exc:
        if exc.status_code in {401, 403}:
            print(f"Keyword search blocked by {instance} (HTTP {exc.status_code}). Skipping.", file=sys.stderr)
        elif exc.status_code == 404:
            print(f"Search endpoint not found on {instance}. Skipping.", file=sys.stderr)
        elif exc.status_code == 429:
            print(f"Rate limited by {instance}. Skipping this instance for now.", file=sys.stderr)
        else:
            print(f"{exc}. Skipping.", file=sys.stderr)
        return []
    except RuntimeError as exc:
        print(f"{exc}. Skipping.", file=sys.stderr)
        return []

    statuses = data.get("statuses") if isinstance(data, dict) else None
    if not isinstance(statuses, list):
        print(f"Malformed status search response from {instance}. Skipping.", file=sys.stderr)
        return []

    out: list[dict[str, Any]] = []
    for status in statuses:
        if not isinstance(status, dict):
            continue
        created_at = parse_datetime(status.get("created_at"))
        if created_at is None:
            print(f"[warn] keeping status with unparseable created_at: {status.get('uri') or status.get('id')}", file=sys.stderr)
        elif created_at < since_utc:
            continue
        copy = dict(status)
        copy["_source_instance"] = instance
        out.append(copy)
        if len(out) >= max_posts:
            break
    return out


def normalize_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").casefold())


def normalize_compact(text: str) -> str:
    return "".join(normalize_tokens(text))


def account_followers(account: dict[str, Any]) -> int:
    try:
        return int(account.get("followers_count") or 0)
    except (TypeError, ValueError):
        return 0


def account_note_text(account: dict[str, Any]) -> str:
    return html_to_text(str(account.get("note") or ""))


def score_account_for_person(account: dict[str, Any], query: str) -> float:
    query_tokens = normalize_tokens(query)
    query_compact = normalize_compact(query)
    display = str(account.get("display_name") or "")
    display_tokens = normalize_tokens(display)
    display_compact = normalize_compact(display)
    username_compact = normalize_compact(str(account.get("username") or ""))
    acct_compact = normalize_compact(str(account.get("acct") or ""))
    note_tokens = normalize_tokens(account_note_text(account))

    score = 0.0
    if display_compact == query_compact:
        score += 90
    elif query_tokens and all(token in display_tokens for token in query_tokens):
        score += 65
    if query_compact and (query_compact in username_compact or query_compact in acct_compact):
        score += 35
    elif query_tokens and all(token in acct_compact for token in query_tokens):
        score += 25
    if query_tokens and all(token in note_tokens for token in query_tokens):
        score += 15

    science_terms = {
        "scientist",
        "professor",
        "researcher",
        "lab",
        "phd",
        "neuroscience",
        "biology",
        "psychology",
        "medicine",
        "data",
        "science",
        "university",
    }
    score += sum(5 for term in science_terms if term in note_tokens)
    score += min(account_followers(account) / 5000, 15)
    if account.get("bot") is True:
        score -= 35
    if account.get("locked") is True:
        score -= 5
    return score


def score_official_journal_account(account: dict[str, Any], query: str) -> float:
    query_tokens = normalize_tokens(query)
    query_compact = normalize_compact(query)
    display = str(account.get("display_name") or "")
    display_compact = normalize_compact(display)
    acct_compact = normalize_compact(str(account.get("acct") or ""))
    username_compact = normalize_compact(str(account.get("username") or ""))
    note = account_note_text(account)
    note_tokens = normalize_tokens(note)
    note_compact = normalize_compact(note)

    score = 0.0
    if display_compact == query_compact:
        score += 75
    elif query_compact and query_compact in display_compact:
        score += 55
    if query_compact and (query_compact in username_compact or query_compact in acct_compact):
        score += 40
    if query_compact and query_compact in note_compact:
        score += 30

    official_terms = {
        "official",
        "journal",
        "magazine",
        "publisher",
        "publishing",
        "research",
        "science",
        "news",
        "editor",
        "editorial",
    }
    publisher_terms = {
        "nature",
        "springer",
        "elsevier",
        "cell",
        "science",
        "lancet",
        "plos",
        "bmj",
        "oxford",
        "cambridge",
    }
    score += sum(6 for term in official_terms if term in note_tokens)
    score += sum(6 for term in publisher_terms if term in note_tokens or term in acct_compact)
    if query_tokens and all(token in note_tokens for token in query_tokens):
        score += 15
    if account.get("bot") is True:
        score -= 30
    score += min(account_followers(account) / 10000, 20)
    return score


def account_key(account: dict[str, Any]) -> str:
    return str(account.get("url") or f"{account.get('_source_instance')}:{account.get('id')}")


def account_summary(account: dict[str, Any], score: float | None = None) -> dict[str, Any]:
    out = {
        "source_instance": account.get("_source_instance"),
        "id": account.get("id"),
        "username": account.get("username"),
        "acct": account.get("acct"),
        "display_name": account.get("display_name"),
        "url": account.get("url"),
        "note": account_note_text(account),
        "followers_count": account_followers(account),
        "locked": bool(account.get("locked")),
        "bot": bool(account.get("bot")),
    }
    if score is not None:
        out["score"] = round(float(score), 3)
    return out


def select_accounts(
    accounts: list[dict[str, Any]],
    query: str,
    scorer: Callable[[dict[str, Any], str], float],
    threshold: float,
    max_accounts: int,
    allow_best_fallback: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    deduped: dict[str, dict[str, Any]] = {}
    for account in accounts:
        deduped.setdefault(account_key(account), account)

    scored = [(account, scorer(account, query)) for account in deduped.values()]
    scored.sort(key=lambda item: (item[1], account_followers(item[0])), reverse=True)
    candidates = [account_summary(account, score) for account, score in scored]
    selected = [account for account, score in scored if score >= threshold][:max_accounts]
    if not selected and allow_best_fallback and scored:
        print("No strong account match found. Using best available candidate.", file=sys.stderr)
        selected = [scored[0][0]]
    return selected, candidates


def print_account_candidates(candidates: list[dict[str, Any]], limit: int = 10) -> None:
    if not candidates:
        print("No account candidates found.")
        return
    print("Top account candidates:")
    for index, account in enumerate(candidates[:limit], start=1):
        score = account.get("score")
        score_text = f"{score:.1f}" if isinstance(score, (int, float)) else "n/a"
        print(
            "  "
            f"{index}. score={score_text} "
            f"source_instance={account.get('source_instance') or ''} "
            f"acct={account.get('acct') or ''} "
            f"display_name={account.get('display_name') or ''!r} "
            f"url={account.get('url') or ''}"
        )


def parse_link_header_next(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        section = part.strip()
        match = re.match(r"<([^>]+)>\s*;\s*(.+)", section)
        if not match:
            continue
        url, attrs = match.groups()
        if re.search(r'rel=["\']?next["\']?', attrs):
            return url
    return None


def fetch_account_statuses(
    account: dict[str, Any],
    since_utc: datetime,
    max_posts: int,
    limit: int,
    timeout: int,
) -> list[dict[str, Any]]:
    source_instance = str(account.get("_source_instance") or "")
    account_id = account.get("id")
    if not source_instance or not account_id:
        return []

    statuses: list[dict[str, Any]] = []
    next_url: str | None = None
    params: dict[str, Any] | None = {
        "limit": limit,
        "exclude_reblogs": "true",
        "exclude_replies": "false",
    }
    pages = 0
    max_pages = max(1, (max_posts // max(1, limit)) + 20)

    while len(statuses) < max_posts and pages < max_pages:
        path_or_url = next_url or f"/api/v1/accounts/{account_id}/statuses"
        if next_url:
            parsed = urlparse(next_url)
            parsed_max_id = parse_qs(parsed.query).get("max_id", [None])[0]
            params = None if parsed_max_id else params
        try:
            data, headers = mastodon_get(source_instance, path_or_url, params=params, timeout=timeout)
        except MastodonTimeoutError:
            print(f"Timeout from {source_instance}. Skipping.", file=sys.stderr)
            break
        except MastodonHTTPError as exc:
            if exc.status_code == 429:
                print(f"Rate limited by {source_instance}. Skipping this instance for now.", file=sys.stderr)
            elif exc.status_code == 404:
                print(f"Statuses endpoint/account not found on {source_instance}. Skipping.", file=sys.stderr)
            else:
                print(f"{exc}. Skipping.", file=sys.stderr)
            break
        except RuntimeError as exc:
            print(f"{exc}. Skipping.", file=sys.stderr)
            break

        if not isinstance(data, list):
            print(f"Malformed statuses response from {source_instance}. Skipping.", file=sys.stderr)
            break
        if not data:
            break

        pages += 1
        saw_recent_or_unknown = False
        for status in data:
            if not isinstance(status, dict):
                continue
            created_at = parse_datetime(status.get("created_at"))
            if created_at is None:
                print(f"[warn] keeping status with unparseable created_at: {status.get('uri') or status.get('id')}", file=sys.stderr)
                saw_recent_or_unknown = True
            elif created_at >= since_utc:
                saw_recent_or_unknown = True
            else:
                continue
            copy = dict(status)
            copy["_source_instance"] = source_instance
            statuses.append(copy)
            if len(statuses) >= max_posts:
                break

        next_url = parse_link_header_next(headers.get("Link") or headers.get("link"))
        if not next_url or not saw_recent_or_unknown:
            break
        time.sleep(0.2)

    return statuses[:max_posts]


def html_to_text(content_html: str) -> str:
    raw = content_html or ""
    try:
        from bs4 import BeautifulSoup  # type: ignore

        return re.sub(r"\s+", " ", BeautifulSoup(raw, "html.parser").get_text(" ")).strip()
    except Exception:
        parser = TextExtractor()
        try:
            parser.feed(raw)
            return html.unescape(parser.text())
        except Exception:
            text = re.sub(r"<[^>]+>", " ", raw)
            return re.sub(r"\s+", " ", html.unescape(text)).strip()


def status_key(status: dict[str, Any]) -> str:
    return str(status.get("uri") or status.get("url") or f"{status.get('_source_instance')}:{status.get('id')}")


def normalize_status(
    status: dict[str, Any],
    mode: str,
    result_source: str,
    query: str,
    selected_account: dict[str, Any] | None = None,
) -> dict[str, Any]:
    account = status.get("account") if isinstance(status.get("account"), dict) else {}
    selected = selected_account or {}
    source_instance = status.get("_source_instance") or selected.get("_source_instance")
    content_html = str(status.get("content") or "")
    created_at = status.get("created_at")
    indexed_at = isoformat_z(utc_now())

    return {
        "uri": status_key(status),
        "platform": "mastodon",
        "source_instance": source_instance,
        "mode": mode,
        "result_source": result_source,
        "source_query": query,
        "selected_account_id": selected.get("id"),
        "selected_account_acct": selected.get("acct"),
        "selected_account_display_name": selected.get("display_name"),
        "selected_account_url": selected.get("url"),
        "account_id": account.get("id"),
        "account_username": account.get("username"),
        "account_display_name": account.get("display_name"),
        "account_acct": account.get("acct"),
        "account_url": account.get("url"),
        "text": html_to_text(content_html),
        "content_html": content_html,
        "created_at": created_at,
        "indexed_at": indexed_at,
        "url": status.get("url"),
        "replies_count": status.get("replies_count"),
        "reblogs_count": status.get("reblogs_count"),
        "favourites_count": status.get("favourites_count"),
        "sensitive": 1 if status.get("sensitive") else 0,
        "spoiler_text": status.get("spoiler_text"),
        "raw": status,
    }


def save_json(
    path: Path,
    *,
    mode: str,
    journal_mode: str,
    result_source: str,
    query: str,
    days: int,
    since_utc: datetime,
    fetched_at: datetime,
    instances: list[str],
    selected_accounts: list[dict[str, Any]],
    candidate_accounts: list[dict[str, Any]],
    posts: list[dict[str, Any]],
) -> None:
    payload = {
        "platform": "mastodon",
        "mode": mode,
        "journal_mode": journal_mode,
        "result_source": result_source,
        "query": query,
        "days": days,
        "since": isoformat_z(since_utc),
        "fetched_at": isoformat_z(fetched_at),
        "instances": instances,
        "selected_accounts": selected_accounts,
        "candidate_accounts": candidate_accounts,
        "count": len(posts),
        "posts": posts,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def save_sqlite(path: Path, posts: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "uri",
        "platform",
        "source_instance",
        "mode",
        "result_source",
        "source_query",
        "selected_account_id",
        "selected_account_acct",
        "selected_account_display_name",
        "selected_account_url",
        "account_id",
        "account_username",
        "account_display_name",
        "account_acct",
        "account_url",
        "text",
        "content_html",
        "created_at",
        "indexed_at",
        "url",
        "replies_count",
        "reblogs_count",
        "favourites_count",
        "sensitive",
        "spoiler_text",
        "raw_json",
    ]
    create_sql = """
CREATE TABLE IF NOT EXISTS mastodon_posts (
    uri TEXT PRIMARY KEY,
    platform TEXT,
    source_instance TEXT,
    mode TEXT,
    result_source TEXT,
    source_query TEXT,
    selected_account_id TEXT,
    selected_account_acct TEXT,
    selected_account_display_name TEXT,
    selected_account_url TEXT,
    account_id TEXT,
    account_username TEXT,
    account_display_name TEXT,
    account_acct TEXT,
    account_url TEXT,
    text TEXT,
    content_html TEXT,
    created_at TEXT,
    indexed_at TEXT,
    url TEXT,
    replies_count INTEGER,
    reblogs_count INTEGER,
    favourites_count INTEGER,
    sensitive INTEGER,
    spoiler_text TEXT,
    raw_json TEXT
);
"""
    placeholders = ", ".join("?" for _ in columns)
    insert_sql = f"INSERT OR REPLACE INTO mastodon_posts ({', '.join(columns)}) VALUES ({placeholders})"
    with sqlite3.connect(path) as conn:
        conn.execute(create_sql)
        for post in posts:
            row = []
            for column in columns:
                if column == "raw_json":
                    row.append(json.dumps(post.get("raw", {}), ensure_ascii=False))
                else:
                    row.append(post.get(column))
            conn.execute(insert_sql, row)
        conn.commit()


def dedupe_statuses(statuses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for status in statuses:
        key = status_key(status)
        if key in seen:
            continue
        seen.add(key)
        out.append(status)
    return out


def collect_account_candidates(instances: list[str], query: str, timeout: int) -> list[dict[str, Any]]:
    accounts: list[dict[str, Any]] = []
    for instance in instances:
        accounts.extend(search_accounts_on_instance(instance, query, timeout))
        time.sleep(0.2)
    return accounts


def collect_status_keyword_results(
    instances: list[str],
    query: str,
    since_utc: datetime,
    max_posts: int,
    limit: int,
    timeout: int,
) -> list[dict[str, Any]]:
    print("Mastodon keyword search is instance-dependent and may not cover the full Fediverse.")
    statuses: list[dict[str, Any]] = []
    for instance in instances:
        remaining = max_posts - len(statuses)
        if remaining <= 0:
            break
        statuses.extend(search_statuses_on_instance(instance, query, since_utc, remaining, limit, timeout))
        statuses = dedupe_statuses(statuses)
        time.sleep(0.2)
    return statuses[:max_posts]


def main() -> int:
    try:
        args = parse_args()
        instances = load_instances(args.instances)
    except (UsageError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        print_usage(sys.stderr)
        return 2

    direct_account: tuple[str, str] | None = None
    if args.account:
        try:
            direct_account = parse_account_spec(args.account)
        except UsageError as exc:
            print(str(exc), file=sys.stderr)
            print_usage(sys.stderr)
            return 2
        direct_instance = direct_account[1]
        if direct_instance not in instances:
            instances.append(direct_instance)

    now_utc = utc_now()
    since_utc = now_utc - timedelta(days=args.days)
    selected_accounts: list[dict[str, Any]] = []
    candidate_accounts: list[dict[str, Any]] = []
    raw_statuses: list[dict[str, Any]] = []
    result_source = "scientist_account" if args.mode == "scientist" else "journal_account"

    if direct_account:
        direct_username, direct_instance = direct_account
        account = lookup_account_on_instance(direct_instance, direct_username, args.timeout)
        if account:
            selected_accounts = [account]
            raw_statuses.extend(fetch_account_statuses(account, since_utc, args.max_posts, args.limit, args.timeout))
        else:
            print(f"No usable direct account found for: @{direct_username}@{direct_instance}", file=sys.stderr)
    elif args.mode == "scientist":
        account_candidates = collect_account_candidates(instances, args.query, args.timeout)
        selected_accounts, candidate_accounts = select_accounts(
            account_candidates,
            args.query,
            score_account_for_person,
            PERSON_ACCOUNT_THRESHOLD,
            args.max_accounts,
            allow_best_fallback=True,
        )
        print_account_candidates(candidate_accounts)
        for account in selected_accounts:
            remaining = args.max_posts - len(raw_statuses)
            if remaining <= 0:
                break
            raw_statuses.extend(fetch_account_statuses(account, since_utc, remaining, args.limit, args.timeout))
    elif args.journal_mode in {"auto", "account"}:
        account_candidates = collect_account_candidates(instances, args.query, args.timeout)
        selected_accounts, candidate_accounts = select_accounts(
            account_candidates,
            args.query,
            score_official_journal_account,
            JOURNAL_ACCOUNT_THRESHOLD,
            args.max_accounts,
            allow_best_fallback=False,
        )
        print_account_candidates(candidate_accounts)
        if selected_accounts:
            result_source = "journal_account"
            for account in selected_accounts:
                remaining = args.max_posts - len(raw_statuses)
                if remaining <= 0:
                    break
                raw_statuses.extend(fetch_account_statuses(account, since_utc, remaining, args.limit, args.timeout))
        elif args.journal_mode == "account":
            print(f"No likely official journal account found for query: {args.query}")
            result_source = "journal_account"
        else:
            result_source = "journal_keyword"
            raw_statuses = collect_status_keyword_results(
                instances, args.query, since_utc, args.max_posts, args.limit, args.timeout
            )
            selected_accounts = []
    else:
        result_source = "journal_keyword"
        raw_statuses = collect_status_keyword_results(instances, args.query, since_utc, args.max_posts, args.limit, args.timeout)

    raw_statuses = dedupe_statuses(raw_statuses)[: args.max_posts]
    selected_by_instance_id = {
        (str(account.get("_source_instance")), str(account.get("id"))): account for account in selected_accounts
    }
    normalized_posts: list[dict[str, Any]] = []
    for status in raw_statuses:
        parsed = parse_datetime(status.get("created_at"))
        if parsed is not None and parsed < since_utc:
            continue
        selected = selected_by_instance_id.get((str(status.get("_source_instance")), str((status.get("account") or {}).get("id"))))
        if not selected and selected_accounts:
            selected = next(
                (
                    account
                    for account in selected_accounts
                    if str(account.get("_source_instance")) == str(status.get("_source_instance"))
                    and str(account.get("id")) == str((status.get("account") or {}).get("id"))
                ),
                None,
            )
        normalized_posts.append(normalize_status(status, args.mode, result_source, args.query, selected))

    output_base = safe_filename(args.query)
    sqlite_path = args.out_dir / f"{output_base}.sqlite"
    json_path = args.out_dir / f"{output_base}.json"
    selected_summaries = [account_summary(account) for account in selected_accounts]
    fetched_at = utc_now()

    save_sqlite(sqlite_path, normalized_posts)
    save_json(
        json_path,
        mode=args.mode,
        journal_mode=args.journal_mode,
        result_source=result_source,
        query=args.query,
        days=args.days,
        since_utc=since_utc,
        fetched_at=fetched_at,
        instances=instances,
        selected_accounts=selected_summaries,
        candidate_accounts=candidate_accounts[:25],
        posts=normalized_posts,
    )

    print("\nSummary:")
    print("  platform = mastodon")
    print(f"  mode = {args.mode}")
    print(f"  query = {args.query}")
    print(f"  days = {args.days}")
    if args.mode == "journal":
        print(f"  journal_mode = {args.journal_mode}")
    print(f"  result_source = {result_source}")
    print(f"  searched instances count = {len(instances)}")
    if selected_summaries:
        print("  selected accounts:")
        for account in selected_summaries:
            print(
                "    "
                f"{account.get('acct') or account.get('username') or account.get('id')} "
                f"on {account.get('source_instance')} "
                f"({account.get('display_name') or ''})"
            )
    print(f"  number of posts saved = {len(normalized_posts)}")
    print(f"  output sqlite path = {sqlite_path}")
    print(f"  output json path = {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
