# 24-Hour PubMed Download

This guide is for downloading PubMed records added or updated in the last 24 hours.

The main script is:

```bash
python3 base.py
```

It uses:

```text
reldate=1
datetype=edat
```

So "last 24 hours" means records added to or updated in PubMed/Entrez during the previous day.

## 1. Install Requirements

### Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

### macOS

If Python is missing, install it first:

```bash
brew install python
```

Then:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

### Windows

Recommended: use WSL, then follow the Linux commands above.

Native PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate again.

## 2. Configure NCBI

Create a `.env` file in this repo:

```bash
NCBI_EMAIL=your.email@example.com
NCBI_TOOL=my-pubmed-ingester
NCBI_API_KEY=optional_ncbi_api_key
```

`NCBI_API_KEY` is optional, but useful for higher NCBI E-utilities rate limits.

## 3. Count Last 24 Hours

This does not download records:

```bash
python3 base.py --count-only
```

For a query:

```bash
python3 base.py --count-only --query "cancer[Title]"
```

## 4. Download Without a Database

Recommended for large 24-hour PubMed runs. This writes a plain text JSON Lines file:

```bash
python3 base.py \
  --jsonl pubmed_last24.jsonl \
  --edirect on \
  --fetch-batch 100 \
  --fetch-retries 5
```

This writes one JSON object per line to `pubmed_last24.jsonl`.

The repo includes a local EDirect copy in `edirect/`, so no external EDirect install is needed for `--edirect on`.

Resume JSONL after interruption:

```bash
wc -l pubmed_last24.jsonl
```

Then use the line count as `--start-from`:

```bash
python3 base.py \
  --jsonl pubmed_last24.jsonl \
  --edirect on \
  --fetch-batch 100 \
  --fetch-retries 5 \
  --start-from 5000
```

## 5. Download to SQLite Database

Default SQLite output, using the standard Biopython Entrez path:

```bash
python3 base.py
```

This writes to:

```text
pubmed.sqlite
```

Choose another SQLite path:

```bash
python3 base.py --db /tmp/pubmed_last24.sqlite
```

SQLite with bundled EDirect, useful for large result sets:

```bash
python3 base.py \
  --db pubmed_last24.sqlite \
  --edirect on \
  --fetch-batch 100 \
  --fetch-retries 5
```

Resume SQLite after interruption:

```bash
python3 base.py \
  --db pubmed_last24.sqlite \
  --edirect on \
  --fetch-batch 100 \
  --fetch-retries 5 \
  --start-from 5000
```

SQLite deduplicates by PMID, so re-running against the same database will skip records already present.

## 6. Query Examples

### JSONL, no database

Cancer in title:

```bash
python3 base.py --jsonl cancer_last24.jsonl --edirect on --query "cancer[Title]"
```

Records with abstracts:

```bash
python3 base.py --jsonl abstracts_last24.jsonl --edirect on --query "hasabstract[text]"
```

Small test run:

```bash
python3 base.py --jsonl test_last24.jsonl --edirect on --max 25 --fetch-batch 10
```

### SQLite database

Cancer in title:

```bash
python3 base.py --db cancer_last24.sqlite --edirect on --query "cancer[Title]"
```

Records with abstracts:

```bash
python3 base.py --db abstracts_last24.sqlite --edirect on --query "hasabstract[text]"
```

Small test run:

```bash
python3 base.py --db test_last24.sqlite --edirect on --max 25 --fetch-batch 10
```

## 7. Check Output

Count JSONL records:

```bash
wc -l pubmed_last24.jsonl
```

Preview first record:

```bash
head -n 1 pubmed_last24.jsonl
```

Count SQLite records:

```bash
sqlite3 pubmed_last24.sqlite 'select count(*) from pubmed_articles;'
```

Preview SQLite records:

```bash
sqlite3 pubmed_last24.sqlite 'select pmid, title from pubmed_articles limit 5;'
```

Each JSONL row includes fields such as:

```text
pmid
title
journal
pub_date
doi
authors
abstract
fetched_at
```
