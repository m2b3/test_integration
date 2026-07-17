"""
Fetch OSF-hosted preprints from the past 24 hours into a unified SQLite schema.

PsyArXiv and SocArXiv are OSF-hosted providers, so this module contains the
shared OSF API v2 preprints logic and thin provider scripts can call into it.

Usage through wrappers:
  python psyarxiv.py --db psyarxiv.sqlite
  python socarxiv.py --db socarxiv.sqlite
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests


OSF_PREPRINTS_API_URL = "https://api.osf.io/v2/preprints/"
SUPPORTED_PROVIDERS = {"psyarxiv", "socarxiv"}
DATE_FILTER_FIELDS = ("date_published", "date_created", "date_modified")
USER_AGENT = "osf_preprints_last24h_to_sqlite/1.0 (Python requests)"


@dataclass
class OSFFetchStats:
    provider: str
    pages_fetched: int = 0
    raw_records_fetched: int = 0
    unique_records_fetched: int = 0
    retained_records: int = 0
    date_filter_failures: int = 0
    used_fallback_recent_pages: bool = False


class OSFPreprintFetchError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_space(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_osf_date(value: str | None) -> datetime | None:
    raw = normalize_space(value)
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    if "T" not in raw:
        raw = f"{raw}T00:00:00+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0)


def last_24h_cutoff(now: datetime | None = None) -> datetime:
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return base.astimezone(timezone.utc).replace(microsecond=0) - timedelta(hours=24)


def cutoff_for_hours(hours: int) -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=hours)


def normalize_author_list(raw_contributors_or_attrs: Any) -> list[str]:
    if not raw_contributors_or_attrs:
        return []

    raw = raw_contributors_or_attrs
    if isinstance(raw, dict):
        for key in ("contributors", "bibliographic_contributors", "authors"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
        else:
            raw = [raw]

    if not isinstance(raw, list):
        return []

    authors: list[str] = []
    for contributor in raw:
        name = None
        if isinstance(contributor, str):
            name = contributor
        elif isinstance(contributor, dict):
            attrs = contributor.get("attributes") if isinstance(contributor.get("attributes"), dict) else contributor
            name = (
                attrs.get("full_name")
                or attrs.get("name")
                or attrs.get("display_name")
                or attrs.get("family_name")
            )
            if not name and (attrs.get("given_name") or attrs.get("family_name")):
                name = " ".join(part for part in (attrs.get("given_name"), attrs.get("family_name")) if part)
        normalized = normalize_space(name)
        if normalized:
            authors.append(normalized)
    return authors


def build_osf_preprints_url(
    provider: str,
    page_size: int = 100,
    query: str | None = None,
    since: datetime | None = None,
) -> str:
    return _build_osf_preprints_url(
        provider=provider,
        page_size=page_size,
        query=query,
        since=since,
        date_filter_field="date_published" if since else None,
    )


def _build_osf_preprints_url(
    *,
    provider: str,
    page_size: int,
    query: str | None,
    since: datetime | None,
    date_filter_field: str | None,
    sort: str | None = None,
) -> str:
    safe_page_size = max(1, min(page_size, 100))
    params: list[tuple[str, str]] = [
        ("filter[provider]", provider),
        ("page[size]", str(safe_page_size)),
    ]
    if since is not None and date_filter_field:
        params.append((f"filter[{date_filter_field}][gte]", since.date().isoformat()))
    if sort:
        params.append(("sort", sort))
    return f"{OSF_PREPRINTS_API_URL}?{urllib.parse.urlencode(params)}"


def fetch_osf_preprints_provider(
    provider: str,
    hours: int = 24,
    query: str | None = None,
    max_results: int | None = None,
    page_size: int = 100,
    timeout: float = 30.0,
) -> list[dict]:
    papers, _stats = fetch_osf_preprints_provider_with_stats(
        provider=provider,
        hours=hours,
        query=query,
        max_results=max_results,
        page_size=page_size,
        timeout=timeout,
    )
    return papers


def fetch_osf_preprints_provider_with_stats(
    provider: str,
    hours: int = 24,
    query: str | None = None,
    max_results: int | None = None,
    page_size: int = 100,
    timeout: float = 30.0,
) -> tuple[list[dict], OSFFetchStats]:
    validate_fetch_args(provider, hours, max_results, page_size)

    cutoff = cutoff_for_hours(hours)
    stats = OSFFetchStats(provider=provider)
    raw_by_id: dict[str, dict] = {}
    print(f"[info] provider={provider} hours={hours} cutoff={cutoff.isoformat()}")

    for date_field in DATE_FILTER_FIELDS:
        url = _build_osf_preprints_url(
            provider=provider,
            page_size=page_size,
            query=query,
            since=cutoff,
            date_filter_field=date_field,
            sort=f"-{date_field}",
        )
        print(f"[info] OSF request provider={provider} date_filter={date_field} url={url}")
        try:
            page_records, pages = fetch_osf_pages(url, timeout=timeout, max_results=max_results)
        except OSFPreprintFetchError as exc:
            if exc.status_code not in {400, 404, 422}:
                raise
            stats.date_filter_failures += 1
            print(f"[warn] OSF date filter {date_field} failed: {exc}", file=sys.stderr)
            continue

        stats.pages_fetched += pages
        stats.raw_records_fetched += len(page_records)
        for item in page_records:
            item_id = normalize_space(item.get("id"))
            if item_id and item_id not in raw_by_id:
                raw_by_id[item_id] = item
            if max_results and len(raw_by_id) >= max_results:
                break
        print(
            f"[page] provider={provider} date_filter={date_field} pages={pages} "
            f"raw_records={len(page_records)} unique_records={len(raw_by_id)}"
        )
        if max_results and len(raw_by_id) >= max_results:
            break

    if stats.date_filter_failures:
        fallback_limit = fallback_page_limit(page_size=page_size, max_results=max_results)
        fallback_url = _build_osf_preprints_url(
            provider=provider,
            page_size=page_size,
            query=query,
            since=None,
            date_filter_field=None,
            sort="-date_modified",
        )
        print(
            f"[info] Falling back to recent provider pages provider={provider} "
            f"page_limit={fallback_limit} url={fallback_url}"
        )
        try:
            fallback_records, fallback_pages = fetch_osf_pages(
                fallback_url,
                timeout=timeout,
                max_results=max_results,
                max_pages=fallback_limit,
            )
            stats.used_fallback_recent_pages = True
            stats.pages_fetched += fallback_pages
            stats.raw_records_fetched += len(fallback_records)
            for item in fallback_records:
                item_id = normalize_space(item.get("id"))
                if item_id and item_id not in raw_by_id:
                    raw_by_id[item_id] = item
                if max_results and len(raw_by_id) >= max_results:
                    break
        except OSFPreprintFetchError as exc:
            raise OSFPreprintFetchError(f"OSF fallback recent-page fetch failed: {exc}") from exc

    stats.unique_records_fetched = len(raw_by_id)
    normalized = [normalize_osf_preprint(item, provider) for item in raw_by_id.values()]
    if query:
        normalized = filter_by_query(normalized, query)
    retained = filter_last_24h(normalized, hours=hours)
    stats.retained_records = len(retained)
    print(
        f"[progress] provider={provider} pages_fetched={stats.pages_fetched} "
        f"raw_records={stats.raw_records_fetched} unique_records={stats.unique_records_fetched} "
        f"retained_last_{hours}h={stats.retained_records}"
    )
    return retained, stats


def fetch_osf_pages(
    start_url: str,
    *,
    timeout: float,
    max_results: int | None = None,
    max_pages: int | None = None,
) -> tuple[list[dict], int]:
    url: str | None = start_url
    records: list[dict] = []
    pages = 0

    while url:
        if max_pages is not None and pages >= max_pages:
            break
        payload = request_osf_json(url, timeout=timeout)
        data = payload.get("data")
        if not isinstance(data, list):
            raise OSFPreprintFetchError("OSF API response did not contain a JSON:API data list")

        pages += 1
        remaining = max_results - len(records) if max_results else len(data)
        if max_results and remaining <= 0:
            break
        records.extend(data[:remaining] if max_results else data)

        if max_results and len(records) >= max_results:
            break
        links = payload.get("links") if isinstance(payload.get("links"), dict) else {}
        next_url = links.get("next")
        url = next_url if isinstance(next_url, str) and next_url else None
        if url:
            time.sleep(0.3)

    return records, pages


def request_osf_json(url: str, *, timeout: float) -> dict:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise OSFPreprintFetchError(f"request failed: {type(exc).__name__}: {exc}") from exc

    if resp.status_code >= 400:
        preview = " ".join(resp.text.split())[:500]
        raise OSFPreprintFetchError(f"HTTP {resp.status_code}: {preview}", status_code=resp.status_code)

    try:
        data = resp.json()
    except ValueError as exc:
        preview = " ".join(resp.text.split())[:500]
        raise OSFPreprintFetchError(f"malformed JSON response: {preview}") from exc
    if not isinstance(data, dict):
        raise OSFPreprintFetchError("OSF API response was not a JSON object")
    return data


def normalize_osf_preprint(item: dict, provider: str) -> dict:
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    item_id = normalize_space(item.get("id"))
    source = normalize_space(provider) or ""
    published_date = normalize_space(attrs.get("date_published")) or normalize_space(attrs.get("date_created"))
    updated_date = normalize_space(attrs.get("date_modified"))

    # TODO: Follow relationships.contributors links later when contributor
    # metadata is not embedded in the preprints payload.
    authors = normalize_author_list(attrs)
    categories = extract_categories(attrs)

    return {
        "source": source,
        "external_id": item_id,
        "title": normalize_space(attrs.get("title")),
        "abstract": normalize_space(attrs.get("description")),
        "authors": authors,
        "published_date": published_date,
        "updated_date": updated_date,
        "doi": extract_doi(attrs, item),
        "journal": None,
        "categories": categories,
        "url": extract_public_url(item, source, item_id),
        "pdf_url": extract_pdf_url(item),
        "fetched_at": utc_now_iso(),
        "raw_json": item,
    }


def extract_doi(attrs: dict, item: dict) -> str | None:
    for key in ("doi", "preprint_doi", "publication_doi"):
        value = normalize_space(attrs.get(key))
        if value:
            return normalize_doi(value)
    links = item.get("links") if isinstance(item.get("links"), dict) else {}
    for key in ("doi", "preprint_doi"):
        value = normalize_space(links.get(key))
        if value:
            return normalize_doi(value)
    return None


def normalize_doi(value: str) -> str:
    return value.removeprefix("https://doi.org/").removeprefix("http://doi.org/")


def extract_categories(attrs: dict) -> list[str]:
    categories: list[str] = []
    for key in ("subjects", "tags"):
        value = attrs.get(key)
        if isinstance(value, list):
            for item in value:
                categories.extend(flatten_category_value(item))
    return dedupe_preserve_order(categories)


def flatten_category_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        normalized = normalize_space(value)
        return [normalized] if normalized else []
    if isinstance(value, dict):
        for key in ("text", "title", "name"):
            normalized = normalize_space(value.get(key))
            if normalized:
                return [normalized]
        out: list[str] = []
        for nested in value.values():
            if isinstance(nested, (list, dict)):
                out.extend(flatten_category_value(nested))
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(flatten_category_value(item))
        return out
    return []


def dedupe_preserve_order(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def extract_public_url(item: dict, provider: str, item_id: str | None) -> str | None:
    if provider and item_id:
        return f"https://osf.io/preprints/{provider}/{item_id}/"
    links = item.get("links") if isinstance(item.get("links"), dict) else {}
    for key in ("html", "self"):
        value = links.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict) and isinstance(value.get("href"), str):
            return value["href"]
    return None


def extract_pdf_url(item: dict) -> str | None:
    links = item.get("links") if isinstance(item.get("links"), dict) else {}
    for key in ("download", "pdf"):
        value = links.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict) and isinstance(value.get("href"), str):
            return value["href"]

    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    for key in ("download_url", "file_download_url"):
        value = normalize_space(attrs.get(key))
        if value:
            return value
    return None


def filter_last_24h(records: list[dict], hours: int = 24) -> list[dict]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    cutoff = now - timedelta(hours=hours)
    retained: list[dict] = []
    for record in records:
        if record_in_window(record, cutoff, now):
            retained.append(record)
    return retained


def filter_by_query(records: list[dict], query: str) -> list[dict]:
    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        return records
    return [record for record in records if record_matches_query(record, terms)]


def record_matches_query(record: dict, terms: list[str]) -> bool:
    categories = record.get("categories") if isinstance(record.get("categories"), list) else []
    haystack = " ".join(
        str(value or "")
        for value in (
            record.get("title"),
            record.get("abstract"),
            record.get("source"),
            record.get("external_id"),
            " ".join(str(category) for category in categories),
        )
    ).casefold()
    return all(term in haystack for term in terms)


def record_in_window(record: dict, cutoff: datetime, now: datetime) -> bool:
    candidates = [
        record.get("published_date"),
        record.get("updated_date"),
        record.get("date_published"),
        record.get("date_created"),
        record.get("date_modified"),
    ]
    attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
    candidates.extend(
        [
            attrs.get("date_published"),
            attrs.get("date_created"),
            attrs.get("date_modified"),
        ]
    )
    for candidate in candidates:
        dt = parse_osf_date(candidate)
        if dt is not None and cutoff <= dt <= now:
            return True
    return False


def init_papers_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS papers (
            source TEXT NOT NULL,
            external_id TEXT NOT NULL,
            title TEXT,
            abstract TEXT,
            authors TEXT,
            published_date TEXT,
            updated_date TEXT,
            doi TEXT,
            journal TEXT,
            categories TEXT,
            url TEXT,
            pdf_url TEXT,
            fetched_at TEXT,
            raw_json TEXT,
            PRIMARY KEY (source, external_id)
        );
        """
    )
    conn.commit()
    return conn


