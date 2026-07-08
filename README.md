# Scientific communication data pipeline

This repository collects recent scientific papers and science-related social posts into SQLite, then builds searchable scientific-paper indexes with SPECTER, FAISS, and SQLite FTS（Full text search).

The main paper workflow covers six daily sources:

- arXiv
- PubMed
- bioRxiv
- medRxiv
- PsyArXiv
- SocArXiv

Additional standalone tools ingest OpenReview venues, journal RSS/Atom feeds, Bluesky posts, and Mastodon.

## Quick start

Use a recent Python 3 release. The container definitions currently use Python 3.12.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

For PubMed, set an identifying email before fetching:

```bash
export NCBI_EMAIL="you@example.com"
export NCBI_TOOL="scicomm-embedding"
# export NCBI_API_KEY="..."  
```

The first embedding run downloads `sentence-transformers/allenai-specter` from Hugging Face.

## Daily paper pipeline

`pipeline.py` is the primary orchestration entrypoint:

```bash
python pipeline.py
```

It performs five stages:

1. Deletes the selected daily source databases and their SQLite sidecars.
2. Runs each source collector.
3. Validates SQLite integrity, schema, and row counts.
4. Merges successful sources and builds SPECTER/FAISS and FTS5 indexes.
5. Writes a text log and JSON report under `logs/`.

> **Important:** this is a fresh daily rebuild. Before fetching, it deletes the selected `arxiv.sqlite`, `pubmed.sqlite`, `biorxiv.sqlite`, `medrxiv.sqlite`, `psyarxiv.sqlite`, and `socarxiv.sqlite` files. Use `--dry-run` first if you need to inspect the operations.

Preview a run without deleting files or starting subprocesses:

```bash
python pipeline.py --dry-run
```

Run only selected sources:

```bash
python pipeline.py --sources arxiv pubmed medrxiv
```

Fetch and validate without rebuilding combined search artifacts:

```bash
python pipeline.py --skip-index
```

Stop at the first fetch or validation failure:

```bash
python pipeline.py --fail-fast
```

OpenReview/bluesky/mastodon is intentionally excluded from `pipeline.py`.

### Pipeline outputs

The combined paper build produces:

| File | Purpose |
| --- | --- |
| `all.sqlite` | Normalized `papers` table plus the `papers_fts` FTS5 index |
| `all_specter.index` | FAISS `IndexFlatIP` over normalized SPECTER vectors |
| `all_metadata.json` | Metadata aligned by position with the FAISS vectors |
| `all_manifest.json` | Inputs, source counts, model, dimensions, skipped rows, and timings |
| `logs/pipeline_<timestamp>.log` | Full console log |
| `logs/pipeline_report_<timestamp>.json` | Machine-readable source and build report |

Papers without enough usable title/abstract text remain in `all.sqlite` but are skipped by the FAISS and FTS5 indexes.

## Article/search API

The API wraps the daily artifacts produced by `pipeline.py` and keeps them
loaded for request-time use. Run the daily pipeline first so `all.sqlite`,
`all_specter.index`, `all_metadata.json`, and `all_manifest.json` exist.

Install dependencies, then start the service from this repo root:

```bash
python -m pip install -r requirements.txt
uvicorn article_service.main:app --host 0.0.0.0 --port 8100
```

If artifacts live outside the repo root, point the service at that directory:

```bash
SCICOMM_ARTIFACT_DIR=/path/to/artifacts \
uvicorn article_service.main:app --host 0.0.0.0 --port 8100
```

The service reloads artifacts automatically when the pipeline atomically
replaces them.

Useful endpoints:

```text
GET /health
GET /sources
GET /manifest
GET /articles?source=all&limit=50
GET /articles?source=arxiv&limit=50
GET /search?search_mode=semantic&semantic_query=mechanistic+interpretability&source=all
GET /search?search_mode=keyword&keyword_query=Monte+Carlo+tree+search&source=arxiv
GET /search?search_mode=hybrid&semantic_query=language+model+planning&keyword_query=MCTS&source=all
```

