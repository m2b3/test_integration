"""
Build and search a persistent FAISS index of literature metadata.
Stage 1:
  python All_embedding.py --build-index
  python All_embedding.py --build-from-existing-db medrxiv.sqlite

Stage 2:
  python All_embedding.py --interest "your interest query"
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
    if _table_exists(conn, "medrxiv_articles"):
        return "medrxiv"
    if _table_exists(conn, "biorxiv_articles"):
        return "biorxiv"
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

            rows = conn.execute(
                f"SELECT {', '.join(columns)} FROM {table_name} ORDER BY {order_by}"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise RuntimeError(f"Could not read SQLite database `{db_path}`: {exc}") from exc

    return db_source_type, [dict(zip(columns, row)) for row in rows]


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
        journal = str(article.get("journal") or "").strip()
        if not journal:
            journal = "medRxiv" if server == "medrxiv" else "bioRxiv"

        row_id = len(records)
        records.append(
            {
                "row_id": row_id,
                "source": "biorxiv",
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


def articles_to_index_records_medrxiv(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for article in articles:
        doi = str(article.get("doi") or "").strip()
        title = str(article.get("title") or "").strip()
        abstract = str(article.get("abstract") or "").strip()
        if not doi or not abstract:
            continue

        row_id = len(records)
        records.append(
            {
                "row_id": row_id,
                "source": "medrxiv",
                "external_id": doi,
                "doi": doi,
                "title": title,
                "abstract": abstract,
                "journal": article.get("journal") or "medRxiv",
                "pub_date": article.get("pub_date") or "",
                "updated_date": article.get("updated_date") or "",
                "version": article.get("version") or "",
                "type": article.get("type") or "",
                "license": article.get("license") or "",
                "authors": _parse_authors(article.get("authors")),
                "category": article.get("category") or "",
                "server": article.get("server") or "medrxiv",
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
    elif db_source_type == "biorxiv":
        index_records = articles_to_index_records_biorxiv(articles)
    elif db_source_type == "medrxiv":
        index_records = articles_to_index_records_medrxiv(articles)
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


def load_index_artifacts(
    index_path: str,
    metadata_path: str,
    manifest_path: str,
) -> tuple[Any, list[dict[str, Any]], dict[str, Any]]:
    missing = [path for path in (index_path, metadata_path, manifest_path) if not os.path.exists(path)]
    if missing:
        raise RuntimeError("No FAISS index found. Run `python embedding.py --build-index` first.")

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
        source = str(item.get("source") or "").strip()
        external_id = str(item.get("external_id") or item.get("pmid") or item.get("arxiv_id") or doi or "").strip()
        id_label = "External ID"
        if source == "pubmed":
            id_label = "PMID"
        elif source == "arxiv":
            id_label = "arXiv ID"
        elif source in {"biorxiv", "medrxiv"}:
            id_label = "DOI"
        print(f"#{rank} | score={float(item.get('score', 0.0)):.4f}")
        print(f"Title: {item.get('title') or 'N/A'}")
        print(f"{id_label}: {external_id or 'N/A'}")
        print(f"Journal: {item.get('journal') or 'N/A'}")
        print(f"Date: {item.get('pub_date') or 'N/A'}")
        if doi and id_label != "DOI":
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
        description="Build and search a persistent SPECTER FAISS index for supported literature SQLite databases."
    )
    parser.add_argument("--build-index", action="store_true", help="Build the PubMed past-24h SPECTER FAISS index")
    parser.add_argument(
        "--build-from-existing-db",
        metavar="DB_PATH",
        default="",
        help=(
            "Build the SPECTER FAISS index from an existing pubmed_articles, "
            "arxiv_articles, biorxiv_articles, medrxiv_articles, rss_articles, "
            "or unified papers SQLite database"
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
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    try:
        if not args.build_index and not args.build_from_existing_db and not args.interest:
            print(
                "Choose a mode: run `python All_embedding.py --build-index`, "
                "`python All_embedding.py --build-from-existing-db medrxiv.sqlite`, "
                "or `python All_embedding.py --interest \"...\"`."
            )
            return 1

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
