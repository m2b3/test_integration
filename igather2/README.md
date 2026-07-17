# infogather

`infogather` fetches PubMed records added or updated in the last 24 hours and stores a normalized subset of each record in SQLite. It is designed as a small, portable local ingestion tool.

The project has one Python entrypoint:

- `base.py`: fetch PubMed records into SQLite

By default, retrieval uses Biopython's `Bio.Entrez` client and NCBI E-utilities. A local EDirect copy is bundled in `edirect/` for larger retrievals or fallback use, but it is disabled by default because the Biopython path is more stable for smaller workflows.

## Quick Start

Create an environment and install dependencies. This plain Python workflow works on Linux, macOS, and Windows/WSL as long as you have a recent Python 3 installed.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

On macOS, install Python first if needed:

```bash
brew install python
```

Then run the same `python3 -m venv ...` commands above.

Create a `.env` file or export these variables in your shell:

```bash
NCBI_EMAIL=you@example.com
NCBI_TOOL=infogather
NCBI_API_KEY=optional_ncbi_api_key
```

`NCBI_API_KEY` is optional, but recommended for higher NCBI E-utilities rate limits.

Fetch all PubMed records added or updated in the last 24 hours:

```bash
python3 base.py --db pubmed.sqlite
```

Run a smaller test ingest:

```bash
python3 base.py --db pubmed.sqlite --max 500
```

Fetch a topic-specific subset from the last 24 hours:

```bash
python3 base.py --db pubmed.sqlite --query "cerebellum AND eye tracking"
```

If a long run is interrupted, use the printed `--start-from` value:

```bash
python3 base.py --db pubmed.sqlite --start-from 6400
```

For a normal local run on macOS, Linux, or Windows/WSL, this plain Python workflow is all you need. Docker and Apptainer are optional container workflows and are not required to fetch PubMed data locally.

## What "Last 24 Hours" Means

The default query is:

```text
all[sb]
```

`base.py` combines that query with NCBI Entrez date filtering:

```text
reldate=1
datetype=edat
```

That means it asks PubMed for records added to or updated in Entrez during the previous 24 hours. It is not a local mirror of the entire PubMed corpus.

The successful full-day retrieval mechanism is:

1. `Entrez.esearch(db="pubmed", term="all[sb]", reldate=1, datetype="edat", usehistory="y", retmax=0)`
2. read the returned record count, `WebEnv`, and `query_key`
3. page through that stable NCBI History Server result set with `Entrez.efetch(...)`
4. parse PubMed XML records in batches
5. insert new PMIDs into SQLite with deduplication

This History Server path is what lets the script fetch a full 24-hour result set instead of relying on a single returned ID list.

## Output

The output database has one table, `pubmed_articles`, keyed by PMID.

Stored fields:

- `pmid`
- `title`
- `journal`
- `pub_date`
- `doi`
- `authors`
- `abstract`
- `fetched_at`
- `raw_json`

Existing SQLite files such as `pubmed.sqlite`, `base.sqlite`, or `new.db` are workflow outputs. They are not required source files.

## Common Commands

Broad 24-hour ingest:

```bash
python3 base.py
```

By default, this writes to `pubmed.sqlite`. Use `--db some-file.sqlite` to choose another output path.

Write parsed records to a plain text JSON Lines file instead of SQLite:

```bash
python3 base.py --jsonl pubmed.jsonl
```

Count matching PubMed records from the last 24 hours without downloading article records:

```bash
python3 base.py --count-only
```

Conservative ingest with smaller batches and more retries:

```bash
python3 base.py --db pubmed.sqlite --fetch-batch 100 --fetch-retries 5
```

Disable EDirect explicitly, matching the default stable path:

```bash
python3 base.py --db pubmed.sqlite --edirect off
```

Use the bundled EDirect path for larger result sets:

```bash
python3 base.py --jsonl pubmed_last24_edirect.jsonl --edirect on
```

Use a different EDirect checkout:

```bash
python3 base.py --jsonl pubmed_last24_edirect.jsonl --edirect on --edirect-prefix /path/to/edirect
```

## Docker

Docker is optional. Use it only if you want to run the project in a Docker container, for example through Docker Desktop or another Linux container runtime.

Build and run:

```bash
docker build -t infogather .
docker run --rm -v "$PWD:/work" -w /work infogather --db /work/pubmed.sqlite
```

The mounted working directory keeps the SQLite output on the host.

## Apptainer

Apptainer is optional. It is mainly for Linux/HPC environments where Apptainer or Singularity is the standard container runtime.

Build and run:

```bash
apptainer build infogather.sif Apptainer.def
apptainer run infogather.sif --db ./pubmed.sqlite
```

## Testing

Run the smoke tests:

```bash
python3 -m unittest -v tests/test_base_smoke.py
```

## Project Layout

- `base.py`: main PubMed ingestion script
- `tests/test_base_smoke.py`: XML parsing smoke tests
- `requirements.txt`: Python runtime dependencies
- `Dockerfile`: Docker image definition
- `Apptainer.def`: Apptainer/Singularity recipe
- `.dockerignore`: Docker build exclusions
- `.env`: optional local NCBI configuration, not required in source control
- `*.sqlite`, `*.db`: generated SQLite outputs

## Notes

- The default retrieval path is Biopython Entrez, not EDirect.
- The bundled `edirect/` directory is used automatically when `--edirect on` is selected and no `--edirect-prefix` is provided.
- `NCBI_API_KEY` is used if present in `.env` or the shell environment.
- Re-running an ingest is safe for duplicates because PMID is the primary key.
- The project currently appends and deduplicates records; it is not a full PubMed mirror or a historical archive.
