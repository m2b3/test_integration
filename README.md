# Science Communication Social Feed Tools

This repository contains two small command-line tools for collecting recent public posts about scientists, people, and journals:

- `bluesky.py` fetches posts from the public Bluesky API.
- `mastodon.py` fetches public statuses from Mastodon instances.

Both tools save results as SQLite and JSON files.

## Setup

These scripts require Python 3 and the `requests` package. If you cloned or downloaded this repository, create your own virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install requests
```

Then check that the scripts run:

```bash
python bluesky.py --help
python mastodon.py --help
```

If you do not want to use a virtual environment, install `requests` in whatever Python environment you plan to use.

The Mastodon script expects `mastodon_instances.json` in the current directory by default. If that file is missing, `mastodon.py` will create it automatically with the default instance list.

## Command Pattern

Both tools use an intentionally fixed command pattern:

```bash
python <tool>.py - <mode> - "<query>" - days <N> [options]
```

The literal `-` separators and the literal word `days` are required. The mode must be either `scientist` or `journal`.

## Bluesky

Bluesky examples:

```bash
python bluesky.py - scientist - "Nicole Rust" - days 1
python bluesky.py - scientist - "Nicole Rust" - days 30
python bluesky.py - journal - "Nature" - days 1
python bluesky.py - journal - "Nature" - days 30
```

Optional arguments:

```bash
--journal-mode auto|account|keyword
--max-posts 500
--limit 100
--out-dir data
```

Bluesky scientist mode searches for likely account matches, fetches recent posts from the selected account, and writes `<safe_query>.sqlite` and `<safe_query>.json`.

Bluesky journal mode supports:

- `auto`: search for an official-looking journal account first, then fall back to keyword search.
- `account`: only use official-looking journal accounts.
- `keyword`: search posts mentioning the journal/topic.

## Mastodon

Mastodon is decentralized, so there is no single global API endpoint. The tool searches across instances listed in `mastodon_instances.json`.

Default instances:

```json
{
  "instances": [
    "mastodon.social",
    "mstdn.social",
    "fediscience.org",
    "scholar.social",
    "mathstodon.xyz",
    "mstdn.science",
    "scicomm.xyz"
  ]
}
```

### Recommended Mastodon Scientist Format

For scientists or people, the most reliable Mastodon command is to provide the known account:

```bash
python mastodon.py - scientist - "Terence Tao" - days 10 --account @tao@mathstodon.xyz
python mastodon.py - scientist - "Satrevik" - days 10 --account @satrevik@fediscience.org
```

The fixed command prefix is still required:

```bash
python mastodon.py - <mode> - "<query>" - days <N>
```

Then add `--account @user@instance` after it.

Why this matters: many Mastodon instances return `401 Unauthorized` for anonymous account search, but still allow public lookup and public status fetching for a known account.

With `--account`, the tool does this:

1. Parse `@user@instance`.
2. Call `GET https://<instance>/api/v1/accounts/lookup?acct=<user>`.
3. Get the account ID.
4. Call `GET https://<instance>/api/v1/accounts/{account_id}/statuses`.
5. Filter statuses to the requested time window.
6. Save SQLite and JSON output.

### Mastodon Search-Based Examples

Search-based account discovery is still available, but it may fail if instances block anonymous search:

```bash
python mastodon.py - scientist - "Nicole Rust" - days 1
python mastodon.py - scientist - "Nicole Rust" - days 30
```

Journal examples:

```bash
python mastodon.py - journal - "Nature" - days 1
python mastodon.py - journal - "Nature" - days 30
python mastodon.py - journal - "Nature" - days 1 --journal-mode auto
python mastodon.py - journal - "Nature" - days 1 --journal-mode account
python mastodon.py - journal - "Nature" - days 1 --journal-mode keyword
```

Optional arguments:

```bash
--instances mastodon_instances.json
--account @user@instance
--journal-mode auto|account|keyword
--max-posts 500
--max-accounts 5
--limit 40
--out-dir data
--timeout 15
```

Mastodon journal mode supports:

- `auto`: search for official-looking journal accounts first, then fall back to keyword search.
- `account`: only use official-looking journal accounts.
- `keyword`: search statuses mentioning the journal/topic across configured instances.

Mastodon keyword search is instance-dependent and may not cover the full Fediverse.

## Outputs

Both tools write:

```text
<safe_query>.sqlite
<safe_query>.json
```

Examples:

```text
Nicole_Rust.sqlite
Nicole_Rust.json
Nature.sqlite
Nature.json
Terence_Tao.sqlite
Terence_Tao.json
```

Use `--out-dir` to place output files in a separate directory:

```bash
python mastodon.py - scientist - "Terence Tao" - days 10 --account @tao@mathstodon.xyz --out-dir data
python bluesky.py - journal - "Nature" - days 30 --out-dir data
```

## Notes

These tools only use public APIs. They do not implement login, OAuth, browser automation, or HTML scraping.

Mastodon search behavior varies by instance. A `401` during search usually means anonymous search is disabled on that instance; it does not necessarily mean public posts are inaccessible.
