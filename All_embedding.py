"""
Build and search persistent SPECTER FAISS indexes for scientific SQLite databases.
Stage 1:
  python All_embedding.py arxiv.sqlite
  python All_embedding.py all.sqlite

Stage 2:
  python All_embedding.py --interest "single-cell genomics for early cancer biomarker discovery" --all --limit 10
  python All_embedding.py --interest "eye-tracking" --arxiv --limit 10
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
import numpy as np

DEFAULT_DB_PATH = "pubmed.sqlite"
DEFAULT_INDEX_PATH = "paper_specter.index"
DEFAULT_METADATA_PATH = "paper_metadata.json"
DEFAULT_MANIFEST_PATH = "paper_index_manifest.json"
DEFAULT_MODEL_NAME = "sentence-transformers/allenai-specter"
SEARCH_ARTIFACT_TARGETS = (
    "all",
    "arxiv",
    "pubmed",
    "biorxiv",
    "medrxiv",
    "psyarxiv",
    "socarxiv",
    "openreview",
    "rss",
)
PUBMED_HISTORY_FETCH_LIMIT = 9999
PUBMED_MAX_UID = 999_999_999
PUBMED_ARTICLE_COLUMNS = [
    "pmid",
    "title",
    "journal",
    "pub_date",
    "doi",
    "authors",
    "abstract",
    "fetched_at",
    "raw_json",
]
ARXIV_ARTICLE_COLUMNS = [
    "arxiv_id",
    "title",
    "journal",
    "pub_date",
    "updated_date",
    "doi",
    "authors",
    "abstract",
    "categories",
    "primary_category",
    "url",
    "pdf_url",
    "fetched_at",
    "raw_json",
]
BIORXIV_ARTICLE_COLUMNS = [
    "doi",
    "title",
    "journal",
    "pub_date",
    "version",
    "type",
    "authors",
    "abstract",
    "category",
    "server",
    "url",
    "pdf_url",
    "fetched_at",
    "raw_json",
]
MEDRXIV_ARTICLE_COLUMNS = [
    "doi",
    "title",
    "journal",
    "pub_date",
    "updated_date",
    "authors",
    "abstract",
    "category",
    "url",
    "pdf_url",
    "version",
    "type",
    "license",
    "server",
    "fetched_at",
    "raw_json",
]
RSS_ARTICLE_COLUMNS = [
    "rss_id",
    "source",
    "title",
    "journal",
    "pub_date",
    "updated_date",
    "doi",
    "authors",
    "abstract",
    "categories",
    "primary_category",
    "url",
    "pdf_url",
    "feed_url",
    "fetched_at",
    "raw_json",
]
UNIFIED_PAPER_COLUMNS = [
    "source",
    "external_id",
    "title",
    "abstract",
    "authors",
    "published_date",
    "updated_date",
    "doi",
    "journal",
    "categories",
    "url",
    "pdf_url",
    "fetched_at",
    "raw_json",
]
OPENREVIEW_PAPER_COLUMNS = [
    "id",
    "source",
    "forum",
    "number",
    "title",
    "authors",
    "abstract",
    "pdf_url",
    "venue_id",
    "venue",
    "venueid",
    "decision",
    "status",
    "presentation",
    "readers",
    "cdate",
    "mdate",
    "classification",
    "raw_content",
]
ALL_SQLITE_COLUMNS = [
    "paper_key",
    "source",
    "external_id",
    "title",
    "abstract",
    "authors",
    "published_date",
    "updated_date",
    "doi",
    "journal",
    "categories",
    "url",
    "pdf_url",
    "fetched_at",
    "raw_json",
    "pmid",
    "arxiv_id",
    "rss_id",
    "version",
    "article_type",
    "license",
    "server",
    "category",
    "primary_category",
    "feed_url",
    "openreview_id",
    "forum",
    "number",
    "venue_id",
    "venue",
    "venueid",
    "decision",
    "status",
    "presentation",
    "classification",
    "readers",
    "raw_content",
    "source_db",
]
EXISTING_DB_TABLES = {
    "pubmed": ("pubmed_articles", "pmid", PUBMED_ARTICLE_COLUMNS),
    "arxiv": ("arxiv_articles", "arxiv_id", ARXIV_ARTICLE_COLUMNS),
    "biorxiv": ("biorxiv_articles", "doi", BIORXIV_ARTICLE_COLUMNS),
    "medrxiv": ("medrxiv_articles", "doi", MEDRXIV_ARTICLE_COLUMNS),
    "rss": ("rss_articles", "rss_id", RSS_ARTICLE_COLUMNS),
    "papers": ("papers", "source, external_id", UNIFIED_PAPER_COLUMNS),
    "openreview": ("papers", "id", OPENREVIEW_PAPER_COLUMNS),
}


def format_seconds(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {sec:.1f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h {int(minutes)}m {sec:.1f}s"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def chunked(seq: list[Any], n: int) -> Iterable[list[Any]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _load_faiss():
    try:
        import faiss
    except ImportError as exc:
        raise RuntimeError("Missing FAISS dependency. Install with `pip install faiss-cpu`.") from exc
    return faiss


def _unique_pmids(pmids: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for pmid in pmids:
        pmid = str(pmid).strip()
        if pmid and pmid not in seen:
            seen.add(pmid)
            out.append(pmid)
    return out


def _delete_articles_by_pmids(conn: sqlite3.Connection, pmids: list[str]) -> None:
    for block in chunked(_unique_pmids(pmids), 800):
        qmarks = ",".join(["?"] * len(block))
        conn.execute(f"DELETE FROM pubmed_articles WHERE pmid IN ({qmarks})", block)
    conn.commit()


def build_pubmed_all_query(require_abstract: bool = True) -> str:
    if require_abstract:
        return "all[sb] AND hasabstract"
    return "all[sb]"


def _pubmed_uid_range_query(query: str, low_uid: int, high_uid: int) -> str:
    return f"({query}) AND {low_uid}:{high_uid}[uid]"


def _split_pubmed_query_by_uid_range(
    cfg: Any,
    query: str,
    max_records: int = PUBMED_HISTORY_FETCH_LIMIT,
) -> list[tuple[str, int, str, str]]:
    pending: list[tuple[int, int]] = [(1, PUBMED_MAX_UID)]
    chunks: list[tuple[int, str, int, str, str]] = []
    try:
        from base import esearch_last_24h
    except ImportError as exc:
        raise RuntimeError("PubMed fetching requires dependencies from requirements.txt.") from exc

    while pending:
        low_uid, high_uid = pending.pop()
        range_query = _pubmed_uid_range_query(query, low_uid, high_uid)
        count, webenv, query_key = esearch_last_24h(cfg, range_query)
        if count == 0:
            continue
        if count <= max_records:
            chunks.append((low_uid, range_query, count, webenv, query_key))
            print(f"[split] {low_uid}:{high_uid}[uid] count={count}")
            continue
        if low_uid >= high_uid:
            raise RuntimeError(
                f"PubMed subquery still has {count} records at a single UID range. "
                "Cannot split further."
            )

        mid_uid = (low_uid + high_uid) // 2
        print(f"[split] {low_uid}:{high_uid}[uid] count={count}; splitting")
        pending.append((mid_uid + 1, high_uid))
        pending.append((low_uid, mid_uid))

    chunks.sort(key=lambda item: item[0])
    return [(query_text, count, webenv, query_key) for _, query_text, count, webenv, query_key in chunks]


def fetch_pubmed_last24h_all(
    db_path: str,
    query: str,
    fetch_batch: int,
    fetch_retries: int,
    refresh: bool,
) -> list[str]:
    if fetch_batch <= 0:
        raise ValueError("--fetch-batch must be greater than 0.")
    if fetch_retries <= 0:
        raise ValueError("--fetch-retries must be greater than 0.")

    try:
        from base import (
            efetch_pubmed_batch,
            esearch_last_24h,
            existing_pmids,
            get_ncbi_config,
            init_db,
            insert_articles,
        )
    except ImportError as exc:
        raise RuntimeError("PubMed fetching requires dependencies from requirements.txt.") from exc

    cfg = get_ncbi_config()
    conn = init_db(db_path)
    try:
        count, webenv, query_key = esearch_last_24h(cfg, query)
        print(f"[info] PubMed query: {query}")
        print(f"[info] Found {count} PubMed records in the past 24h (reldate=1, datetype=edat).")
        if count == 0:
            return []

        if count > PUBMED_HISTORY_FETCH_LIMIT:
            print(
                f"[warn] PubMed history fetch is unreliable past {PUBMED_HISTORY_FETCH_LIMIT} records. "
                "Splitting query by PMID range to fetch all records."
            )
            fetch_chunks = _split_pubmed_query_by_uid_range(cfg, query)
        else:
            fetch_chunks = [(query, count, webenv, query_key)]

        total_fetched = 0
        total_inserted = 0
        run_pmids: list[str] = []

        for chunk_num, (chunk_query, chunk_count, chunk_webenv, chunk_query_key) in enumerate(fetch_chunks, start=1):
            if len(fetch_chunks) > 1:
                print(f"[chunk] {chunk_num}/{len(fetch_chunks)} count={chunk_count} query={chunk_query}")

            retstart = 0
            while retstart < chunk_count:
                this_batch = min(fetch_batch, chunk_count - retstart)
                records = efetch_pubmed_batch(
                    chunk_webenv,
                    chunk_query_key,
                    retstart,
                    this_batch,
                    max_retries=fetch_retries,
                )
                retstart += this_batch

                pmids = [str(record["pmid"]) for record in records if record.get("pmid")]
                run_pmids.extend(pmids)
                total_fetched += len(pmids)

                if refresh:
                    _delete_articles_by_pmids(conn, pmids)
                    records_to_insert = [record for record in records if record.get("pmid")]
                else:
                    already = existing_pmids(conn, pmids)
                    records_to_insert = [
                        record
                        for record in records
                        if record.get("pmid") and str(record["pmid"]) not in already
                    ]

                inserted = insert_articles(conn, records_to_insert)
                total_inserted += inserted
                print(
                    f"[page] chunk={chunk_num}/{len(fetch_chunks)} retstart={retstart - this_batch} "
                    f"fetched={len(pmids)} inserted={inserted} total_fetched={total_fetched}/{count}"
                )

        print(f"[done] fetched={total_fetched} inserted={total_inserted} db={db_path}")
        return _unique_pmids(run_pmids)
    except HTTPError as exc:
        raise RuntimeError(
            f"PubMed request failed with HTTP {exc.code}: {exc.reason}. "
            "This is often temporary on the NCBI side; wait a minute and run again."
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"PubMed request failed because of a network error: {exc}. "
            "Check internet access and try again."
        ) from exc
    finally:
        conn.close()


def load_articles_by_pmids(conn: sqlite3.Connection, pmids: list[str]) -> list[dict[str, Any]]:
    unique = _unique_pmids(pmids)
    if not unique:
        return []

    rows_by_pmid: dict[str, dict[str, Any]] = {}
    for block in chunked(unique, 800):
        qmarks = ",".join(["?"] * len(block))
        rows = conn.execute(
            f"SELECT {', '.join(PUBMED_ARTICLE_COLUMNS)} "
            f"FROM pubmed_articles WHERE pmid IN ({qmarks})",
            block,
        ).fetchall()
        for row in rows:
            article = dict(zip(PUBMED_ARTICLE_COLUMNS, row))
            rows_by_pmid[str(article["pmid"])] = article

    return [rows_by_pmid[pmid] for pmid in unique if pmid in rows_by_pmid]


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _detect_existing_db_source_type(conn: sqlite3.Connection, db_path: str) -> str:
    if _table_exists(conn, "pubmed_articles"):
        return "pubmed"
    if _table_exists(conn, "arxiv_articles"):
        return "arxiv"
    if _table_exists(conn, "biorxiv_articles"):
        return "biorxiv"
    if _table_exists(conn, "medrxiv_articles"):
        return "medrxiv"
    if _table_exists(conn, "rss_articles"):
        return "rss"
    if _table_exists(conn, "papers"):
        columns = _table_columns(conn, "papers")
        if {"source", "external_id", "published_date"}.issubset(columns):
            return "papers"
        if {"id", "forum", "classification", "raw_content"}.issubset(columns):
            return "openreview"
        return "papers"
    raise RuntimeError(
        "SQLite database has none of the supported tables "
        "(pubmed_articles, arxiv_articles, biorxiv_articles, medrxiv_articles, rss_articles, papers): "
        f"{db_path}"
    )


def load_articles_from_existing_db(db_path: str) -> tuple[str, list[dict[str, Any]]]:
    if not os.path.exists(db_path):
        raise RuntimeError(f"SQLite database not found: {db_path}")

    try:
        conn = sqlite3.connect(db_path)
        try:
            db_source_type = _detect_existing_db_source_type(conn, db_path)
            table_name, order_by, columns = EXISTING_DB_TABLES[db_source_type]
            if db_source_type == "papers":
                available_columns = _table_columns(conn, table_name)
                columns = [column for column in ALL_SQLITE_COLUMNS if column in available_columns]
                for required_column in UNIFIED_PAPER_COLUMNS:
                    if required_column in available_columns and required_column not in columns:
                        columns.append(required_column)
                order_by = "source, external_id"

            rows = conn.execute(
                f"SELECT {', '.join(columns)} FROM {table_name} ORDER BY {order_by}"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise RuntimeError(f"Could not read SQLite database `{db_path}`: {exc}") from exc

    return db_source_type, [dict(zip(columns, row)) for row in rows]


def derive_artifact_paths(sqlite_path: str) -> tuple[str, str, str]:
    stem = os.path.splitext(os.path.basename(sqlite_path))[0]
    if not stem:
        raise ValueError(f"Cannot derive artifact names from SQLite path: {sqlite_path}")
    return f"{stem}_specter.index", f"{stem}_metadata.json", f"{stem}_manifest.json"


def derive_artifact_paths_for_target(target: str) -> tuple[str, str, str]:
    return derive_artifact_paths(f"{target}.sqlite")


def _sqlite_target_is_merge_mode(sqlite_target: str) -> bool:
    return os.path.basename(sqlite_target) == "all.sqlite"


def _normalize_cli_argv(argv: list[str]) -> list[str]:
    normalized: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--" and i + 1 < len(argv):
            next_arg = argv[i + 1]
            if next_arg in SEARCH_ARTIFACT_TARGETS:
                normalized.append(f"--{next_arg}")
                i += 2
                continue
            if next_arg in {"limit", "min-score"}:
                normalized.append(f"--{next_arg}")
                i += 2
                continue
        normalized.append(arg)
        i += 1
    return normalized


def _nonempty_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _json_for_sql(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text
        return json.dumps(parsed, ensure_ascii=False)
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    text = str(value).strip()
    return text or None


def _json_list_for_sql(value: Any) -> str | None:
    parsed = _parse_json_list(value)
    if not parsed:
        return None
    return json.dumps(parsed, ensure_ascii=False)


def _source_from_biorxiv_server(article: dict[str, Any], default_source: str) -> str:
    server = str(article.get("server") or "").strip().lower()
    if server == "medrxiv":
        return "medrxiv"
    if server == "biorxiv":
        return "biorxiv"
    return default_source


def normalize_record_to_all_schema(record: dict[str, Any]) -> dict[str, Any] | None:
    source = _nonempty_text(record.get("source"))
    external_id = _nonempty_text(record.get("external_id"))
    if not source or not external_id:
        return None

    normalized = {column: None for column in ALL_SQLITE_COLUMNS}
    normalized.update({key: record.get(key) for key in ALL_SQLITE_COLUMNS if key in record})
    normalized["source"] = source
    normalized["external_id"] = external_id
    normalized["paper_key"] = f"{source}:{external_id}"

    for text_column in (
        "title",
        "abstract",
        "published_date",
        "updated_date",
        "doi",
        "journal",
        "url",
        "pdf_url",
        "fetched_at",
        "pmid",
        "arxiv_id",
        "rss_id",
        "version",
        "article_type",
        "license",
        "server",
        "category",
        "primary_category",
        "feed_url",
        "openreview_id",
        "forum",
        "number",
        "venue_id",
        "venue",
        "venueid",
        "decision",
        "status",
        "presentation",
        "classification",
        "source_db",
    ):
        normalized[text_column] = _nonempty_text(normalized.get(text_column))

    normalized["authors"] = _json_list_for_sql(normalized.get("authors"))
    normalized["categories"] = _json_list_for_sql(normalized.get("categories"))
    normalized["readers"] = _json_list_for_sql(normalized.get("readers"))
    normalized["raw_json"] = _json_for_sql(normalized.get("raw_json"))
    normalized["raw_content"] = _json_for_sql(normalized.get("raw_content"))
    return normalized


def articles_to_unified_records(
    db_source_type: str,
    articles: list[dict[str, Any]],
    source_db: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    source_db_name = os.path.basename(source_db)
    for article in articles:
        record: dict[str, Any]
        if db_source_type == "pubmed":
            pmid = _nonempty_text(article.get("pmid"))
            record = {
                "source": "pubmed",
                "external_id": pmid,
                "pmid": pmid,
                "title": article.get("title"),
                "abstract": article.get("abstract"),
                "authors": article.get("authors"),
                "published_date": article.get("pub_date"),
                "doi": article.get("doi"),
                "journal": article.get("journal"),
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
                "fetched_at": article.get("fetched_at"),
                "raw_json": article.get("raw_json"),
            }
        elif db_source_type == "arxiv":
            arxiv_id = _nonempty_text(article.get("arxiv_id"))
            record = {
                "source": "arxiv",
                "external_id": arxiv_id,
                "arxiv_id": arxiv_id,
                "title": article.get("title"),
                "abstract": article.get("abstract"),
                "authors": article.get("authors"),
                "published_date": article.get("pub_date"),
                "updated_date": article.get("updated_date"),
                "doi": article.get("doi"),
                "journal": article.get("journal"),
                "categories": article.get("categories"),
                "primary_category": article.get("primary_category"),
                "url": article.get("url"),
                "pdf_url": article.get("pdf_url"),
                "fetched_at": article.get("fetched_at"),
                "raw_json": article.get("raw_json"),
            }
        elif db_source_type in {"biorxiv", "medrxiv"}:
            doi = _nonempty_text(article.get("doi"))
            source = _source_from_biorxiv_server(article, db_source_type)
            record = {
                "source": source,
                "external_id": doi,
                "doi": doi,
                "title": article.get("title"),
                "abstract": article.get("abstract"),
                "authors": article.get("authors"),
                "published_date": article.get("pub_date"),
                "updated_date": article.get("updated_date"),
                "journal": article.get("journal"),
                "version": article.get("version"),
                "article_type": article.get("type"),
                "license": article.get("license"),
                "server": article.get("server"),
                "category": article.get("category"),
                "url": article.get("url"),
                "pdf_url": article.get("pdf_url"),
                "fetched_at": article.get("fetched_at"),
                "raw_json": article.get("raw_json"),
            }
        elif db_source_type == "rss":
            rss_id = _nonempty_text(article.get("rss_id"))
            source = _nonempty_text(article.get("source")) or "rss"
            record = {
                "source": source,
                "external_id": rss_id,
                "rss_id": rss_id,
                "title": article.get("title"),
                "abstract": article.get("abstract"),
                "authors": article.get("authors"),
                "published_date": article.get("pub_date"),
                "updated_date": article.get("updated_date"),
                "doi": article.get("doi"),
                "journal": article.get("journal"),
                "categories": article.get("categories"),
                "primary_category": article.get("primary_category"),
                "url": article.get("url"),
                "pdf_url": article.get("pdf_url"),
                "feed_url": article.get("feed_url"),
                "fetched_at": article.get("fetched_at"),
                "raw_json": article.get("raw_json"),
            }
        elif db_source_type == "openreview":
            openreview_id = _nonempty_text(article.get("id"))
            source = _nonempty_text(article.get("source")) or "openreview"
            forum = _nonempty_text(article.get("forum"))
            record = {
                "source": source,
                "external_id": openreview_id,
                "openreview_id": openreview_id,
                "title": article.get("title"),
                "abstract": article.get("abstract"),
                "authors": article.get("authors"),
                "published_date": article.get("cdate"),
                "updated_date": article.get("mdate"),
                "journal": article.get("venue") or article.get("venue_id"),
                "url": f"https://openreview.net/forum?id={forum or openreview_id}" if openreview_id else None,
                "pdf_url": article.get("pdf_url"),
                "forum": forum,
                "number": article.get("number"),
                "venue_id": article.get("venue_id"),
                "venue": article.get("venue"),
                "venueid": article.get("venueid"),
                "decision": article.get("decision"),
                "status": article.get("status"),
                "presentation": article.get("presentation"),
                "classification": article.get("classification"),
                "readers": article.get("readers"),
                "raw_content": article.get("raw_content"),
            }
        else:
            record = {column: article.get(column) for column in ALL_SQLITE_COLUMNS if column in article}
            record.update(
                {
                    "source": article.get("source"),
                    "external_id": article.get("external_id"),
                    "title": article.get("title"),
                    "abstract": article.get("abstract"),
                    "authors": article.get("authors"),
                    "published_date": article.get("published_date"),
                    "updated_date": article.get("updated_date"),
                    "doi": article.get("doi"),
                    "journal": article.get("journal"),
                    "categories": article.get("categories"),
                    "url": article.get("url"),
                    "pdf_url": article.get("pdf_url"),
                    "fetched_at": article.get("fetched_at"),
                    "raw_json": article.get("raw_json"),
                }
            )

        record["source_db"] = source_db_name
        normalized = normalize_record_to_all_schema(record)
        if normalized is not None:
            records.append(normalized)
    return records


def build_paper_text(title: str | None, abstract: str | None) -> str:
    return f"Title: {title or ''}\nAbstract: {abstract or ''}"


def _parse_authors(raw_authors: Any) -> list[str]:
    if isinstance(raw_authors, list):
        return [str(author) for author in raw_authors if str(author).strip()]
    if not raw_authors:
        return []
    if isinstance(raw_authors, str):
        try:
            parsed = json.loads(raw_authors)
        except json.JSONDecodeError:
            return [raw_authors] if raw_authors.strip() else []
        if isinstance(parsed, list):
            return [str(author) for author in parsed if str(author).strip()]
    return []


def _parse_json_list(raw_value: Any) -> list[str]:
    if isinstance(raw_value, list):
        return [str(value) for value in raw_value if str(value).strip()]
    if not raw_value:
        return []
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return [raw_value] if raw_value.strip() else []
        if isinstance(parsed, list):
            return [str(value) for value in parsed if str(value).strip()]
        if isinstance(parsed, str) and parsed.strip():
            return [parsed]
    return []


def articles_to_index_records(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for article in articles:
        pmid = str(article.get("pmid") or "").strip()
        title = str(article.get("title") or "").strip()
        abstract = str(article.get("abstract") or "").strip()
        if not pmid or not abstract or not (title or abstract):
            continue

        row_id = len(records)
        records.append(
            {
                "row_id": row_id,
                "source": "pubmed",
                "pmid": pmid,
                "external_id": pmid,
                "title": title,
                "abstract": abstract,
                "journal": article.get("journal") or "",
                "pub_date": article.get("pub_date") or "",
                "doi": article.get("doi") or "",
                "authors": _parse_authors(article.get("authors")),
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            }
        )
    return records


def articles_to_index_records_arxiv(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for article in articles:
        arxiv_id = str(article.get("arxiv_id") or "").strip()
        title = str(article.get("title") or "").strip()
        abstract = str(article.get("abstract") or "").strip()
        if not arxiv_id or not abstract or not (title or abstract):
            continue

        row_id = len(records)
        records.append(
            {
                "row_id": row_id,
                "source": "arxiv",
                "external_id": arxiv_id,
                "arxiv_id": arxiv_id,
                "title": title,
                "abstract": abstract,
                "journal": article.get("journal") or "",
                "pub_date": article.get("pub_date") or "",
                "updated_date": article.get("updated_date") or "",
                "doi": article.get("doi") or "",
                "authors": _parse_authors(article.get("authors")),
                "categories": _parse_json_list(article.get("categories")),
                "primary_category": article.get("primary_category") or "",
                "url": article.get("url") or "",
                "pdf_url": article.get("pdf_url") or "",
            }
        )
    return records


def articles_to_index_records_biorxiv(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for article in articles:
        doi = str(article.get("doi") or "").strip()
        title = str(article.get("title") or "").strip()
        abstract = str(article.get("abstract") or "").strip()
        if not doi or not abstract:
            continue

        server = str(article.get("server") or "").strip().lower()
        source = "medrxiv" if server == "medrxiv" else "biorxiv"
        journal = str(article.get("journal") or "").strip()
        if not journal:
            journal = "medRxiv" if server == "medrxiv" else "bioRxiv"

        row_id = len(records)
        records.append(
            {
                "row_id": row_id,
                "source": source,
                "external_id": doi,
                "doi": doi,
                "title": title,
                "abstract": abstract,
                "journal": journal,
                "pub_date": article.get("pub_date") or "",
                "version": article.get("version") or "",
                "type": article.get("type") or "",
                "authors": _parse_authors(article.get("authors")),
                "category": article.get("category") or "",
                "server": server,
                "url": article.get("url") or "",
                "pdf_url": article.get("pdf_url") or "",
            }
        )
    return records


def articles_to_index_records_rss(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for article in articles:
        rss_id = str(article.get("rss_id") or "").strip()
        source = str(article.get("source") or "").strip() or "rss"
        title = str(article.get("title") or "").strip()
        abstract = str(article.get("abstract") or "").strip()
        if not rss_id or not abstract or not (title or abstract):
            continue

        row_id = len(records)
        records.append(
            {
                "row_id": row_id,
                "source": source,
                "rss_id": rss_id,
                "external_id": rss_id,
                "title": title,
                "abstract": abstract,
                "journal": article.get("journal") or "",
                "pub_date": article.get("pub_date") or "",
                "updated_date": article.get("updated_date") or "",
                "doi": article.get("doi") or "",
                "authors": _parse_authors(article.get("authors")),
                "categories": _parse_json_list(article.get("categories")),
                "primary_category": article.get("primary_category") or "",
                "url": article.get("url") or "",
                "pdf_url": article.get("pdf_url") or "",
                "feed_url": article.get("feed_url") or "",
            }
        )
    return records


def articles_to_index_records_papers(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for article in articles:
        source = str(article.get("source") or "").strip()
        external_id = str(article.get("external_id") or "").strip()
        title = str(article.get("title") or "").strip()
        abstract = str(article.get("abstract") or "").strip()
        if not source or not external_id or not abstract or not (title or abstract):
            continue

        row_id = len(records)
        records.append(
            {
                "row_id": row_id,
                "source": source,
                "external_id": external_id,
                "title": title,
                "abstract": abstract,
                "journal": article.get("journal") or "",
                "pub_date": article.get("published_date") or "",
                "published_date": article.get("published_date") or "",
                "updated_date": article.get("updated_date") or "",
                "doi": article.get("doi") or "",
                "authors": _parse_authors(article.get("authors")),
                "categories": _parse_json_list(article.get("categories")),
                "url": article.get("url") or "",
                "pdf_url": article.get("pdf_url") or "",
            }
        )
    return records


def articles_to_index_records_openreview(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for article in articles:
        openreview_id = str(article.get("id") or "").strip()
        source = str(article.get("source") or "").strip() or "openreview"
        title = str(article.get("title") or "").strip()
        abstract = str(article.get("abstract") or "").strip()
        if not openreview_id or not abstract or not (title or abstract):
            continue

        forum = str(article.get("forum") or "").strip()
        venue = str(article.get("venue") or "").strip()
        venue_id = str(article.get("venue_id") or "").strip()
        journal = venue or venue_id

        row_id = len(records)
        records.append(
            {
                "row_id": row_id,
                "source": source,
                "external_id": openreview_id,
                "openreview_id": openreview_id,
                "forum": forum,
                "number": article.get("number") or "",
                "title": title,
                "abstract": abstract,
                "journal": journal,
                "pub_date": article.get("cdate") or "",
                "updated_date": article.get("mdate") or "",
                "authors": _parse_authors(article.get("authors")),
                "url": f"https://openreview.net/forum?id={forum or openreview_id}",
                "pdf_url": article.get("pdf_url") or "",
                "venue_id": venue_id,
                "venue": venue,
                "venueid": article.get("venueid") or "",
                "decision": article.get("decision") or "",
                "status": article.get("status") or "",
                "presentation": article.get("presentation") or "",
                "classification": article.get("classification") or "",
                "readers": _parse_json_list(article.get("readers")),
            }
        )
    return records


def _metadata_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return value


def unified_records_to_index_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    index_records: list[dict[str, Any]] = []
    for record in records:
        title = str(record.get("title") or "").strip()
        abstract = str(record.get("abstract") or "").strip()
        source = str(record.get("source") or "").strip()
        external_id = str(record.get("external_id") or "").strip()
        if not source or not external_id or not abstract or not (title or abstract):
            continue

        metadata_record = {key: _metadata_value(value) for key, value in record.items()}
        metadata_record["row_id"] = len(index_records)
        metadata_record["title"] = title
        metadata_record["abstract"] = abstract
        metadata_record["source"] = source
        metadata_record["external_id"] = external_id
        metadata_record["pub_date"] = metadata_record.get("published_date")
        index_records.append(metadata_record)
    return index_records


def _populated_field_count(record: dict[str, Any]) -> int:
    return sum(1 for value in record.values() if value not in (None, "", [], {}))


def _date_sort_value(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        numeric = float(text)
        if numeric > 1_000_000_000_000:
            numeric /= 1000.0
        return numeric
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _dedup_sort_key(record: dict[str, Any]) -> tuple[int, int, float]:
    has_abstract = 1 if str(record.get("abstract") or "").strip() else 0
    populated_count = _populated_field_count(record)
    newest_date = max(_date_sort_value(record.get("updated_date")), _date_sort_value(record.get("fetched_at")))
    return has_abstract, populated_count, newest_date


def deduplicate_unified_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        source = str(record.get("source") or "").strip()
        external_id = str(record.get("external_id") or "").strip()
        if not source or not external_id:
            continue
        key = (source, external_id)
        current = by_key.get(key)
        if current is None or _dedup_sort_key(record) > _dedup_sort_key(current):
            by_key[key] = record

    deduped = sorted(by_key.values(), key=lambda item: (str(item.get("source")), str(item.get("external_id"))))
    return deduped, len(records) - len(deduped)


def _is_excluded_sqlite_filename(filename: str) -> bool:
    lower = filename.lower()
    if not lower.endswith(".sqlite"):
        return True
    if lower == "all.sqlite":
        return True
    excluded_fragments = (
        ".tmp",
        "tmp",
        "temp",
        "backup",
        ".bak",
        "cache",
        "test",
    )
    return any(fragment in lower for fragment in excluded_fragments)


def discover_source_sqlite_files(directory: str, output_db_path: str) -> list[str]:
    output_resolved = os.path.abspath(output_db_path)
    discovered: list[str] = []
    for filename in os.listdir(directory):
        if _is_excluded_sqlite_filename(filename):
            continue
        if filename.endswith((".sqlite-wal", ".sqlite-shm", ".sqlite-journal")):
            continue

        path = os.path.join(directory, filename)
        if not os.path.isfile(path):
            continue
        if os.path.abspath(path) == output_resolved:
            continue

        try:
            conn = sqlite3.connect(path)
            try:
                conn.execute("PRAGMA quick_check").fetchone()
                _detect_existing_db_source_type(conn, path)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            print(f"[warn] Skipping invalid SQLite database: {filename} ({exc})")
            continue
        except RuntimeError:
            print(f"[warn] Skipping unsupported SQLite database: {filename}")
            continue
        discovered.append(path)

    return sorted(discovered)


def create_all_sqlite_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE papers (
            paper_key TEXT PRIMARY KEY,
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
            pmid TEXT,
            arxiv_id TEXT,
            rss_id TEXT,
            version TEXT,
            article_type TEXT,
            license TEXT,
            server TEXT,
            category TEXT,
            primary_category TEXT,
            feed_url TEXT,
            openreview_id TEXT,
            forum TEXT,
            number TEXT,
            venue_id TEXT,
            venue TEXT,
            venueid TEXT,
            decision TEXT,
            status TEXT,
            presentation TEXT,
            classification TEXT,
            readers TEXT,
            raw_content TEXT,
            source_db TEXT,
            UNIQUE(source, external_id)
        )
        """
    )
    conn.execute("CREATE INDEX idx_papers_source ON papers(source)")
    conn.execute("CREATE INDEX idx_papers_doi ON papers(doi)")
    conn.execute("CREATE INDEX idx_papers_published_date ON papers(published_date)")
    conn.execute("CREATE INDEX idx_papers_source_external_id ON papers(source, external_id)")


