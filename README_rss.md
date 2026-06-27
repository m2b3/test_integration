# RSS and Journal Feed Ingestion

This repository includes a small RSS/Atom workflow for collecting recent journal articles into SQLite. Use `rss.py` when you already know the feed URL, and use `rss_discover_journal.py` when you want the project to look up a journal, discover candidate feeds, validate them, and ingest the best match.

RSS databases can be merged and indexed later by `All_embedding.py` alongside PubMed, arXiv, bioRxiv, medRxiv, PsyArXiv, SocArXiv, and OpenReview databases.

## Setup

Install the project dependencies from the shared requirements file:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The RSS tools use:

- `feedparser` for RSS/Atom parsing
- `requests` for Crossref and homepage/feed discovery
- `beautifulsoup4` for HTML feed autodiscovery

## Ingest a Known Feed

Run `rss.py` with a short source slug and a feed URL:

```bash
python rss.py \
  --source nature \
  --feed-url "https://www.nature.com/nature.rss"
```

The source slug is normalized and used as the output database name. For example, `--source nature` writes `nature.sqlite`.

Useful options:

- `--max`: cap feed entries processed; `0` means no cap.
- `--refresh`: replace existing rows for RSS IDs seen in the current run.
- `--no-date-filter`: store every parsed feed entry instead of only recent entries.
- `--sleep`: sleep after fetching the feed, useful when chaining polite requests.
- `--user-agent`: override the HTTP User-Agent sent to the feed.

Examples:

```bash
python rss.py --source nature --feed-url "https://www.nature.com/nature.rss" --max 100
python rss.py --source nature --feed-url "https://www.nature.com/nature.rss" --refresh
python rss.py --source nature --feed-url "https://www.nature.com/nature.rss" --no-date-filter
```

## Discover a Journal Feed

Run `rss_discover_journal.py` with a journal name:

```bash
python rss_discover_journal.py --journal "Nature"
```

The discovery script:

1. Queries Crossref for journal candidates.
2. Selects the best matching title.
3. Builds likely homepage and direct-feed candidates.
4. Looks for RSS/Atom `<link rel="alternate">` entries on working homepages.
5. Validates candidate feeds by parsing entries.
6. Ingests the selected feed into `<journal-slug>.sqlite`.

You can pass an email for Crossref's polite pool:

```bash
python rss_discover_journal.py --journal "Nature" --mailto "you@example.com"
```

The discovery script supports the same ingest options as `rss.py` for `--max`, `--refresh`, `--no-date-filter`, and `--user-agent`.

## Date Filtering

By default, both RSS ingestion paths keep entries whose `updated_date` or `pub_date` is within the last 24 hours.

If an entry has no usable published or updated date, the script keeps it and prints a warning. This prevents undated feeds from silently dropping all records.

Use `--no-date-filter` when you want to backfill all entries currently exposed by a feed.

## SQLite Output

Each RSS entry is normalized into the `rss_articles` table:

```sql
CREATE TABLE IF NOT EXISTS rss_articles (
    rss_id              TEXT PRIMARY KEY,
    source              TEXT,
    title               TEXT,
    journal             TEXT,
    pub_date            TEXT,
    updated_date        TEXT,
    doi                 TEXT,
    authors             TEXT,
    abstract            TEXT,
    categories          TEXT,
    primary_category    TEXT,
    url                 TEXT,
    pdf_url             TEXT,
    feed_url            TEXT,
    fetched_at          TEXT,
    raw_json            TEXT
);
```

Indexes are created on `source`, `pub_date`, and `updated_date`.

## Deduplication

Rows are deduplicated by `rss_id`.

The ID is built in this order:

1. DOI, when available: `doi:<normalized-doi>`
2. Feed entry ID or GUID: `id:<entry-id>`
3. Article URL: `url:<url>`
4. SHA-256 hash of title, URL, and publication date: `hash:<digest>`

Without `--refresh`, existing rows are skipped with `INSERT OR IGNORE`. With `--refresh`, rows matching the current run's RSS IDs are deleted first and reinserted from the current feed payload.

## Feed Utility Scripts

The repository also includes helper scripts for maintaining and checking feed lists:

- `check_rss_feeds.py`: validates `rss_feeds_raw.json` and writes checked, summary, and blocked-feed JSON outputs.
- `scrape_feedspot_science.py`: discovers and checks feeds from Feedspot's public science list.
- `scrape_aps_feeds.py`: parses a local `aps_feeds.html` snapshot and checks discovered APS feeds.
- `scrape_oup_rss.py`: parses a local `oup_rss.html` snapshot and checks OUP feeds; set `OUP_COOKIE` when a browser-derived cookie is needed.

The feed-checking utilities currently classify active feeds as those with a latest item dated in 2025 or 2026.

## Example Output

A successful `rss.py` run prints progress messages similar to:

```text
[info] source=nature feed_url=https://www.nature.com/nature.rss db_path=nature.sqlite
[page] source=nature fetched_entries=100
[insert] parsed=100 new=12 inserted=12
[done] source=nature feed_url=https://www.nature.com/nature.rss db_path=nature.sqlite total_seen=100 total_kept_last24h=12 total_new=12 total_inserted=12 timestamp=...
```

The generated SQLite database is workflow output and can be safely regenerated from the feed.
