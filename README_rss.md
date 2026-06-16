# RSS ingestion

`rss.py` fetches articles from a single RSS or Atom feed, normalizes paper-like metadata, keeps records from the past 24 hours by default, and stores them in a local SQLite database.

The script is useful for journal or publisher feeds that expose recent article metadata through RSS.

## Basic usage

```bash
python rss.py -x nature --feed-url "https://www.nature.com/nature.rss"
```

`-x` / `--source` is a short source slug. It is normalized and used to choose the output database name. For example:

```bash
python rss.py -x nature --feed-url "https://www.nature.com/nature.rss"
```

writes to:

```text
nature.sqlite
```

## Common examples

Process at most 100 feed entries:

```bash
python rss.py -x nature --feed-url "https://www.nature.com/nature.rss" --max 100
```

Refresh existing rows for the RSS IDs seen in the current run:

```bash
python rss.py -x nature --feed-url "https://www.nature.com/nature.rss" --refresh
```

Store every parsed feed entry instead of filtering to the past 24 hours:

```bash
python rss.py -x nature --feed-url "https://www.nature.com/nature.rss" --no-date-filter
```

Use a custom HTTP User-Agent:

```bash
python rss.py \
  -x nature \
  --feed-url "https://www.nature.com/nature.rss" \
  --user-agent "my-rss-ingester/1.0"
```

Sleep after fetching the feed, which can be helpful when chaining polite requests:

```bash
python rss.py -x nature --feed-url "https://www.nature.com/nature.rss" --sleep 2
```

## Command-line options

- `-x`, `--source`: required short source slug used for the output database name.
- `--feed-url`: required RSS or Atom feed URL.
- `--max`: optional cap on feed entries processed. `0` means no cap.
- `--sleep`: seconds to sleep after fetching the feed. Default is `0`.
- `--refresh`: delete existing rows for fetched RSS IDs before inserting the current records.
- `--no-date-filter`: store all parsed feed entries instead of only recent entries.
- `--user-agent`: HTTP User-Agent sent when requesting the feed.

## Date filtering

By default, `rss.py` stores records whose `updated_date` or `pub_date` is within the past 24 hours.

If an entry has no usable published or updated date, the script keeps it and prints a warning. This prevents undated feeds from silently dropping all records.

Use `--no-date-filter` when you want to backfill all entries currently exposed by a feed.

## Normalized fields

Each RSS entry is normalized into a record with:

- `rss_id`
- `source`
- `title`
- `journal`
- `pub_date`
- `updated_date`
- `doi`
- `authors`
- `abstract`
- `categories`
- `primary_category`
- `url`
- `pdf_url`
- `feed_url`
- `fetched_at`
- `raw_json`

The script tries to extract DOI values from common feed fields such as `doi`, `prism_doi`, `dc_identifier`, `identifier`, `id`, `guid`, `link`, `title`, `summary`, and `description`.

## SQLite schema

The output database contains one table, `rss_articles`.

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

The table also has indexes on:

- `source`
- `pub_date`
- `updated_date`

## Deduplication

Rows are deduplicated by `rss_id`.

The `rss_id` is built in this order:

1. DOI, when available: `doi:<normalized-doi>`
2. feed entry ID or GUID: `id:<entry-id>`
3. article URL: `url:<url>`
4. SHA-256 hash of title, URL, and publication date: `hash:<digest>`

Without `--refresh`, existing rows are skipped with `INSERT OR IGNORE`.

With `--refresh`, rows matching the current run's RSS IDs are deleted first, then reinserted from the current feed payload.

## Dependencies

`rss.py` uses Python's standard library for HTTP requests and SQLite, but it requires `feedparser` to parse RSS/Atom payloads.

Install it with:

```bash
pip install feedparser
```

## Output

A successful run prints progress messages similar to:

```text
[info] source=nature feed_url=https://www.nature.com/nature.rss db_path=nature.sqlite
[page] source=nature fetched_entries=100
[insert] parsed=100 new=12 inserted=12
[done] source=nature feed_url=https://www.nature.com/nature.rss db_path=nature.sqlite total_seen=100 total_kept_last24h=12 total_new=12 total_inserted=12 timestamp=...
```

The generated SQLite database is workflow output and can be safely regenerated from the feed.