def write_all_sqlite(records: list[dict[str, Any]], output_db_path: str) -> None:
    tmp_path = f"{output_db_path}.tmp"
    directory = os.path.dirname(output_db_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    if os.path.exists(tmp_path):
        os.remove(tmp_path)

    placeholders = ", ".join(["?"] * len(ALL_SQLITE_COLUMNS))
    columns_sql = ", ".join(ALL_SQLITE_COLUMNS)
    try:
        conn = sqlite3.connect(tmp_path)
        try:
            with conn:
                create_all_sqlite_schema(conn)
                conn.executemany(
                    f"INSERT INTO papers ({columns_sql}) VALUES ({placeholders})",
                    [[record.get(column) for column in ALL_SQLITE_COLUMNS] for record in records],
                )
            row = conn.execute("SELECT COUNT(*) FROM papers").fetchone()
            written_count = int(row[0]) if row else 0
            if written_count != len(records):
                raise RuntimeError(
                    f"Output database validation failed: expected {len(records)} rows, found {written_count}."
                )
            conn.execute("PRAGMA quick_check").fetchone()
        finally:
            conn.close()
        os.replace(tmp_path, output_db_path)
    except (sqlite3.Error, OSError) as exc:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise RuntimeError(f"Output database cannot be created: {output_db_path}: {exc}") from exc


def merge_sqlite_databases(
    input_db_paths: list[str],
    output_db_path: str,
) -> dict[str, Any]:
    if not input_db_paths:
        raise RuntimeError("No supported source SQLite files were found.")

    all_records: list[dict[str, Any]] = []
    per_database_counts: dict[str, int] = {}
    per_source_counts_before_dedup: dict[str, int] = {}
    for db_path in input_db_paths:
        print(f"[info] Loading {os.path.basename(db_path)}")
        db_source_type, articles = load_articles_from_existing_db(db_path)
        records = articles_to_unified_records(db_source_type, articles, db_path)
        per_database_counts[os.path.basename(db_path)] = len(records)
        for record in records:
            source = str(record.get("source") or "unknown")
            per_source_counts_before_dedup[source] = per_source_counts_before_dedup.get(source, 0) + 1
        all_records.extend(records)

    deduped_records, duplicates_removed = deduplicate_unified_records(all_records)
    per_source_counts: dict[str, int] = {}
    for record in deduped_records:
        source = str(record.get("source") or "unknown")
        per_source_counts[source] = per_source_counts.get(source, 0) + 1

    write_all_sqlite(deduped_records, output_db_path)
    return {
        "records": deduped_records,
        "input_databases": [os.path.basename(path) for path in input_db_paths],
        "num_input_databases": len(input_db_paths),
        "per_database_counts": per_database_counts,
        "per_source_counts_before_dedup": per_source_counts_before_dedup,
        "per_source_counts": per_source_counts,
        "num_rows_before_deduplication": len(all_records),
        "num_duplicates_removed": duplicates_removed,
        "num_rows_after_deduplication": len(deduped_records),
    }


def _load_sentence_transformer(model_name: str):
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "Missing sentence-transformers dependency. Install with `pip install -r requirements.txt`."
        ) from exc

    try:
        return SentenceTransformer(model_name)
    except Exception as exc:
        raise RuntimeError(
            f"Could not load embedding model `{model_name}`. Ensure internet access for the first download."
        ) from exc


