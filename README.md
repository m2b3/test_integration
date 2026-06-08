# infogather

Small literature-fetching and semantic-search pipeline for PubMed, arXiv,
bioRxiv, and medRxiv.

The fetch scripts store metadata in SQLite with source-specific deduplication.
`All_embedding.py` can then build a persistent SPECTER + FAISS index from one
of those SQLite databases and search it with a natural-language interest query.

## Entrypoints

- `base.py`: fetch PubMed records from the last 24 hours into `pubmed_articles`
- `arxiv.py`: fetch arXiv records into `arxiv_articles`
- `biorxiv.py`: fetch bioRxiv or medRxiv records into `biorxiv_articles`
- `medrxiv.py`: fetch medRxiv records into `medrxiv_articles`
- `All_embedding.py`: build/search a FAISS index from supported SQLite tables

Supported existing-db tables for embedding:

- `pubmed_articles`
- `arxiv_articles`
- `biorxiv_articles`
- `medrxiv_articles`
- `rss_articles`
- unified `papers`

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

The first embedding run may download the SPECTER model from Hugging Face.

## Fetch Literature

PubMed:

```bash
python base.py --db pubmed.sqlite
python base.py --db pubmed.sqlite --query "cerebellum AND eye tracking"
```

arXiv:

```bash
python arxiv.py --db arxiv.sqlite
python arxiv.py --db arxiv.sqlite --max 500
```

bioRxiv:

```bash
python biorxiv.py --db biorxiv.sqlite --server biorxiv
```

medRxiv:

```bash
python medrxiv.py --db medrxiv.sqlite
python medrxiv.py --db medrxiv.sqlite --max 20 --sleep 1
python medrxiv.py --db medrxiv.sqlite --days 1
python medrxiv.py --db medrxiv.sqlite --start-date 2026-06-07 --end-date 2026-06-08
```

`medrxiv.py` uses the official medRxiv details API, requests yesterday/today in
UTC by default, paginates with cursor offsets, deduplicates by DOI, and stores
rows in `medrxiv_articles`.

## Build Embeddings

Build from a fetched SQLite database:

```bash
python All_embedding.py --build-from-existing-db pubmed.sqlite
python All_embedding.py --build-from-existing-db arxiv.sqlite
python All_embedding.py --build-from-existing-db biorxiv.sqlite
python All_embedding.py --build-from-existing-db medrxiv.sqlite
```

Build PubMed directly, fetching first and then indexing:

```bash
python All_embedding.py --build-index --db pubmed.sqlite
```

Custom artifact paths:

```bash
python All_embedding.py \
  --build-from-existing-db medrxiv.sqlite \
  --index-path indexes/medrxiv_specter.faiss \
  --metadata-path indexes/medrxiv_metadata.json \
  --manifest-path indexes/medrxiv_manifest.json
```

Search an existing index:

```bash
python All_embedding.py \
  --interest "single-cell genomics for early cancer biomarker discovery" \
  --limit 10
```

Build and search in one run:

```bash
python All_embedding.py \
  --build-from-existing-db medrxiv.sqlite \
  --interest "single-cell genomics for early cancer biomarker discovery" \
  --limit 10
```

The default embedding model is
`sentence-transformers/allenai-specter`. Embeddings are normalized and stored in
a FAISS `IndexFlatIP`.

## Docker

Build the image:

```bash
docker build -t infogather .
```

The image entrypoint is `All_embedding.py`.

Build an index from `medrxiv.sqlite`:

```bash
docker run --rm -v "$PWD:/work" -w /work infogather \
  --build-from-existing-db /work/medrxiv.sqlite \
  --index-path /work/medrxiv_specter.index \
  --metadata-path /work/medrxiv_metadata.json \
  --manifest-path /work/medrxiv_manifest.json
```

Search that index:

```bash
docker run --rm -v "$PWD:/work" -w /work infogather \
  --interest "machine learning for clinical risk prediction" \
  --limit 10 \
  --index-path /work/medrxiv_specter.index \
  --metadata-path /work/medrxiv_metadata.json \
  --manifest-path /work/medrxiv_manifest.json
```

Run a fetch script inside the same image:

```bash
docker run --rm -v "$PWD:/work" -w /work --entrypoint python infogather \
  /app/medrxiv.py --db /work/medrxiv.sqlite --max 20 --sleep 1
```

## Apptainer

Build the image:

```bash
apptainer build infogather.sif Apptainer.def
```

The runscript is `All_embedding.py`.

Build an index:

```bash
apptainer run infogather.sif \
  --build-from-existing-db medrxiv.sqlite \
  --index-path medrxiv_specter.index \
  --metadata-path medrxiv_metadata.json \
  --manifest-path medrxiv_manifest.json
```

Search an index:

```bash
apptainer run infogather.sif \
  --interest "machine learning for clinical risk prediction" \
  --limit 10 \
  --index-path medrxiv_specter.index \
  --metadata-path medrxiv_metadata.json \
  --manifest-path medrxiv_manifest.json
```

Run a fetch script:

```bash
apptainer exec infogather.sif python /opt/infogather/medrxiv.py \
  --db ./medrxiv.sqlite --max 20 --sleep 1
```

## Requirements

Python dependencies are declared in `requirements.txt`:

- `biopython`
- `faiss-cpu`
- `numpy`
- `sentence-transformers`

System/runtime assumptions:

- Python 3.12 or a compatible recent Python 3
- network access to NCBI, arXiv, bioRxiv, medRxiv, or Hugging Face when needed
- a writable path for SQLite, FAISS, metadata, and manifest artifacts

Optional PubMed environment variables:

- `NCBI_EMAIL`
- `NCBI_TOOL`
- `NCBI_API_KEY`
- `EDIRECT_PREFIX`

## Testing

Run the smoke tests with:

```bash
python -m unittest -v tests/test_base_smoke.py
```

Quick medRxiv smoke test:

```bash
python medrxiv.py --db medrxiv.sqlite --max 20 --sleep 1
python All_embedding.py --build-from-existing-db medrxiv.sqlite
```

## Notes

- SQLite databases and FAISS artifacts are workflow outputs, not required source
  files.
- Fetch scripts use `INSERT OR IGNORE` style deduplication.
- `medrxiv.py` does not implement arbitrary API keyword search because the
  official medRxiv details endpoint is interval/cursor based.
- EDirect is optional for PubMed; the default PubMed path uses Biopython.
