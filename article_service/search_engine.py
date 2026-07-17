from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Literal

import numpy as np

from All_embedding import (
    DEFAULT_MODEL_NAME,
    _load_sentence_transformer,
    load_index_artifacts,
    metadata_by_paper_key,
    reciprocal_rank_fusion,
    search_keyword_pool,
    search_semantic_pool,
    select_hybrid_top_k,
)


SearchMode = Literal["none", "semantic", "keyword", "hybrid"]
TagMatchMode = Literal["and", "or"]


class ArtifactNotReadyError(RuntimeError):
    pass


def _clean_source(source: str | None) -> str:
    value = str(source or "all").strip().lower()
    return value or "all"


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [part.strip() for part in text.split(",") if part.strip()]


def _authors_to_string(value: Any) -> str:
    return ", ".join(_as_list(value)) if isinstance(value, list) else str(value or "")


def _clean_tags(tags: list[str] | str | None) -> list[str]:
    return [tag.lower() for tag in _as_list(tags)]


def normalize_article(item: dict[str, Any]) -> dict[str, Any]:
    source = str(item.get("source") or "").strip()
    external_id = str(item.get("external_id") or "").strip()
    paper_key = str(item.get("paper_key") or "").strip() or (
        f"{source}:{external_id}" if source and external_id else external_id
    )
    tags = []
    for value in (
        item.get("categories"),
        item.get("category"),
        item.get("primary_category"),
        item.get("classification"),
    ):
        tags.extend(_as_list(value))

    deduped_tags = list(dict.fromkeys(tags))
    published_date = item.get("published_date") or item.get("pub_date") or ""

    normalized = {
        "id": paper_key,
        "paper_key": paper_key,
        "source": source,
        "external_id": external_id,
        "title": item.get("title") or "",
        "authors": _authors_to_string(item.get("authors")),
        "url": item.get("url") or item.get("pdf_url") or "",
        "pdf_url": item.get("pdf_url") or "",
        "published_date": published_date,
        "abstract": item.get("abstract") or "",
        "tags": deduped_tags,
        "doi": item.get("doi") or "",
        "journal": item.get("journal") or item.get("venue") or "",
    }

    for key in (
        "score",
        "semantic_score",
        "semantic_rank",
        "keyword_rank",
        "bm25_score",
        "hybrid_rank",
        "rrf_score",
        "match_pool",
        "exact_title_match",
        "title_term_match",
        "abstract_term_match",
    ):
        if key in item:
            normalized[key] = item[key]
    return normalized