def encode_texts_with_specter(
    texts: list[str],
    model_name: str,
    batch_size: int = 64,
) -> np.ndarray:
    if not texts:
        raise ValueError("No texts provided for embedding.")
    model = _load_sentence_transformer(model_name)
    try:
        embeddings = model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=True,
        )
    except Exception as exc:
        raise RuntimeError(
            "Embedding failed. Install dependencies with `pip install -r requirements.txt` "
            "and ensure internet access for the first model download."
        ) from exc
    return np.ascontiguousarray(embeddings, dtype=np.float32)


def build_faiss_index(embeddings: np.ndarray):
    faiss = _load_faiss()
    embeddings = np.ascontiguousarray(embeddings, dtype=np.float32)
    if embeddings.ndim != 2 or embeddings.shape[0] == 0:
        raise ValueError("No embeddings available for FAISS index building.")
    dimension = embeddings.shape[1]
    index = faiss.IndexFlatIP(dimension)
    index.add(embeddings)
    return index


def save_index_artifacts(
    index: Any,
    metadata: list[dict[str, Any]],
    manifest: dict[str, Any],
    index_path: str,
    metadata_path: str,
    manifest_path: str,
) -> None:
    faiss = _load_faiss()
    for path in (index_path, metadata_path, manifest_path):
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)

    faiss.write_index(index, index_path)
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def build_index_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    start_total = time.perf_counter()
    try:
        from base import configure_entrez, get_ncbi_config, init_db, load_dotenv
    except ImportError as exc:
        raise RuntimeError("PubMed fetching requires dependencies from requirements.txt.") from exc

    load_dotenv()
    cfg = get_ncbi_config()
    configure_entrez(cfg, max_tries=None, sleep_between_tries=None)

    query = build_pubmed_all_query(args.require_abstract)

    start_fetch = time.perf_counter()
    pmids = fetch_pubmed_last24h_all(
        db_path=args.db,
        query=query,
        fetch_batch=args.fetch_batch,
        fetch_retries=args.fetch_retries,
        refresh=args.refresh,
    )
    fetch_elapsed = time.perf_counter() - start_fetch

    conn = init_db(args.db)
    try:
        articles = load_articles_by_pmids(conn, pmids)
    finally:
        conn.close()

    index_records = articles_to_index_records(articles)
    skipped_count = len(articles) - len(index_records)
    if not index_records:
        raise RuntimeError("No eligible papers with abstracts were available for indexing.")

    texts = [build_paper_text(record["title"], record["abstract"]) for record in index_records]

    print(f"[info] Encoding {len(texts)} papers with {args.model_name}")
    start_embedding = time.perf_counter()
    embeddings = encode_texts_with_specter(texts, args.model_name)
    embedding_elapsed = time.perf_counter() - start_embedding

    start_faiss = time.perf_counter()
    index = build_faiss_index(embeddings)
    faiss_elapsed = time.perf_counter() - start_faiss
    total_elapsed = time.perf_counter() - start_total

    manifest = {
        "model_name": args.model_name,
        "index_path": args.index_path,
        "metadata_path": args.metadata_path,
        "manifest_path": args.manifest_path,
        "pubmed_query": query,
        "datetype": "edat",
        "reldate": 1,
        "fetch_time_window": "past 24 hours",
        "embedding_normalized": True,
        "faiss_index_type": "IndexFlatIP",
        "vector_dimension": int(embeddings.shape[1]),
        "num_indexed_papers": len(index_records),
        "num_fetched_pmids": len(pmids),
        "num_loaded_articles": len(articles),
        "num_skipped_papers": skipped_count,
        "built_at": now_iso(),
        "elapsed_seconds": {
            "fetch": fetch_elapsed,
            "embedding": embedding_elapsed,
            "faiss_index": faiss_elapsed,
            "total": total_elapsed,
        },
    }

    save_index_artifacts(
        index=index,
        metadata=index_records,
        manifest=manifest,
        index_path=args.index_path,
        metadata_path=args.metadata_path,
        manifest_path=args.manifest_path,
    )

    print("\nBuild summary")
    print(f"- PubMed query: {query}")
    print(f"- fetched PMIDs: {len(pmids)}")
    print(f"- indexed papers: {len(index_records)}")
    print(f"- skipped papers without enough text: {skipped_count}")
    print(f"- model name: {args.model_name}")
    print(f"- FAISS index path: {args.index_path}")
    print(f"- metadata path: {args.metadata_path}")
    print(f"- manifest path: {args.manifest_path}")
    print(f"- fetch time: {format_seconds(fetch_elapsed)}")
    print(f"- embedding time: {format_seconds(embedding_elapsed)}")
    print(f"- FAISS build time: {format_seconds(faiss_elapsed)}")
    print(f"- total time: {format_seconds(total_elapsed)}")
    return manifest


