from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from typing import Any, Iterable
from urllib.error import HTTPError, URLError

import numpy as np

from base import (
    chunked,
    configure_entrez,
    efetch_pubmed_batch,
    esearch_last_24h,
    existing_pmids,
    get_ncbi_config,
    init_db,
    insert_articles,
    load_dotenv,
)


DEFAULT_MODEL_NAME = "sentence-transformers/allenai-specter"
DEFAULT_DB_PATH = "pubmed.sqlite"
TOPIC_CHOICES = [
    ("cancer", "Cancer / oncology"),
    ("genomics", "Genomics"),
    ("cardiovascular", "Cardiovascular"),
    ("neuroscience", "Neuroscience"),
    ("immunology", "Immunology"),
]
INTEREST_EXAMPLES = [
    "single-cell genomics for early cancer biomarker discovery",
    "genomic biomarkers for breast cancer prognosis",
    "machine learning for cardiovascular risk prediction in clinical cohorts",
]


def build_pubmed_query(topic: str, require_abstract: bool = True) -> str:
    topic = topic.strip()
    if not topic:
        raise ValueError("Topic cannot be empty.")
    query = f"({topic})"
    if require_abstract:
        query = f"{query} AND hasabstract[text]"
    return query


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


def fetch_topic_candidates(
    db_path: str,
    query: str,
    fetch_batch: int,
    fetch_retries: int,
    refresh: bool = False,
) -> list[str]:
    if fetch_batch <= 0:
        raise ValueError("--fetch-batch must be greater than 0.")
    if fetch_retries <= 0:
        raise ValueError("--fetch-retries must be greater than 0.")

    cfg = get_ncbi_config()
    conn = init_db(db_path)
    try:
        count, webenv, query_key = esearch_last_24h(cfg, query)
        print(f"[info] PubMed query: {query}")
        print(f"[info] Found {count} PubMed records in the past 24h (reldate=1, datetype=edat).")

        if count == 0:
            print("[done] No PubMed candidates found for this topic.")
            return []

        retstart = 0
        total_fetched = 0
        total_inserted = 0
        run_pmids: list[str] = []

        while retstart < count:
            this_batch = min(fetch_batch, count - retstart)
            records = efetch_pubmed_batch(
                webenv,
                query_key,
                retstart,
                this_batch,
                max_retries=fetch_retries,
            )
            retstart += this_batch

            pmids = [str(r["pmid"]) for r in records if r.get("pmid")]
            run_pmids.extend(pmids)
            total_fetched += len(pmids)

            if refresh:
                _delete_articles_by_pmids(conn, pmids)
                records_to_insert = [r for r in records if r.get("pmid")]
            else:
                already = existing_pmids(conn, pmids)
                records_to_insert = [r for r in records if r.get("pmid") and str(r["pmid"]) not in already]

            inserted = insert_articles(conn, records_to_insert)
            total_inserted += inserted
            print(
                f"[page] retstart={retstart - this_batch} "
                f"fetched={len(pmids)} inserted={inserted} "
                f"total_fetched={total_fetched}/{count}"
            )

        print(f"[done] fetched={total_fetched} inserted={total_inserted} db={db_path}")
        return _unique_pmids(run_pmids)
    except HTTPError as exc:
        raise RuntimeError(
            f"PubMed request failed with HTTP {exc.code}: {exc.reason}. "
            "This is often temporary on the NCBI side; wait a minute and run the command again."
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

    columns = [
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
    rows_by_pmid: dict[str, dict[str, Any]] = {}
    for block in chunked(unique, 800):
        qmarks = ",".join(["?"] * len(block))
        rows = conn.execute(
            f"SELECT {', '.join(columns)} FROM pubmed_articles WHERE pmid IN ({qmarks})",
            block,
        ).fetchall()
        for row in rows:
            article = dict(zip(columns, row))
            rows_by_pmid[str(article["pmid"])] = article

    return [rows_by_pmid[pmid] for pmid in unique if pmid in rows_by_pmid]


def build_paper_text(title: str | None, abstract: str | None) -> str:
    return f"Title: {title or ''}\nAbstract: {abstract or ''}"


def _articles_with_embedding_text(articles: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str]]:
    candidates: list[tuple[dict[str, Any], str]] = []
    for article in articles:
        abstract = str(article.get("abstract") or "").strip()
        if not abstract:
            continue
        text = build_paper_text(
            title=str(article.get("title") or "").strip(),
            abstract=abstract,
        )
        candidates.append((article, text))
    return candidates


def _load_sentence_transformer(model_name: str):
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "Missing embedding dependency. Install dependencies with "
            "`pip install -r requirements.txt`, then try again."
        ) from exc

    try:
        return SentenceTransformer(model_name)
    except Exception as exc:
        raise RuntimeError(
            f"Could not load embedding model `{model_name}`. Install dependencies and "
            "ensure internet access for the first model download."
        ) from exc


