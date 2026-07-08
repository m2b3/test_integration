from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware

from article_service.search_engine import ArtifactNotReadyError, ArticleSearchEngine, SearchMode


ResolvedMode = Literal["none", "semantic", "keyword", "hybrid"]


app = FastAPI(title="Scicommons Article Search API")

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

engine = ArticleSearchEngine()


def resolve_mode(
    search_mode: str,
    semantic_query: str,
    keyword_query: str,
) -> ResolvedMode:
    mode = search_mode.strip().lower()
    semantic = bool(semantic_query.strip())
    keyword = bool(keyword_query.strip())

    if mode in {"", "auto"}:
        if semantic and keyword:
            return "hybrid"
        if semantic:
            return "semantic"
        if keyword:
            return "keyword"
        return "none"

    if mode not in {"none", "semantic", "keyword", "hybrid"}:
        raise HTTPException(status_code=400, detail="search_mode must be one of none, semantic, keyword, hybrid, auto")
    return mode  # type: ignore[return-value]


def handle_error(exc: Exception) -> None:
    if isinstance(exc, ArtifactNotReadyError):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    raise exc


@app.get("/health")
def health() -> dict:
    return engine.health()


@app.get("/sources")
def sources() -> list[str]:
    try:
        return engine.sources()
    except Exception as exc:
        handle_error(exc)
        raise


@app.get("/manifest")
def manifest() -> dict:
    try:
        _index, _metadata, loaded_manifest = engine._ensure_artifacts_loaded()
        return loaded_manifest
    except Exception as exc:
        handle_error(exc)
        raise


@app.get("/articles")
def articles(
    source: str = "all",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    date: str | None = None,
    semantic_query: str = "",
    keyword_query: str = "",
    search_mode: str = "auto",
    pool_size: int = Query(default=100, ge=1, le=2000),
    min_score: float | None = None,
    rrf_k: int = Query(default=60, ge=1),
    keyword_reserved: int = Query(default=3, ge=0),
) -> list[dict]:
    mode = resolve_mode(search_mode, semantic_query, keyword_query)
    try:
        if mode == "none":
            return engine.articles(source=source, limit=limit, offset=offset, date=date)
        return engine.search(
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
        )
    except Exception as exc:
        handle_error(exc)
        raise


@app.get("/search")
def search(
    source: str = "all",
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    semantic_query: str = "",
    keyword_query: str = "",
    search_mode: str = "auto",
    pool_size: int = Query(default=100, ge=1, le=2000),
    min_score: float | None = None,
    rrf_k: int = Query(default=60, ge=1),
    keyword_reserved: int = Query(default=3, ge=0),
) -> list[dict]:
    mode: SearchMode = resolve_mode(search_mode, semantic_query, keyword_query)
    if mode == "none":
        raise HTTPException(status_code=400, detail="Search requires semantic_query or keyword_query")
    try:
        return engine.search(
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
        )
    except Exception as exc:
        handle_error(exc)
        raise