def build_index_from_existing_db_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    start_total = time.perf_counter()

    start_load = time.perf_counter()
    db_source_type, articles = load_articles_from_existing_db(args.build_from_existing_db)
    load_elapsed = time.perf_counter() - start_load
    if not articles:
        table_name = EXISTING_DB_TABLES[db_source_type][0]
        raise RuntimeError(f"No rows found in {table_name} table: {args.build_from_existing_db}")

    if db_source_type == "pubmed":
        index_records = articles_to_index_records(articles)
    elif db_source_type == "arxiv":
        index_records = articles_to_index_records_arxiv(articles)
    elif db_source_type in {"biorxiv", "medrxiv"}:
        index_records = articles_to_index_records_biorxiv(articles)
    elif db_source_type == "rss":
        index_records = articles_to_index_records_rss(articles)
    elif db_source_type == "openreview":
        index_records = articles_to_index_records_openreview(articles)
    else:
        index_records = articles_to_index_records_papers(articles)
    skipped_count = len(articles) - len(index_records)
    if not index_records:
        raise RuntimeError("No eligible papers with abstracts were available for indexing.")

    texts = [build_paper_text(record["title"], record["abstract"]) for record in index_records]

    print(f"[info] Loaded {len(articles)} articles from {args.build_from_existing_db}")
    print(f"[info] Encoding {len(texts)} papers with {args.model_name}")
    start_embedding = time.perf_counter()
    embeddings = encode_texts_with_specter(texts, args.model_name)
    embedding_elapsed = time.perf_counter() - start_embedding

    start_faiss = time.perf_counter()
    index = build_faiss_index(embeddings)
    faiss_elapsed = time.perf_counter() - start_faiss
    total_elapsed = time.perf_counter() - start_total

    manifest = {
        "model_name": args.model_name,
        "index_path": args.index_path,
        "metadata_path": args.metadata_path,
        "manifest_path": args.manifest_path,
        "source": "existing_sqlite_db",
        "db_source_type": db_source_type,
        "db_path": args.build_from_existing_db,
        "embedding_normalized": True,
        "faiss_index_type": "IndexFlatIP",
        "vector_dimension": int(embeddings.shape[1]),
        "num_indexed_papers": len(index_records),
        "num_loaded_articles": len(articles),
        "num_skipped_papers": skipped_count,
        "built_at": now_iso(),
        "elapsed_seconds": {
            "load_db": load_elapsed,
            "embedding": embedding_elapsed,
            "faiss_index": faiss_elapsed,
            "total": total_elapsed,
        },
    }

    save_index_artifacts(
        index=index,
        metadata=index_records,
        manifest=manifest,
        index_path=args.index_path,
        metadata_path=args.metadata_path,
        manifest_path=args.manifest_path,
    )

    print("\nBuild summary")
    print(f"- source DB: {args.build_from_existing_db}")
    print(f"- DB source type: {db_source_type}")
    print(f"- loaded articles: {len(articles)}")
    print(f"- indexed papers: {len(index_records)}")
    print(f"- skipped papers without enough text: {skipped_count}")
    print(f"- model name: {args.model_name}")
    print(f"- FAISS index path: {args.index_path}")
    print(f"- metadata path: {args.metadata_path}")
    print(f"- manifest path: {args.manifest_path}")
    print(f"- DB load time: {format_seconds(load_elapsed)}")
    print(f"- embedding time: {format_seconds(embedding_elapsed)}")
    print(f"- FAISS build time: {format_seconds(faiss_elapsed)}")
    print(f"- total time: {format_seconds(total_elapsed)}")
    return manifest