def rank_articles_by_similarity(
    articles: list[dict[str, Any]],
    user_interest: str,
    model_name: str,
    top_k: int,
) -> list[dict[str, Any]]:
    user_interest = user_interest.strip()
    if not user_interest:
        raise ValueError("Interest cannot be empty.")
    if top_k <= 0:
        raise ValueError("--top-k must be greater than 0.")

    candidates = _articles_with_embedding_text(articles)
    print(f"[info] Available for embedding: {len(candidates)}")
    if not candidates:
        return []

    print(f"[info] Embedding model: {model_name}")
    model = _load_sentence_transformer(model_name)
    texts = [user_interest] + [text for _, text in candidates]

    try:
        embeddings = model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=True,
        )
    except Exception as exc:
        raise RuntimeError(
            "Embedding failed. Install dependencies with `pip install -r requirements.txt` "
            "and ensure internet access for the first model download."
        ) from exc

    embeddings = np.asarray(embeddings, dtype=np.float32)
    user_embedding = embeddings[0]
    paper_embeddings = embeddings[1:]
    scores = paper_embeddings @ user_embedding
    order = np.argsort(-scores)[:top_k]

    results: list[dict[str, Any]] = []
    for idx in order:
        article = dict(candidates[int(idx)][0])
        article["score"] = float(scores[int(idx)])
        results.append(article)
    return results


def _parse_authors(raw_authors: Any) -> list[str]:
    if isinstance(raw_authors, list):
        return [str(a) for a in raw_authors if str(a).strip()]
    if not raw_authors:
        return []
    if isinstance(raw_authors, str):
        try:
            parsed = json.loads(raw_authors)
        except json.JSONDecodeError:
            return [raw_authors] if raw_authors.strip() else []
        if isinstance(parsed, list):
            return [str(a) for a in parsed if str(a).strip()]
    return []


def _format_authors(raw_authors: Any) -> str:
    authors = _parse_authors(raw_authors)
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


def _print_recommendation_items(results: list[dict[str, Any]]) -> None:
    if not results:
        print("[done] No recommendations to show.")
        return

    for rank, article in enumerate(results, start=1):
        pmid = str(article.get("pmid") or "").strip()
        doi = str(article.get("doi") or "").strip()
        print(f"#{rank} | score={float(article.get('score', 0.0)):.4f}")
        print(f"Title: {article.get('title') or 'N/A'}")
        print(f"PMID: {pmid or 'N/A'}")
        print(f"Journal: {article.get('journal') or 'N/A'}")
        print(f"Date: {article.get('pub_date') or 'N/A'}")
        if doi:
            print(f"DOI: {doi}")
        print(f"Authors: {_format_authors(article.get('authors'))}")
        if pmid:
            print(f"URL: https://pubmed.ncbi.nlm.nih.gov/{pmid}/")
        print(f"Abstract: {_abstract_preview(article.get('abstract'))}")
        print()


def print_recommendations(results: list[dict[str, Any]]) -> None:
    print("\nTop recommendations\n")
    _print_recommendation_items(results)


def print_recommendation_section(results: list[dict[str, Any]], section_title: str, model_name: str) -> None:
    print("=" * 60)
    print(section_title)
    print(f"Model: {model_name}")
    print("=" * 60)
    print()
    _print_recommendation_items(results)