def insert_papers(conn: sqlite3.Connection, papers: list[dict]) -> int:
    if not papers:
        return 0

    cur = conn.cursor()
    inserted = 0
    for paper in papers:
        try:
            cur.execute(
                """
                INSERT OR IGNORE INTO papers
                (
                    source, external_id, title, abstract, authors,
                    published_date, updated_date, doi, journal, categories,
                    url, pdf_url, fetched_at, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    paper.get("source"),
                    paper.get("external_id"),
                    paper.get("title"),
                    paper.get("abstract"),
                    json.dumps(paper.get("authors", []), ensure_ascii=False),
                    paper.get("published_date"),
                    paper.get("updated_date"),
                    paper.get("doi"),
                    paper.get("journal"),
                    json.dumps(paper.get("categories", []), ensure_ascii=False),
                    paper.get("url"),
                    paper.get("pdf_url"),
                    paper.get("fetched_at"),
                    json.dumps(paper.get("raw_json", paper), ensure_ascii=False),
                ),
            )
            if cur.rowcount == 1:
                inserted += 1
        except sqlite3.Error as exc:
            print(
                f"[error] insert failed for source={paper.get('source')} "
                f"external_id={paper.get('external_id')}: {exc}",
                file=sys.stderr,
            )
    conn.commit()
    return inserted


def fallback_page_limit(page_size: int, max_results: int | None) -> int:
    if max_results:
        return max(1, (max_results + page_size - 1) // page_size)
    return 5


def validate_fetch_args(provider: str, hours: int, max_results: int | None, page_size: int) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unsupported OSF preprint provider: {provider}")
    if hours <= 0:
        raise ValueError("hours must be > 0")
    if max_results is not None and max_results <= 0:
        raise ValueError("max_results must be > 0 when provided")
    if page_size <= 0:
        raise ValueError("page_size must be > 0")