def build_index_from_unified_records(
    records: list[dict[str, Any]],
    args: argparse.Namespace,
    manifest: dict[str, Any],
    total_loaded_rows: int,
    start_total: float,
) -> dict[str, Any]:
    index_records = unified_records_to_index_records(records)
    skipped_count = total_loaded_rows - len(index_records)
    if not index_records:
        raise RuntimeError("No eligible papers contain sufficient text for indexing.")

    texts = [build_paper_text(record["title"], record["abstract"]) for record in index_records]
    print(f"[info] Eligible for embedding: {len(index_records)}")
    print(f"[info] Skipped: {skipped_count}")
    print(f"[info] Encoding {len(texts)} papers with {args.model_name}")
    start_embedding = time.perf_counter()
    embeddings = encode_texts_with_specter(texts, args.model_name)
    embedding_elapsed = time.perf_counter() - start_embedding

    start_faiss = time.perf_counter()
    index = build_faiss_index(embeddings)
    faiss_elapsed = time.perf_counter() - start_faiss
    if index.ntotal != len(index_records):
        raise RuntimeError(
            f"FAISS index and metadata counts do not match: index.ntotal={index.ntotal}, "
            f"metadata={len(index_records)}."
        )

    total_elapsed = time.perf_counter() - start_total
    manifest.update(
        {
            "model_name": args.model_name,
            "index_path": args.index_path,
            "metadata_path": args.metadata_path,
            "manifest_path": args.manifest_path,
            "embedding_normalized": True,
            "faiss_index_type": "IndexFlatIP",
            "vector_dimension": int(embeddings.shape[1]),
            "num_indexed_papers": len(index_records),
            "num_skipped_papers": skipped_count,
            "built_at": now_iso(),
            "elapsed_seconds": total_elapsed,
            "timings": {
                "embedding": embedding_elapsed,
                "faiss_index": faiss_elapsed,
                "total": total_elapsed,
            },
        }
    )

    save_index_artifacts(
        index=index,
        metadata=index_records,
        manifest=manifest,
        index_path=args.index_path,
        metadata_path=args.metadata_path,
        manifest_path=args.manifest_path,
    )
    return manifest