def prompt_topic() -> str:
    print("\nStep 1/3: Select your topic")
    print("This broad topic is used only for PubMed retrieval from the past 24 hours.")
    print("Choose a popular topic below, or type your own PubMed query.")
    print()
    for idx, (query, label) in enumerate(TOPIC_CHOICES, start=1):
        print(f"{idx}. {label}  [{query}]")
    print(f"{len(TOPIC_CHOICES) + 1}. Custom PubMed query")
    print()

    while True:
        max_choice = len(TOPIC_CHOICES) + 1
        choice = input(f"Choose 1-{max_choice}, or type a custom topic/query: ").strip()
        if not choice:
            print("Please choose a number or type a topic.")
            continue
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(TOPIC_CHOICES):
                return TOPIC_CHOICES[idx - 1][0]
            if idx == len(TOPIC_CHOICES) + 1:
                custom = input("Enter your broad PubMed query: ").strip()
                if custom:
                    return custom
                print("Custom query cannot be empty.")
                continue
            print("Please choose a valid number.")
            continue
        return choice


def prompt_interest() -> str:
    print("\nStep 2/3: Describe your specific interest")
    print("Now be as detailed as possible. This text is embedded and compared with")
    print("each paper's title + abstract, so useful details can include:")
    print("- disease/subfield")
    print("- method or technology")
    print("- population, model system, or data type")
    print("- outcome, mechanism, or application")
    print("- what you want to prioritize or avoid")
    print()
    print("Examples:")
    for example in INTEREST_EXAMPLES:
        print(f"- {example}")
    print()
    print("Write one or more lines. Press Enter on an empty line to finish.")

    lines: list[str] = []
    while True:
        prompt = "> " if not lines else "... "
        line = input(prompt)
        if not line.strip():
            if lines:
                break
            print("Please write at least one sentence.")
            continue
        lines.append(line.strip())

    return " ".join(lines).strip()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch past-24-hour PubMed candidates for a broad topic and rank "
            "them by semantic similarity to a specific interest."
        )
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"SQLite database path (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument("--topic", default="", help="Broad PubMed topic query")
    parser.add_argument("--interest", default="", help="Specific semantic interest text")
    parser.add_argument("--top-k", type=int, default=10, help="Number of recommendations to print")
    parser.add_argument("--fetch-batch", type=int, default=200, help="EFetch batch size")
    parser.add_argument("--fetch-retries", type=int, default=3, help="Retries for failed EFetch requests")
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME, help="sentence-transformers model name")
    parser.add_argument(
        "--no-has-abstract-filter",
        action="store_true",
        help="Do not add hasabstract[text] to the PubMed query",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Replace existing rows for fetched PMIDs before inserting this run's records",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    try:
        topic = args.topic.strip() or prompt_topic()
        query = build_pubmed_query(topic, require_abstract=not args.no_has_abstract_filter)

        load_dotenv()
        cfg = get_ncbi_config()
        configure_entrez(cfg, max_tries=None, sleep_between_tries=None)

        print("\nFetching PubMed candidates from the past 24 hours...")
        pmids = fetch_topic_candidates(
            db_path=args.db,
            query=query,
            fetch_batch=args.fetch_batch,
            fetch_retries=args.fetch_retries,
            refresh=args.refresh,
        )
        if not pmids:
            return 0

        interest = args.interest.strip() or prompt_interest()
        conn = init_db(args.db)
        try:
            articles = load_articles_by_pmids(conn, pmids)
        finally:
            conn.close()

        print(f"[info] Loaded from this run: {len(articles)}")
        print("\nStep 3/3: Embedding candidates and ranking by semantic similarity...")
        specter_results = rank_articles_by_similarity(
            articles=articles,
            user_interest=interest,
            model_name=args.model_name,
            top_k=args.top_k,
        )
        print_recommendation_section(
            specter_results,
            "Top recommendations using SPECTER",
            args.model_name,
        )
        return 0
    except KeyboardInterrupt:
        print("\n[error] Interrupted.", file=sys.stderr)
        return 130
    except (RuntimeError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