`source=all` searches all indexed papers. Any other source value, such as
`arxiv`, `pubmed`, `biorxiv`, `medrxiv`, `psyarxiv`, or `socarxiv`, filters
results to that source. The current implementation retrieves a candidate pool
from the combined artifacts and filters by source before returning results.
Increase `pool_size` when filtering to a source and asking for many results.

The response article shape is designed for the Scicommons web backend:

```json
{
  "id": "arxiv:2401.12345",
  "paper_key": "arxiv:2401.12345",
  "source": "arxiv",
  "external_id": "2401.12345",
  "title": "Example title",
  "authors": "A. Smith, B. Lee",
  "url": "https://arxiv.org/abs/2401.12345",
  "pdf_url": "https://arxiv.org/pdf/2401.12345",
  "published_date": "2026-07-08",
  "abstract": "...",
  "tags": ["cs.AI"],
  "score": 0.83
}
```

## Build and search indexes directly

`All_embedding.py` is the merge, indexing, and retrieval tool.

### Build one database

Pass a supported SQLite database as the positional argument:

```bash
python All_embedding.py arxiv.sqlite
```

Artifact names are derived from the database stem:

```text
arxiv_specter.index
arxiv_metadata.json
arxiv_manifest.json
```

Supported input schemas are produced by PubMed, arXiv, bioRxiv, medRxiv, RSS, PsyArXiv/SocArXiv, OpenReview, and previously merged `papers` databases.

An existing database can also be indexed with explicit artifact paths:

```bash
python All_embedding.py \
  --build-from-existing-db arxiv.sqlite \
  --index-path arxiv_specter.index \
  --metadata-path arxiv_metadata.json \
  --manifest-path arxiv_manifest.json
```

### Merge local databases

Run this in a directory containing the source `.sqlite` files:

```bash
python All_embedding.py all.sqlite
```

This discovers supported SQLite databases in the current directory, normalizes them into `all.sqlite`, deduplicates by `(source, external_id)`, builds `papers_fts`, and writes the combined FAISS/metadata/manifest artifacts.

Unlike `pipeline.py`, a direct `all.sqlite` merge includes any supported database it discovers, including an `openreview.sqlite` file. Temporary, backup, cache, test, and existing `all.sqlite` files are ignored.

### Search

Stage-two search currently requires exactly one artifact selector: `--all` or `--arxiv`.

Semantic search:

```bash
python All_embedding.py \
  --interest "mechanistic interpretability for language models" \
  --all \
  --limit 10
```

Keyword search through SQLite FTS5:

```bash
python All_embedding.py \
  --interest "Monte Carlo tree search" \
  --all \
  --search-mode keyword \
  --limit 10
```

Hybrid search combines semantic and keyword candidate pools with reciprocal rank fusion:

```bash
python All_embedding.py \
  --interest "Monte Carlo tree search" \
  --all \
  --search-mode hybrid \
  --pool-size 100 \
  --rrf-k 60 \
  --keyword-reserved 3 \
  --limit 10
```

Useful search options:

- `--min-score`: semantic-score cutoff.
- `--pool-size`: candidates retrieved from each hybrid pool.
- `--rrf-k`: reciprocal-rank-fusion smoothing constant.
- `--keyword-reserved`: exact-title keyword matches reserved in hybrid output; use `0` for pure RRF.
- `--index-path`, `--metadata-path`, and `--manifest-path`: override artifact locations.

Only `--all` and `--arxiv` are exposed as built-in search selectors. Semantic artifact paths can be overridden explicitly, but keyword search still reads `all.sqlite` or `arxiv.sqlite`; fully supporting another source as a search target requires a code change.

### Fetch and index PubMed in one command

`All_embedding.py` can also fetch the latest PubMed window and build an index:

```bash
python All_embedding.py --build-index
```

By default it uses `all[sb] AND hasabstract`, `datetype=edat`, and `reldate=1`.

```bash
python All_embedding.py --build-index --no-require-abstract
python All_embedding.py --build-index --refresh
```

## Standalone paper collectors

All collectors deduplicate on a stable source identifier and preserve normalized fields plus raw source data.

### PubMed