def build_single_database_positional_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    start_total = time.perf_counter()
    db_path = args.sqlite_target
    print("[info] Stage 1 single-database mode")
    print(f"[info] Source database: {db_path}")

    start_load = time.perf_counter()
    db_source_type, articles = load_articles_from_existing_db(db_path)
    load_elapsed = time.perf_counter() - start_load
    if not articles:
        table_name = EXISTING_DB_TABLES[db_source_type][0]
        raise RuntimeError(f"No rows found in {table_name} table: {db_path}")

    records = articles_to_unified_records(db_source_type, articles, db_path)
    print(f"[info] Detected source type: {db_source_type}")
    print(f"[info] Loaded articles: {len(articles)}")

    manifest = {
        "mode": "single_database",
        "db_path": db_path,
        "db_source_type": db_source_type,
        "num_loaded_articles": len(articles),
        "load_elapsed_seconds": load_elapsed,
    }
    manifest = build_index_from_unified_records(
        records=records,
        args=args,
        manifest=manifest,
        total_loaded_rows=len(articles),
        start_total=start_total,
    )

    print("\nBuild summary")
    print(f"- source DB: {db_path}")
    print(f"- FAISS index: {args.index_path}")
    print(f"- metadata: {args.metadata_path}")
    print(f"- manifest: {args.manifest_path}")
    return manifest