class ArticleSearchEngine:
    def __init__(self, artifact_dir: str | Path | None = None) -> None:
        self.artifact_dir = Path(
            artifact_dir
            or os.getenv("SCICOMM_ARTIFACT_DIR")
            or Path(__file__).resolve().parents[1]
        ).resolve()
        self.sqlite_path = self.artifact_dir / "all.sqlite"
        self.index_path = self.artifact_dir / "all_specter.index"
        self.metadata_path = self.artifact_dir / "all_metadata.json"
        self.manifest_path = self.artifact_dir / "all_manifest.json"

        self._lock = threading.RLock()
        self._artifact_signature: tuple[tuple[str, int, int], ...] | None = None
        self._index: Any | None = None
        self._metadata: list[dict[str, Any]] | None = None
        self._manifest: dict[str, Any] | None = None
        self._model_name: str | None = None
        self._model: Any | None = None

    def health(self) -> dict[str, Any]:
        artifacts = {
            "sqlite": self.sqlite_path,
            "index": self.index_path,
            "metadata": self.metadata_path,
            "manifest": self.manifest_path,
        }
        return {
            "status": "ok" if all(path.exists() for path in artifacts.values()) else "not_ready",
            "artifact_dir": str(self.artifact_dir),
            "artifacts": {
                name: {
                    "path": str(path),
                    "exists": path.exists(),
                    "size": path.stat().st_size if path.exists() else 0,
                }
                for name, path in artifacts.items()
            },
            "loaded": self._metadata is not None and self._index is not None,
        }

    def _signature(self) -> tuple[tuple[str, int, int], ...]:
        paths = (self.sqlite_path, self.index_path, self.metadata_path, self.manifest_path)
        missing = [str(path) for path in paths if not path.exists()]
        if missing:
            raise ArtifactNotReadyError("Missing search artifact(s): " + ", ".join(missing))
        return tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in paths)

    def _ensure_artifacts_loaded(self) -> tuple[Any, list[dict[str, Any]], dict[str, Any]]:
        signature = self._signature()
        with self._lock:
            if signature != self._artifact_signature:
                index, metadata, manifest = load_index_artifacts(
                    str(self.index_path),
                    str(self.metadata_path),
                    str(self.manifest_path),
                )
                metadata_by_paper_key(metadata)
                self._index = index
                self._metadata = metadata
                self._manifest = manifest
                self._artifact_signature = signature
            assert self._index is not None
            assert self._metadata is not None
            assert self._manifest is not None
            return self._index, self._metadata, self._manifest

    def _model_for_manifest(self, manifest: dict[str, Any]) -> Any:
        model_name = str(manifest.get("model_name") or DEFAULT_MODEL_NAME)
        with self._lock:
            if self._model is None or self._model_name != model_name:
                self._model = _load_sentence_transformer(model_name)
                self._model_name = model_name
            return self._model

    def _encode_query(self, query: str, manifest: dict[str, Any]) -> np.ndarray:
        query = query.strip()
        if not query:
            raise ValueError("Semantic query cannot be empty.")
        model = self._model_for_manifest(manifest)
        embedding = model.encode(
            [query],
            batch_size=1,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(embedding[0], dtype=np.float32)

    def sources(self) -> list[str]:
        self._require_sqlite()
        with sqlite3.connect(f"file:{self.sqlite_path}?mode=ro", uri=True) as conn:
            rows = conn.execute(
                "SELECT source FROM papers GROUP BY source ORDER BY source ASC"
            ).fetchall()
        return [str(row[0]) for row in rows]

    def articles(
        self,
        *,
        source: str = "all",
        limit: int = 50,
        offset: int = 0,
        date: str | None = None,
        tags: list[str] | None = None,
        tag_match: TagMatchMode = "or",
    ) -> list[dict[str, Any]]:
        self._require_sqlite()
        selected_source = _clean_source(source)
        where = []
        params: list[Any] = []
        if selected_source != "all":
            where.append("source = ?")
            params.append(selected_source)
        if date:
            where.append("published_date = ?")
            params.append(date)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        selected_tags = _clean_tags(tags)
        if not selected_tags:
            params.extend([limit, offset])

        with sqlite3.connect(f"file:{self.sqlite_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            if selected_tags:
                rows = conn.execute(
                    f"""
                    SELECT
                        paper_key,
                        source,
                        external_id,
                        title,
                        abstract,
                        authors,
                        published_date,
                        doi,
                        journal,
                        categories,
                        url,
                        pdf_url,
                        category,
                        primary_category,
                        classification
                    FROM papers
                    {where_sql}
                    ORDER BY COALESCE(published_date, '') DESC,
                             COALESCE(fetched_at, '') DESC,
                             paper_key ASC
                    """,
                    params,
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT
                        paper_key,
                        source,
                        external_id,
                        title,
                        abstract,
                        authors,
                        published_date,
                        doi,
                        journal,
                        categories,
                        url,
                        pdf_url,
                        category,
                        primary_category,
                        classification
                    FROM papers
                    {where_sql}
                    ORDER BY COALESCE(published_date, '') DESC,
                             COALESCE(fetched_at, '') DESC,
                             paper_key ASC
                    LIMIT ? OFFSET ?
                    """,
                    params,
                ).fetchall()

        articles = [normalize_article(dict(row)) for row in rows]
        if selected_tags:
            articles = self._filter_by_tags(articles, selected_tags, tag_match)
            articles = articles[offset : offset + limit]
        return articles

    def article_page(
        self,
        *,
        source: str = "all",
        limit: int = 50,
        offset: int = 0,
        date: str | None = None,
        tags: list[str] | None = None,
        tag_match: TagMatchMode = "or",
    ) -> dict[str, Any]:
        return {
            "items": self.articles(
                source=source,
                limit=limit,
                offset=offset,
                date=date,
                tags=tags,
                tag_match=tag_match,
            ),
            "total": self.count_articles(
                source=source,
                date=date,
                tags=tags,
                tag_match=tag_match,
            ),
        }

    def articles_by_keys(
        self,
        *,
        paper_keys: list[str],
        source: str = "all",
        limit: int = 50,
        offset: int = 0,
        tags: list[str] | None = None,
        tag_match: TagMatchMode = "or",
    ) -> list[dict[str, Any]]:
        articles = self._articles_for_keys(paper_keys)
        selected_source = _clean_source(source)
        selected_tags = _clean_tags(tags)
        filtered = self._filter_by_source(articles, selected_source)
        filtered = self._filter_by_tags(filtered, selected_tags, tag_match)
        return [normalize_article(item) for item in filtered[offset : offset + limit]]

    def article_page_by_keys(
        self,
        *,
        paper_keys: list[str],
        source: str = "all",
        limit: int = 50,
        offset: int = 0,
        tags: list[str] | None = None,
        tag_match: TagMatchMode = "or",
    ) -> dict[str, Any]:
        articles = self._articles_for_keys(paper_keys)
        selected_source = _clean_source(source)
        selected_tags = _clean_tags(tags)
        filtered = self._filter_by_source(articles, selected_source)
        filtered = self._filter_by_tags(filtered, selected_tags, tag_match)
        return {
            "items": [normalize_article(item) for item in filtered[offset : offset + limit]],
            "total": len(filtered),
        }

    def count_articles(
        self,
        *,
        source: str = "all",
        date: str | None = None,
        tags: list[str] | None = None,
        tag_match: TagMatchMode = "or",
    ) -> int:
        self._require_sqlite()
        selected_source = _clean_source(source)
        selected_tags = _clean_tags(tags)
        where = []
        params: list[Any] = []
        if selected_source != "all":
            where.append("source = ?")
            params.append(selected_source)
        if date:
            where.append("published_date = ?")
            params.append(date)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        with sqlite3.connect(f"file:{self.sqlite_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            if not selected_tags:
                row = conn.execute(f"SELECT COUNT(*) FROM papers {where_sql}", params).fetchone()
                return int(row[0])

            rows = conn.execute(
                f"""
                SELECT categories, category, primary_category, classification
                FROM papers
                {where_sql}
                """,
                params,
            ).fetchall()

        count = 0
        for row in rows:
            row_tags = []
            for value in (
                row["categories"],
                row["category"],
                row["primary_category"],
                row["classification"],
            ):
                row_tags.extend(_as_list(value))
            if self._filter_by_tags([{"tags": row_tags}], selected_tags, tag_match):
                count += 1
        return count

    def search(
        self,
        *,
        mode: SearchMode,
        semantic_query: str = "",
        keyword_query: str = "",
        source: str = "all",
        limit: int = 20,
        offset: int = 0,
        pool_size: int = 100,
        min_score: float | None = None,
        rrf_k: int = 60,
        keyword_reserved: int = 3,
        tags: list[str] | None = None,
        tag_match: TagMatchMode = "or",
        scope_semantic_query: str = "",
        scope_limit: int = 1000,
        scope_paper_keys: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self.search_page(
            mode=mode,
            semantic_query=semantic_query,
            keyword_query=keyword_query,
            source=source,
            limit=limit,
            offset=offset,
            pool_size=pool_size,
            min_score=min_score,
            rrf_k=rrf_k,
            keyword_reserved=keyword_reserved,
            tags=tags,
            tag_match=tag_match,
            scope_semantic_query=scope_semantic_query,
            scope_limit=scope_limit,
            scope_paper_keys=scope_paper_keys,
            exact_total=False,
        )["items"]

    def search_page(
        self,
        *,
        mode: SearchMode,
        semantic_query: str = "",
        keyword_query: str = "",
        source: str = "all",
        limit: int = 20,
        offset: int = 0,
        pool_size: int = 100,
        min_score: float | None = None,
        rrf_k: int = 60,
        keyword_reserved: int = 3,
        tags: list[str] | None = None,
        tag_match: TagMatchMode = "or",
        scope_semantic_query: str = "",
        scope_limit: int = 1000,
        scope_paper_keys: list[str] | None = None,
        exact_total: bool = True,
    ) -> dict[str, Any]:
        selected_tags = _clean_tags(tags)
        if mode == "none":
            return self.article_page(source=source, limit=limit, offset=offset, tags=selected_tags, tag_match=tag_match)
        if limit <= 0:
            raise ValueError("limit must be greater than 0.")
        if offset < 0:
            raise ValueError("offset cannot be negative.")
        if pool_size <= 0:
            raise ValueError("pool_size must be greater than 0.")

        index, metadata, manifest = self._ensure_artifacts_loaded()
        selected_source = _clean_source(source)
        final_count = offset + limit
        candidate_count = len(metadata) if exact_total else min(
            max(pool_size, final_count, scope_limit if scope_semantic_query.strip() else 0),
            len(metadata),
        )
        scope_keys = self._combined_scope_paper_keys(
            scope_paper_keys,
            scope_semantic_query,
            index,
            metadata,
            manifest,
            scope_limit,
            min_score,
        )

        if mode == "semantic":
            query = semantic_query.strip() or keyword_query.strip()
            query_embedding = self._encode_query(query, manifest)
            results = search_semantic_pool(index, metadata, query_embedding, candidate_count, min_score)
            filtered = self._filter_results(results, selected_source, selected_tags, tag_match, scope_keys)
            return {
                "items": [normalize_article(item) for item in filtered[offset : offset + limit]],
                "total": len(filtered),
            }

        if mode == "keyword":
            query = keyword_query.strip() or semantic_query.strip()
            if not query:
                raise ValueError("Keyword query cannot be empty.")
            results = search_keyword_pool(str(self.sqlite_path), metadata, query, candidate_count)
            filtered = self._filter_results(results, selected_source, selected_tags, tag_match, scope_keys)
            return {
                "items": [normalize_article(item) for item in filtered[offset : offset + limit]],
                "total": len(filtered),
            }

        if mode == "hybrid":
            semantic_text = semantic_query.strip() or keyword_query.strip()
            keyword_text = keyword_query.strip() or semantic_query.strip()
            if not semantic_text or not keyword_text:
                raise ValueError("Hybrid search requires a semantic or keyword query.")

            keyword_results = search_keyword_pool(
                str(self.sqlite_path),
                metadata,
                keyword_text,
                candidate_count,
            )
            query_embedding = self._encode_query(semantic_text, manifest)
            semantic_results = search_semantic_pool(
                index,
                metadata,
                query_embedding,
                candidate_count,
                min_score,
            )
            fused = reciprocal_rank_fusion(keyword_results, semantic_results, rrf_k)
            filtered = self._filter_results(fused, selected_source, selected_tags, tag_match, scope_keys)
            selection_limit = min(final_count, len(filtered))
            if selection_limit <= 0:
                return {"items": [], "total": len(filtered)}
            selected, _reserved_count = select_hybrid_top_k(
                filtered,
                selection_limit,
                min(keyword_reserved, selection_limit),
            )
            return {
                "items": [normalize_article(item) for item in selected[offset : offset + limit]],
                "total": len(filtered),
            }

        raise ValueError(f"Unsupported search mode: {mode}")

    def _require_sqlite(self) -> None:
        if not self.sqlite_path.exists():
            raise ArtifactNotReadyError(f"Missing SQLite artifact: {self.sqlite_path}")

    def _articles_for_keys(self, paper_keys: list[str]) -> list[dict[str, Any]]:
        self._require_sqlite()
        ordered_keys = list(dict.fromkeys(key.strip() for key in paper_keys if key.strip()))
        if not ordered_keys:
            return []

        placeholders = ", ".join(["?"] * len(ordered_keys))
        with sqlite3.connect(f"file:{self.sqlite_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT
                    paper_key,
                    source,
                    external_id,
                    title,
                    abstract,
                    authors,
                    published_date,
                    doi,
                    journal,
                    categories,
                    url,
                    pdf_url,
                    category,
                    primary_category,
                    classification
                FROM papers
                WHERE paper_key IN ({placeholders})
                """,
                ordered_keys,
            ).fetchall()

        by_key = {str(row["paper_key"]): dict(row) for row in rows}
        return [by_key[key] for key in ordered_keys if key in by_key]

    @staticmethod
    def _filter_by_source(results: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
        if source == "all":
            return results
        return [
            item
            for item in results
            if str(item.get("source") or "").strip().lower() == source
        ]

    @staticmethod
    def _filter_by_scope(results: list[dict[str, Any]], scope_keys: set[str] | None) -> list[dict[str, Any]]:
        if not scope_keys:
            return results
        return [
            item
            for item in results
            if str(item.get("paper_key") or item.get("id") or "").strip() in scope_keys
        ]

    @staticmethod
    def _filter_by_tags(
        results: list[dict[str, Any]],
        tags: list[str],
        tag_match: TagMatchMode,
    ) -> list[dict[str, Any]]:
        if not tags:
            return results
        required = set(tags)
        matches_all = tag_match == "and"
        filtered = []
        for item in results:
            article_tags = set(_clean_tags(item.get("tags") or item.get("categories")))
            if not article_tags:
                continue
            if required.issubset(article_tags) if matches_all else bool(required & article_tags):
                filtered.append(item)
        return filtered

    def _combined_scope_paper_keys(
        self,
        scope_paper_keys: list[str] | None,
        scope_semantic_query: str,
        index: Any,
        metadata: list[dict[str, Any]],
        manifest: dict[str, Any],
        scope_limit: int,
        min_score: float | None,
    ) -> set[str] | None:
        explicit_scope = {
            key.strip()
            for key in (scope_paper_keys or [])
            if key.strip()
        }
        query = scope_semantic_query.strip()
        if not query:
            return explicit_scope or None
        query_embedding = self._encode_query(query, manifest)
        scoped = search_semantic_pool(
            index,
            metadata,
            query_embedding,
            min(max(scope_limit, 1), len(metadata)),
            min_score,
        )
        semantic_scope = {
            str(item.get("paper_key") or item.get("id") or "").strip()
            for item in scoped
            if str(item.get("paper_key") or item.get("id") or "").strip()
        }
        if explicit_scope:
            return explicit_scope & semantic_scope
        return semantic_scope

    def _filter_and_slice(
        self,
        results: list[dict[str, Any]],
        source: str,
        offset: int,
        limit: int,
        tags: list[str] | None = None,
        tag_match: TagMatchMode = "or",
        scope_keys: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        filtered = self._filter_results(results, source, tags or [], tag_match, scope_keys)
        return [normalize_article(item) for item in filtered[offset : offset + limit]]

    def _filter_results(
        self,
        results: list[dict[str, Any]],
        source: str,
        tags: list[str],
        tag_match: TagMatchMode,
        scope_keys: set[str] | None,
    ) -> list[dict[str, Any]]:
        filtered = self._filter_by_source(results, source)
        filtered = self._filter_by_scope(filtered, scope_keys)
        return self._filter_by_tags(filtered, tags, tag_match)