`base.py` fetches records added or updated in the previous Entrez day (`reldate=1`, `datetype=edat`) and writes `pubmed_articles`.

```bash
python base.py --db pubmed.sqlite
python base.py --db pubmed.sqlite --query "cerebellum AND eye tracking"
python base.py --db pubmed.sqlite --max 1000 --fetch-batch 100
python base.py --db pubmed.sqlite --start-from 1000
```

Biopython Entrez is the default retrieval path. NCBI EDirect is optional:

```bash
python base.py --db pubmed.sqlite --edirect auto
python base.py --db pubmed.sqlite --edirect on --edirect-prefix wsl
```

An optional `.env` file in the working directory may define `NCBI_EMAIL`, `NCBI_TOOL`, `NCBI_API_KEY`, and `EDIRECT_PREFIX`.

### arXiv

With no query, `arxiv.py` uses OAI-PMH for bulk/incremental metadata and locally filters the day-level datestamps. Supplying `--query` switches to the arXiv Atom API.

```bash
python arxiv.py --db arxiv.sqlite
python arxiv.py --db arxiv.sqlite --oai-set "cs:cs:AI"
python arxiv.py --db arxiv.sqlite --query "cat:cs.CL" --max 500
```

Rows are stored in `arxiv_articles` and deduplicated by canonical arXiv ID.

### bioRxiv and medRxiv

```bash
python biorxiv.py --db biorxiv.sqlite --server biorxiv
python medrxiv.py --db medrxiv.sqlite
python medrxiv.py --db medrxiv.sqlite --days 7
python medrxiv.py --db medrxiv.sqlite \
  --start-date 2026-06-01 \
  --end-date 2026-06-07
```

`biorxiv.py` can query either server, while `medrxiv.py` provides a dedicated medRxiv schema and explicit date-range controls.

### PsyArXiv and SocArXiv

These wrappers share the OSF API and normalization logic in `osf_preprints.py`.

```bash
python psyarxiv.py --db psyarxiv.sqlite
python socarxiv.py --db socarxiv.sqlite
python psyarxiv.py --hours 72 --query "cognitive bias" --max-results 500
python socarxiv.py --dry-run
```

Both write a unified `papers` table keyed by `(source, external_id)`.

### OpenReview

OpenReview collection is venue-based rather than a global recent-paper feed:

```bash
python openreview.py "ICLR.cc/2026/Conference"
python openreview.py \
  "ICLR.cc/2026/Conference" \
  --invitation "ICLR.cc/2026/Conference/-/Blind_Submission"
```

The script discovers or accepts a submission invitation, fetches publicly visible notes, classifies their visible status, and writes `openreview.sqlite`. Private submissions and non-public rejected papers are not accessible without the relevant permissions.

## RSS and journal feed tools

See [`README_rss.md`](README_rss.md) for the `rss.py` schema and detailed behavior.

Ingest a known feed:

```bash
python rss.py \
  --source nature \
  --feed-url "https://www.nature.com/nature.rss"
```

The source slug determines the output name, such as `nature.sqlite`. By default, entries from the last 24 hours are retained; undated entries are kept with a warning.

Discover a journal through Crossref, homepage autodiscovery, and candidate feed validation:

```bash
python rss_discover_journal.py --journal "Nature"
python rss_discover_journal.py --journal "Nature" --mailto "you@example.com"
```

Feed-list utilities:

- `check_rss_feeds.py` validates `rss_feeds_raw.json` and writes checked, summary, and blocked-feed JSON files.
- `scrape_feedspot_science.py` discovers and checks feeds from Feedspot's public science list.
- `scrape_aps_feeds.py` parses a local `aps_feeds.html` snapshot and checks discovered APS feeds.
- `scrape_oup_rss.py` parses a local `oup_rss.html` snapshot and checks OUP feeds; `OUP_COOKIE` can be set when a browser-derived cookie is needed.

The feed-checking scripts currently define active feeds as those with a latest item dated in 2025 or 2026.

## Bluesky and Mastodon collectors

These tools use a deliberately unusual positional syntax:

```bash
python bluesky.py - scientist - "Nicole Rust" - days 30
python bluesky.py - journal - "Nature" - days 7 --journal-mode account

python mastodon.py - scientist - "Terence Tao" - days 10 \
  --account @tao@mathstodon.xyz
python mastodon.py - journal - "Nature" - days 7 --journal-mode auto
```

Both write `<safe_query>.sqlite` and `<safe_query>.json` in the current directory or `--out-dir`.

Bluesky uses the public API. Scientist mode selects an actor account; journal mode can use an official-looking account or explicit keyword search. Public post search may be authentication-limited.

Mastodon searches the instances listed in `mastodon_instances.json`. A direct `--account @user@instance` is the most reliable scientist lookup. Keyword coverage is instance-dependent and is not a complete search of the Fediverse.

## Data model

Source databases retain source-specific schemas. During a combined build, `All_embedding.py` maps them into one `papers` table with:

- stable identity: `paper_key`, `source`, `external_id`
- bibliographic metadata: title, abstract, authors, dates, DOI, journal, categories, URLs
- source-specific identifiers and fields for PubMed, arXiv, RSS, preprint servers, and OpenReview
- provenance: `source_db`, `fetched_at`, and raw JSON/content

The combined database also contains `papers_fts`, an FTS5 table over eligible paper text. FAISS uses normalized SPECTER vectors and inner-product search, which corresponds to cosine similarity for normalized vectors.

## Containers

The Docker and Apptainer definitions install the Python dependencies and use `All_embedding.py` as the default entrypoint.

```bash
docker build -t scicomm-embedding .
docker run --rm -v "$PWD:/work" -w /work scicomm-embedding all.sqlite
docker run --rm -v "$PWD:/work" -w /work scicomm-embedding \
  --interest "single-cell cancer biomarkers" --all --limit 10
```

```bash
apptainer build scicomm-embedding.sif Apptainer.def
apptainer run scicomm-embedding.sif all.sqlite
apptainer run scicomm-embedding.sif \
  --interest "single-cell cancer biomarkers" --all --limit 10
```

The current images copy `All_embedding.py`, `base.py`, `arxiv.py`, `biorxiv.py`, and `medrxiv.py`. They do not include `pipeline.py`, the OSF/OpenReview/RSS/social scripts, or local source databases unless those files are mounted at runtime.

## Repository map

| Path | Role |
| --- | --- |
| `pipeline.py` | Fresh daily fetch, validation, merge, index, and reporting workflow |
| `All_embedding.py` | Schema detection, normalization, merge, FTS5, SPECTER/FAISS, and search |
| `base.py` | PubMed ingestion |
| `arxiv.py` | arXiv OAI-PMH/Atom ingestion |
| `biorxiv.py` | bioRxiv or medRxiv details-API ingestion |
| `medrxiv.py` | Dedicated medRxiv ingestion with date controls |
| `osf_preprints.py` | Shared PsyArXiv/SocArXiv API and SQLite helpers |
| `psyarxiv.py`, `socarxiv.py` | OSF provider wrappers |
| `openreview.py` | Public OpenReview venue ingestion |
| `rss.py` | Known RSS/Atom feed ingestion |
| `rss_discover_journal.py` | Journal lookup, feed discovery, validation, and ingestion |
| `check_rss_feeds.py` | Bulk feed validation |
| `scrape_*.py` | Feed discovery/checking utilities |
| `bluesky.py`, `mastodon.py` | Scientist/journal social-post collection |
| `Dockerfile`, `Apptainer.def` | Container builds centered on `All_embedding.py` |

SQLite databases, FAISS indexes, metadata/manifest JSON, social exports, bytecode caches, and pipeline logs are generated workflow outputs. They can be large and are not required to understand or modify the source.

## Operational notes

- All network collectors depend on the availability and rate limits of upstream services.
- A first SPECTER run requires internet access and can take substantial time on CPU.
- SQLite collectors generally use WAL mode, so `-wal` and `-shm` files may appear while databases are open.
- There is currently no automated test suite in the repository. Use CLI `--help`, `python -m compileall`, small capped fetches, and `pipeline.py --dry-run` for local validation.
- No license file is currently included.