def build_merged_databases_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    start_total = time.perf_counter()
    output_db_path = args.sqlite_target
    print("[info] Stage 1 merged-database mode")
    print("[info] Merge mode selected")
    print(f"[info] Output database: {output_db_path}")

    input_db_paths = discover_source_sqlite_files(os.getcwd(), output_db_path)
    if not input_db_paths:
        raise RuntimeError("No supported source SQLite files were found.")

    print(f"[info] Discovered {len(input_db_paths)} source databases:")
    for db_path in input_db_paths:
        print(f"- {os.path.basename(db_path)}")

    merge_result = merge_sqlite_databases(input_db_paths, output_db_path)
    records = merge_result["records"]
    print(f"[info] Rows before deduplication: {merge_result['num_rows_before_deduplication']}")
    print(f"[info] Duplicates removed: {merge_result['num_duplicates_removed']}")
    print(f"[info] Rows written to {output_db_path}: {merge_result['num_rows_after_deduplication']}")

    manifest = {
        "mode": "merged_databases",
        "output_db_path": output_db_path,
        "input_databases": merge_result["input_databases"],
        "num_input_databases": merge_result["num_input_databases"],
        "per_database_counts": merge_result["per_database_counts"],
        "per_source_counts": merge_result["per_source_counts"],
        "num_rows_before_deduplication": merge_result["num_rows_before_deduplication"],
        "num_duplicates_removed": merge_result["num_duplicates_removed"],
        "num_rows_after_deduplication": merge_result["num_rows_after_deduplication"],
    }
    manifest = build_index_from_unified_records(
        records=records,
        args=args,
        manifest=manifest,
        total_loaded_rows=len(records),
        start_total=start_total,
    )

    print("\nBuild summary")
    print(f"- merged SQLite: {output_db_path}")
    print(f"- input databases: {len(input_db_paths)}")
    print(f"- merged rows: {merge_result['num_rows_after_deduplication']}")
    print(f"- indexed papers: {manifest['num_indexed_papers']}")
    print(f"- FAISS index: {args.index_path}")
    print(f"- metadata: {args.metadata_path}")
    print(f"- manifest: {args.manifest_path}")
    return manifest


