# infogather

This repository contains a small PubMed ingestion tool. The main script fetches PubMed records that were added or updated in the last 24 hours, extracts a useful subset of fields, and stores them in a local SQLite database with deduplication by PMID.

The project is centered around a single Python entrypoint:

- `base.py`: main ingestion script

It uses Biopython's `Bio.Entrez` client by default and can optionally use external NCBI EDirect tools if they are installed, but EDirect is not required.

## What `base.py` does

`base.py`:

- queries PubMed for records in the last 24 hours
- fetches records in XML form
- parses fields including PMID, title, journal, publication date, DOI, authors, and abstract
- stores parsed records in SQLite
- avoids duplicate inserts by using PMID as the primary key
- supports retry behavior and resumable fetch offsets for long runs

Typical usage:

```bash
python base.py --db pubmed.sqlite
```

Example with a narrower search term:

```bash
python base.py --db pubmed.sqlite --query "cerebellum AND eye tracking"
```

## How `base.py` is structured

The script is still a single-file tool, but it already has a clear internal split by responsibility.

- configuration helpers:
  `get_ncbi_config()`, `load_dotenv()`, and `configure_entrez()` load `.env`, read NCBI-related environment variables, and configure Biopython Entrez
- database helpers:
  `init_db()`, `existing_pmids()`, and `insert_articles()` create the SQLite schema, check which PMIDs are already present, and insert only new records
- XML parsing:
  `parse_pubmed_record_xml()` extracts the normalized record fields from a single PubMed XML element
- XML stream handling:
  `iter_pubmed_records_from_handle()` and `parse_pubmed_records_from_handle()` parse PubMed XML streams into Python dictionaries while tolerating partial parsing failures
- optional EDirect integration:
  `EDirectStream`, `parse_edirect_prefix()`, and `edirect_available()` support an alternate retrieval path using external `esearch` and `efetch` commands
- Biopython PubMed retrieval:
  `esearch_last_24h()`, `esearch_ids()`, `efetch_pubmed_batch()`, and `efetch_pubmed_by_ids()` handle Entrez search/fetch calls and retry behavior
- command-line orchestration:
  `main()` wires together argument parsing, retrieval mode selection, pagination, deduplication, insertion, progress reporting, and resume support

Functionally, a normal run does this:

1. Load configuration from environment variables and an optional `.env` file.
2. Open or create the SQLite database.
3. Query PubMed for the last 24 hours of matching records.
4. Fetch PubMed XML in batches.
5. Parse each record into normalized Python fields.
6. Check which PMIDs are already in the database.
7. Insert only new records.
8. Print progress and a final summary.

The main persisted schema is a single `pubmed_articles` table with:

- `pmid`
- `title`
- `journal`
- `pub_date`
- `doi`
- `authors`
- `abstract`
- `fetched_at`
- `raw_json`

That means the script is currently optimized for a simple append-and-deduplicate workflow rather than a richer relational data model.

## Obvious next features

These are the most immediate additions that would fit the current codebase without a redesign:

- add a `README` example for common queries:
  for example broad ingest, topic-specific ingest, and small test runs with `--max`
- add export commands:
  support writing records to CSV or JSON in addition to SQLite
- add a date-range option:
  replace the fixed last-24-hours behavior with explicit start/end or `--days N`
- add update behavior:
  right now inserts are `INSERT OR IGNORE`; a new mode could refresh existing rows if PubMed metadata changed
- add more parsed fields:
  affiliations, MeSH terms, keywords, publication types, language, or PMID status are obvious candidates
- add better operational logging:
  write progress and failures to a log file instead of only stdout/stderr
- add schema/version metadata:
  record when the database was created and what script/schema version produced it
- add a query or report utility:
  a second script could inspect the SQLite DB and summarize counts by day, journal, or keyword
- add tests beyond smoke coverage:
  especially for XML edge cases, EDirect mode behavior, and database insertion logic
- add a lock or single-run guard:
  useful if collaborators might accidentally run multiple ingests against the same SQLite file
- add packaged CLI entrypoints:
  move from a single script toward a small installable package with clearer commands

If the goal stays "small and portable", the best immediate feature work is probably:

- better tests
- explicit date-range control
- export to CSV/JSON
- optional update/upsert behavior for existing PMIDs

## Repository layout

Top-level files and directories:

- `base.py`: main PubMed ingestion script
- `tests/test_base_smoke.py`: small smoke tests for XML parsing behavior
- `requirements.txt`: Python package requirements for running the script without a repo-local virtualenv
- `Dockerfile`: Linux Docker image definition for portable containerized execution
- `Apptainer.def`: Apptainer/Singularity recipe for Linux and HPC environments
- `.dockerignore`: excludes local artifacts from Docker build context
- `.env`: optional local environment file read by `base.py` if present
- `base.sqlite`, `new.db`: existing SQLite database artifacts in this working tree
- `stuff.md`: auxiliary notes/documentation not required to run the script

Generated or local-only artifacts you may also see:

- `__pycache__/`: Python bytecode cache
- test caches or temporary files created locally

## Requirements

This repo no longer depends on a checked-in virtualenv. The runtime dependencies are declared in `requirements.txt`.

Current Python dependencies:

- `biopython`
- `numpy`

System/runtime assumptions:

- Python 3.12 or compatible recent Python 3
- network access to NCBI if you are actually fetching PubMed data
- a writable path for the SQLite database file

Optional environment variables:

- `NCBI_EMAIL`: email sent to NCBI via Entrez
- `NCBI_TOOL`: tool name sent to NCBI
- `NCBI_API_KEY`: optional NCBI API key for higher rate limits
- `EDIRECT_PREFIX`: optional command prefix if using EDirect through something like WSL

## Running the project

What collaborators can do now:

- plain Python:
  `python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`
- Docker:
  `docker build -t infogather .`
- Apptainer:
  `apptainer build infogather.sif Apptainer.def`

### Plain Python

Create your own environment and install dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python base.py --db pubmed.sqlite
```

### Docker

Build and run:

```bash
docker build -t infogather .
docker run --rm -v "$PWD:/work" -w /work infogather --db /work/pubmed.sqlite
```

This keeps the output database on the host by mounting the working directory into the container.

### Apptainer

Build and run on Linux:

```bash
apptainer build infogather.sif Apptainer.def
apptainer run infogather.sif --db ./pubmed.sqlite
```

## Testing

Run the smoke tests with:

```bash
python -m unittest -v tests/test_base_smoke.py
```

## Notes

- EDirect is optional. The default code path uses Biopython and works without installing EDirect.
- EDirect is not bundled in this repo.
- The last-24-hours query can return a large number of records, so using `--max` is useful for smaller test runs.
- The SQLite database is part of the workflow output, not a required source file.