def load_index_artifacts(
    index_path: str,
    metadata_path: str,
    manifest_path: str,
) -> tuple[Any, list[dict[str, Any]], dict[str, Any]]:
    missing = [path for path in (index_path, metadata_path, manifest_path) if not os.path.exists(path)]
    if missing:
        missing_list = ", ".join(missing)
        raise RuntimeError(
            "No FAISS index artifacts found "
            f"(missing: {missing_list}). "
            "Build one first with `python All_embedding.py arxiv.sqlite` or "
            "`python All_embedding.py all.sqlite`. Then search with "
            "`python All_embedding.py --interest \"...\" --all --limit 10`, "
            "`--arxiv`, or explicit `--index-path`, `--metadata-path`, and "
            "`--manifest-path` values."
        )

    faiss = _load_faiss()
    index = faiss.read_index(index_path)
    with open(metadata_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    if not isinstance(metadata, list):
        raise RuntimeError("Metadata file is invalid: expected a list.")
    if index.ntotal != len(metadata):
        raise RuntimeError(
            f"FAISS index and metadata length mismatch: index.ntotal={index.ntotal}, "
            f"metadata={len(metadata)}."
        )
    return index, metadata, manifest


def encode_query_with_specter(interest: str, model_name: str) -> np.ndarray:
    interest = interest.strip()
    if not interest:
        raise ValueError("--interest cannot be empty.")
    embeddings = encode_texts_with_specter([interest], model_name)
    return embeddings[0]


def search_faiss_index(index: Any, query_embedding: np.ndarray, limit: int) -> tuple[np.ndarray, np.ndarray]:
    if limit <= 0:
        raise ValueError("--limit must be greater than 0 when provided.")
    query_embedding = np.ascontiguousarray(query_embedding.reshape(1, -1), dtype=np.float32)
    scores, indices = index.search(query_embedding, limit)
    return scores[0], indices[0]


def build_search_results(
    scores: np.ndarray,
    indices: np.ndarray,
    metadata: list[dict[str, Any]],
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for score, idx in zip(scores, indices):
        idx = int(idx)
        if idx < 0 or idx >= len(metadata):
            continue
        score_value = float(score)
        if min_score is not None and score_value < min_score:
            continue
        item = dict(metadata[idx])
        item["score"] = score_value
        results.append(item)
    return results


def _format_authors(authors: Any) -> str:
    if not authors:
        return "N/A"
    if not isinstance(authors, list):
        authors = [str(authors)]
    authors = [str(author) for author in authors if str(author).strip()]
    if not authors:
        return "N/A"
    if len(authors) > 3:
        return f"{', '.join(authors[:3])}, et al."
    return ", ".join(authors)


def _abstract_preview(abstract: Any, limit: int = 500) -> str:
    text = " ".join(str(abstract or "").split())
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def print_search_results(results: list[dict[str, Any]]) -> None:
    if not results:
        print("[done] No matching papers found.")
        return

    for rank, item in enumerate(results, start=1):
        doi = str(item.get("doi") or "").strip()
        print(f"#{rank} | score={float(item.get('score', 0.0)):.4f}")
        print(f"Title: {item.get('title') or 'N/A'}")
        print(f"PMID: {item.get('pmid') or 'N/A'}")
        print(f"Journal: {item.get('journal') or 'N/A'}")
        print(f"Date: {item.get('pub_date') or 'N/A'}")
        if doi:
            print(f"DOI: {doi}")
        print(f"Authors: {_format_authors(item.get('authors'))}")
        print(f"URL: {item.get('url') or 'N/A'}")
        print(f"Abstract: {_abstract_preview(item.get('abstract'))}")
        print()


def search_index_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    start_total = time.perf_counter()

    start_load = time.perf_counter()
    index, metadata, manifest = load_index_artifacts(
        args.index_path,
        args.metadata_path,
        args.manifest_path,
    )
    load_elapsed = time.perf_counter() - start_load

    manifest_model_name = str(manifest.get("model_name") or "").strip()
    model_name = args.model_name
    if manifest_model_name:
        if args.model_name != DEFAULT_MODEL_NAME and args.model_name != manifest_model_name:
            print(
                f"[warn] Requested model `{args.model_name}` differs from index model "
                f"`{manifest_model_name}`. Query embeddings should use the same model."
            )
        else:
            model_name = manifest_model_name

    start_embed = time.perf_counter()
    query_embedding = encode_query_with_specter(args.interest, model_name)
    embed_elapsed = time.perf_counter() - start_embed

    if args.limit is None:
        k = index.ntotal
        print(f"[warn] No --limit provided; printing all {index.ntotal} indexed papers.")
    else:
        k = min(args.limit, index.ntotal)

    start_search = time.perf_counter()
    scores, indices = search_faiss_index(index, query_embedding, k)
    search_elapsed = time.perf_counter() - start_search
    results = build_search_results(scores, indices, metadata, args.min_score)
    total_elapsed = time.perf_counter() - start_total

    print("\nSearch summary")
    print(f"- loaded index path: {args.index_path}")
    print(f"- indexed papers: {index.ntotal}")
    print(f"- user interest: {args.interest}")
    print(f"- limit used: {k}")
    print(f"- min score: {args.min_score if args.min_score is not None else 'none'}")
    print(f"- model name: {model_name}")
    print(f"- load time: {format_seconds(load_elapsed)}")
    print(f"- query embedding time: {format_seconds(embed_elapsed)}")
    print(f"- FAISS search time: {format_seconds(search_elapsed)}")
    print(f"- total time: {format_seconds(total_elapsed)}")
    print()
    print_search_results(results)

    return {
        "num_results": len(results),
        "load_elapsed": load_elapsed,
        "embedding_elapsed": embed_elapsed,
        "search_elapsed": search_elapsed,
        "total_elapsed": total_elapsed,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and search persistent SPECTER FAISS indexes for scientific SQLite databases."
    )
    parser.add_argument(
        "sqlite_target",
        nargs="?",
        help=(
            "SQLite database to index. Use `all.sqlite` to merge all supported "
            "SQLite databases before building one combined index."
        ),
    )
    parser.add_argument("--build-index", action="store_true", help="Build the PubMed past-24h SPECTER FAISS index")
    parser.add_argument(
        "--build-from-existing-db",
        metavar="DB_PATH",
        default="",
        help=(
            "Build the SPECTER FAISS index from an existing pubmed_articles, "
            "arxiv_articles, biorxiv_articles, medrxiv_articles, rss_articles, or unified papers SQLite database"
        ),
    )
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    parser.add_argument(
        "--index-path",
        default=DEFAULT_INDEX_PATH,
        help=f"FAISS index path (default: {DEFAULT_INDEX_PATH})",
    )
    parser.add_argument(
        "--metadata-path",
        default=DEFAULT_METADATA_PATH,
        help=f"Metadata JSON path (default: {DEFAULT_METADATA_PATH})",
    )
    parser.add_argument(
        "--manifest-path",
        default=DEFAULT_MANIFEST_PATH,
        help=f"Manifest JSON path (default: {DEFAULT_MANIFEST_PATH})",
    )
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME, help="SentenceTransformer model name")
    parser.add_argument("--fetch-batch", type=int, default=200, help="EFetch batch size")
    parser.add_argument("--fetch-retries", type=int, default=3, help="Retries for failed EFetch requests")
    parser.add_argument(
        "--require-abstract",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use all[sb] AND hasabstract for PubMed retrieval (default: true)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Replace existing DB rows for fetched PMIDs before insertion",
    )
    parser.add_argument("--interest", default="", help="Specific user interest to search against the saved index")
    parser.add_argument("--limit", type=int, default=None, help="Return top K matches; omit to return all")
    parser.add_argument("--min-score", type=float, default=None, help="Only print results with score >= min score")
    artifact_group = parser.add_mutually_exclusive_group()
    for target in SEARCH_ARTIFACT_TARGETS:
        artifact_group.add_argument(
            f"--{target}",
            dest="artifact_target",
            action="store_const",
            const=target,
            help=f"Use {target}_specter.index, {target}_metadata.json, and {target}_manifest.json for search",
        )
    parser.set_defaults(artifact_target="")
    argv = _normalize_cli_argv(sys.argv[1:])
    args = parser.parse_args(argv)

    index_path_explicit = any(arg == "--index-path" or arg.startswith("--index-path=") for arg in argv)
    metadata_path_explicit = any(arg == "--metadata-path" or arg.startswith("--metadata-path=") for arg in argv)
    manifest_path_explicit = any(arg == "--manifest-path" or arg.startswith("--manifest-path=") for arg in argv)
    if (
        args.interest
        and args.sqlite_target in SEARCH_ARTIFACT_TARGETS
        and not args.artifact_target
        and not os.path.exists(str(args.sqlite_target))
    ):
        args.artifact_target = args.sqlite_target
        args.sqlite_target = None

    if args.artifact_target:
        derived_index_path, derived_metadata_path, derived_manifest_path = derive_artifact_paths_for_target(
            args.artifact_target
        )
        if not index_path_explicit:
            args.index_path = derived_index_path
        if not metadata_path_explicit:
            args.metadata_path = derived_metadata_path
        if not manifest_path_explicit:
            args.manifest_path = derived_manifest_path

    if args.sqlite_target:
        derived_index_path, derived_metadata_path, derived_manifest_path = derive_artifact_paths(args.sqlite_target)
        if not index_path_explicit:
            args.index_path = derived_index_path
        if not metadata_path_explicit:
            args.metadata_path = derived_metadata_path
        if not manifest_path_explicit:
            args.manifest_path = derived_manifest_path
    return args


def main() -> int:
    args = _parse_args()

    try:
        if not args.sqlite_target and not args.build_index and not args.build_from_existing_db and not args.interest:
            print(
                "Choose a mode: run `python All_embedding.py arxiv.sqlite`, "
                "`python All_embedding.py all.sqlite`, "
                "`python All_embedding.py --build-index`, "
                "`python All_embedding.py --build-from-existing-db pubmed.sqlite`, "
                "or `python All_embedding.py --interest \"...\" --all --limit 10`."
            )
            return 1

        if args.sqlite_target:
            if _sqlite_target_is_merge_mode(args.sqlite_target):
                build_merged_databases_pipeline(args)
            else:
                build_single_database_positional_pipeline(args)

        if args.build_index:
            build_index_pipeline(args)

        if args.build_from_existing_db:
            build_index_from_existing_db_pipeline(args)

        if args.interest:
            search_index_pipeline(args)

        return 0
    except KeyboardInterrupt:
        print("\n[error] Interrupted.", file=sys.stderr)
        return 130
    except (RuntimeError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
