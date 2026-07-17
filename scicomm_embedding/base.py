#!/usr/bin/env python3
"""
Fetch PubMed records newly added/updated in the last 24 hours and store them
into a SQLite database with deduping by PMID. Includes PubmedArticle and
PubmedBookArticle records when present.

- Uses Bio.Entrez (ESearch + EFetch via History server)
- Dedupe = PMID primary key in SQLite
- Fetch window = reldate=1 day using Entrez date (edat)
- Batches through results using WebEnv/query_key history

Usage:
  python base.py --db pubmed.sqlite
  python base.py --db pubmed.sqlite --query 'cerebellum AND eye tracking'
  python base.py --db pubmed.sqlite --max 20000
  python base.py --db pubmed.sqlite --max-tries 5 --sleep-between-tries 2.0
  python base.py --db pubmed.sqlite --fetch-retries 5 --fetch-batch 100
  python base.py --db pubmed.sqlite --start-from 6400  # Resume after error

Requirements:
  pip install biopython

Environment (recommended):
  export NCBI_EMAIL="you@example.com"
  export NCBI_TOOL="my-pubmed-ingester"
  export NCBI_API_KEY="..."   # optional; allows higher rate limits
  export EDIRECT_PREFIX="wsl" # optional; command prefix or EDirect directory

Notes:
  - Pulling "everything in PubMed in the last 24h" can be large.
  - This script stores abstracts when available.
  - If a .env file exists in the working directory, it will be loaded.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import IncompleteRead
from typing import Callable, Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError

from Bio import Entrez


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def chunked(seq: List[str], n: int) -> Iterable[List[str]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


@dataclass
class NCBIConfig:
    email: str
    tool: str
    api_key: Optional[str]


def get_ncbi_config() -> NCBIConfig:
    email = os.getenv("NCBI_EMAIL", "").strip() or "unknown@example.com"
    tool = os.getenv("NCBI_TOOL", "").strip() or "pubmed_last24h_to_sqlite"
    api_key = os.getenv("NCBI_API_KEY", "").strip() or None
    return NCBIConfig(email=email, tool=tool, api_key=api_key)


def load_dotenv(path: str = ".env") -> None:
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'").strip('"')
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError as e:
        print(f"[warn] Failed to read {path}: {e}", file=sys.stderr)


def configure_entrez(cfg: NCBIConfig, *, max_tries: Optional[int], sleep_between_tries: Optional[float]) -> None:
    Entrez.email = cfg.email
    Entrez.tool = cfg.tool
    if cfg.api_key:
        Entrez.api_key = cfg.api_key
    if max_tries is not None:
        Entrez.max_tries = max_tries
    if sleep_between_tries is not None:
        Entrez.sleep_between_tries = sleep_between_tries


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pubmed_articles (
            pmid        TEXT PRIMARY KEY,
            title       TEXT,
            journal     TEXT,
            pub_date    TEXT,
            doi         TEXT,
            authors     TEXT,   -- JSON array of strings
            abstract    TEXT,
            fetched_at  TEXT,
            raw_json    TEXT    -- optional: store extracted fields as JSON blob
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_pubmed_articles_pub_date
        ON pubmed_articles(pub_date);
        """
    )
    conn.commit()
    return conn


def existing_pmids(conn: sqlite3.Connection, pmids: List[str]) -> set[str]:
    if not pmids:
        return set()
    # SQLite has a max variable limit; keep it safe.
    out: set[str] = set()
    for block in chunked(pmids, 800):
        qmarks = ",".join(["?"] * len(block))
        rows = conn.execute(f"SELECT pmid FROM pubmed_articles WHERE pmid IN ({qmarks})", block).fetchall()
        out.update(r[0] for r in rows)
    return out


def insert_articles(conn: sqlite3.Connection, records: List[Dict[str, object]]) -> int:
    if not records:
        return 0
    cur = conn.cursor()
    inserted = 0
    for r in records:
        try:
            cur.execute(
                """
                INSERT OR IGNORE INTO pubmed_articles
                (pmid, title, journal, pub_date, doi, authors, abstract, fetched_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    r.get("pmid"),
                    r.get("title"),
                    r.get("journal"),
                    r.get("pub_date"),
                    r.get("doi"),
                    json.dumps(r.get("authors", []), ensure_ascii=False),
                    r.get("abstract"),
                    r.get("fetched_at"),
                    json.dumps(r, ensure_ascii=False),
                ),
            )
            if cur.rowcount == 1:
                inserted += 1
        except sqlite3.Error as e:
            # If a single row fails, continue (but surface the error).
            print(f"[db] insert failed for PMID={r.get('pmid')}: {e}", file=sys.stderr)
    conn.commit()
    return inserted


def parse_pubmed_record_xml(record_el: ET.Element) -> Dict[str, object]:
    # Best-effort extraction from PubmedArticle or PubmedBookArticle XML.
    month_map = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    year_re = re.compile(r"(19|20)\d{2}")

    def text_from_el(el: Optional[ET.Element], *, use_itertext: bool = False) -> Optional[str]:
        if el is None:
            return None
        txt = "".join(el.itertext()) if use_itertext else (el.text or "")
        txt = txt.strip()
        return txt or None

    def first_text(paths: List[str], *, use_itertext: bool = False) -> Optional[str]:
        for path in paths:
            el = record_el.find(path)
            txt = text_from_el(el, use_itertext=use_itertext)
            if txt:
                return txt
        return None

    def normalize_month(m: str) -> Optional[str]:
        m = (m or "").strip()
        if not m:
            return None
        if m.isdigit():
            val = int(m)
            if 1 <= val <= 12:
                return f"{val:02d}"
            return None
        key = m.lower()[:3]
        if key in month_map:
            return f"{month_map[key]:02d}"
        return None

    def normalize_day(d: str) -> Optional[str]:
        d = (d or "").strip()
        if d.isdigit():
            val = int(d)
            if 1 <= val <= 31:
                return f"{val:02d}"
        return None

    def parse_medline_date(md: str) -> Optional[str]:
        md = (md or "").strip()
        if not md:
            return None
        y_match = year_re.search(md)
        if not y_match:
            return None
        year = y_match.group(0)
        tokens = md.replace("-", " ").replace("/", " ").split()
        month = None
        day = None
        for tok in tokens:
            if month is None:
                month = normalize_month(tok)
                if month:
                    continue
            if day is None:
                day = normalize_day(tok)
        if month and day:
            return f"{year}-{month}-{day}"
        if month:
            return f"{year}-{month}"
        return year

    def parse_pub_date(date_el: Optional[ET.Element]) -> Optional[str]:
        if date_el is None:
            return None
        y = (date_el.findtext("Year") or "").strip()
        m = (date_el.findtext("Month") or "").strip()
        d = (date_el.findtext("Day") or "").strip()
        medline_date = (date_el.findtext("MedlineDate") or "").strip()
        if y:
            month = normalize_month(m) if m else None
            day = normalize_day(d) if d else None
            if month and day:
                return f"{y}-{month}-{day}"
            if month:
                return f"{y}-{month}"
            return y
        if medline_date:
            return parse_medline_date(medline_date) or None
        return None

    pmid = first_text(["./MedlineCitation/PMID", "./BookDocument/PMID"])
    title = first_text(
        [
            "./MedlineCitation/Article/ArticleTitle",
            "./BookDocument/ArticleTitle",
            "./BookDocument/Book/BookTitle",
            "./BookDocument/Book/CollectionTitle",
        ],
        use_itertext=True,
    )
    journal = first_text(
        [
            "./MedlineCitation/Article/Journal/Title",
            "./BookDocument/Book/BookTitle",
            "./BookDocument/Book/CollectionTitle",
            "./BookDocument/Book/Publisher/PublisherName",
        ],
        use_itertext=True,
    )

    # Publication date: try ArticleDate, else JournalIssue PubDate, else Book PubDate
    pub_date = parse_pub_date(record_el.find("./MedlineCitation/Article/ArticleDate"))
    if pub_date is None:
        pub_date = parse_pub_date(record_el.find("./MedlineCitation/Article/Journal/JournalIssue/PubDate"))
    if pub_date is None:
        pub_date = parse_pub_date(record_el.find("./BookDocument/Book/PubDate"))

    # Authors
    authors: List[str] = []
    author_paths = [
        "./MedlineCitation/Article/AuthorList/Author",
        "./BookDocument/AuthorList/Author",
        "./BookDocument/Book/AuthorList/Author",
    ]
    for path in author_paths:
        for a in record_el.findall(path):
            last = (a.findtext("LastName") or "").strip()
            fore = (a.findtext("ForeName") or "").strip()
            coll = (a.findtext("CollectiveName") or "").strip()
            if coll:
                authors.append(coll)
            elif last and fore:
                authors.append(f"{fore} {last}")
            elif last:
                authors.append(last)

    # DOI (try any ArticleIdList under the record)
    doi = None
    for aid in record_el.findall(".//ArticleIdList/ArticleId"):
        if (aid.attrib.get("IdType") or "").lower() == "doi":
            doi = (aid.text or "").strip() or None
            break

    # Abstract (may have multiple parts)
    abstract_parts: List[str] = []
    for ab in record_el.findall(".//Abstract/AbstractText"):
        label = (ab.attrib.get("Label") or "").strip()
        piece = "".join(ab.itertext()).strip()
        if piece:
            abstract_parts.append(f"{label}: {piece}" if label else piece)
    abstract = "\n\n".join(abstract_parts) if abstract_parts else None

    return {
        "pmid": pmid,
        "title": title,
        "journal": journal,
        "pub_date": pub_date,
        "doi": doi,
        "authors": authors,
        "abstract": abstract,
        "fetched_at": utc_now_iso(),
    }


def iter_pubmed_records_from_handle(handle) -> Iterable[Dict[str, object]]:
    try:
        for event, elem in ET.iterparse(handle, events=["end"]):
            if elem.tag in {"PubmedArticle", "PubmedBookArticle"}:
                try:
                    record = parse_pubmed_record_xml(elem)
                    if record.get("pmid"):
                        yield record
                except Exception as e:
                    # Log but continue if we can't parse a single record
                    print(f"[warn] Failed to parse record: {e}", file=sys.stderr)
                finally:
                    elem.clear()
    except ET.ParseError as e:
        # XML stream is malformed - log the error and stop iteration
        print(f"[error] XML parse error: {e}", file=sys.stderr)
        print(f"[error] This may be due to malformed data from PubMed or network issues.", file=sys.stderr)
        # Stop iteration but don't crash - allow partial results to be saved
        return
    except (IncompleteRead, URLError, ConnectionError, OSError) as e:
        # Network/connection error during parsing - log and re-raise so retry logic can handle it
        print(f"[error] Connection error during XML parsing: {type(e).__name__}: {e}", file=sys.stderr)
        raise


def parse_pubmed_records_from_handle(handle) -> List[Dict[str, object]]:
    return list(iter_pubmed_records_from_handle(handle))


class EDirectStream:
    def __init__(self, term: str, prefix: List[str]) -> None:
        self._p1 = None
        self._p2 = None
        self._iter = None
        self._start(term, prefix)

    def _start(self, term: str, prefix: List[str]) -> None:
        esearch_cmd = edirect_command(prefix, "esearch") + [
            "-db",
            "pubmed",
            "-query",
            term,
            "-datetype",
            "edat",
            "-reldate",
            "1",
        ]
        efetch_cmd = edirect_command(prefix, "efetch") + ["-format", "xml"]
        self._p1 = subprocess.Popen(esearch_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._p2 = subprocess.Popen(efetch_cmd, stdin=self._p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if self._p1.stdout:
            self._p1.stdout.close()
        self._iter = iter_pubmed_records_from_handle(self._p2.stdout)

    def __iter__(self) -> "EDirectStream":
        return self

    def __next__(self) -> Dict[str, object]:
        if self._iter is None:
            raise StopIteration
        return next(self._iter)

    def close(self, force: bool = False) -> None:
        if force:
            for p in (self._p2, self._p1):
                if p and p.poll() is None:
                    p.terminate()
        for p in (self._p2, self._p1):
            if not p:
                continue
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        if self._p1 and self._p1.returncode not in (0, None):
            # Don't raise if we stopped early - partial results are OK
            if not force:
                raise RuntimeError(f"EDirect esearch failed (code {self._p1.returncode})")
            else:
                print(f"[warn] EDirect esearch exited with code {self._p1.returncode}", file=sys.stderr)
        if self._p2 and self._p2.returncode not in (0, None):
            # -9 indicates SIGKILL (OOM or manual kill) - warn but don't crash
            if self._p2.returncode == -9:
                print(f"[warn] EDirect efetch was killed (code -9). This may indicate memory pressure.", file=sys.stderr)
                print(f"[warn] Partial results have been saved. Consider using --max to limit records.", file=sys.stderr)
            elif not force:
                raise RuntimeError(f"EDirect efetch failed (code {self._p2.returncode})")
            else:
                print(f"[warn] EDirect efetch exited with code {self._p2.returncode}", file=sys.stderr)


def parse_edirect_prefix(raw: str) -> List[str]:
    raw = (raw or "").strip()
    if not raw:
        return []
    return shlex.split(raw)


def edirect_candidate_dirs() -> List[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    return [
        os.path.join(here, "edirect"),
        os.path.join(parent, "igather2", "edirect"),
    ]


def resolve_edirect_prefix(raw: str) -> List[str]:
    prefix = parse_edirect_prefix(raw)
    if prefix:
        return prefix
    for candidate in edirect_candidate_dirs():
        if os.path.isfile(os.path.join(candidate, "esearch")) and os.path.isfile(os.path.join(candidate, "efetch")):
            return [candidate]
    return []


def edirect_command(prefix: List[str], executable: str) -> List[str]:
    if len(prefix) == 1 and os.path.isdir(prefix[0]):
        return [os.path.join(prefix[0], executable)]
    return prefix + [executable]


def edirect_available(prefix: List[str]) -> bool:
    if prefix:
        return all(
            shutil.which(command[0]) or os.path.isfile(command[0])
            for command in (
                edirect_command(prefix, "esearch"),
                edirect_command(prefix, "efetch"),
            )
        )
    return bool(shutil.which("esearch") and shutil.which("efetch"))


def edirect_pubmed_ids(term: str, prefix: List[str]) -> List[str]:
    esearch_cmd = edirect_command(prefix, "esearch") + [
        "-db",
        "pubmed",
        "-query",
        term,
        "-datetype",
        "edat",
        "-reldate",
        "1",
    ]
    efetch_cmd = edirect_command(prefix, "efetch") + ["-format", "uid"]
    p1 = subprocess.Popen(esearch_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    p2 = subprocess.Popen(
        efetch_cmd,
        stdin=p1.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if p1.stdout:
        p1.stdout.close()
    stdout, stderr = p2.communicate()
    p1.wait()
    if p1.returncode not in (0, None):
        raise RuntimeError(f"EDirect esearch failed (code {p1.returncode})")
    if p2.returncode not in (0, None):
        detail = (stderr or "").strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"EDirect efetch -format uid failed (code {p2.returncode}){suffix}")
    ids = [line.strip() for line in stdout.splitlines() if line.strip().isdigit()]
    if not ids:
        raise RuntimeError("EDirect returned no PubMed IDs.")
    return ids


def _entrez_read_with_retries(open_request: Callable[[], object], label: str, max_retries: int = 3) -> object:
    for attempt in range(max_retries):
        handle = None
        try:
            handle = open_request()
            return Entrez.read(handle)
        except HTTPError as e:
            retryable = e.code == 429 or 500 <= e.code < 600
            if retryable and attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(
                    f"[warn] {label} failed with HTTP {e.code}: {e.reason}. "
                    f"Retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})...",
                    file=sys.stderr,
                )
                time.sleep(wait_time)
                continue
            raise
        except (IncompleteRead, URLError, ConnectionError, OSError) as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(
                    f"[warn] {label} connection error: {type(e).__name__}: {e}. "
                    f"Retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})...",
                    file=sys.stderr,
                )
                time.sleep(wait_time)
                continue
            raise
        finally:
            if handle is not None:
                handle.close()
    raise RuntimeError(f"{label} failed after {max_retries} attempts.")


def esearch_last_24h(cfg: NCBIConfig, term: str, max_retries: int = 3) -> tuple[int, str, str]:
    params = {
        "db": "pubmed",
        "term": term,
        "usehistory": "y",
        "retmax": 0,
        "reldate": 1,
        "datetype": "edat",
    }
    data = _entrez_read_with_retries(lambda: Entrez.esearch(**params), "ESearch history", max_retries)
    count = int(data.get("Count", 0))
    webenv = data.get("WebEnv")
    query_key = data.get("QueryKey")
    if not webenv or query_key is None:
        raise RuntimeError("ESearch did not return WebEnv/query_key.")
    return count, str(webenv), str(query_key)


def esearch_ids(term: str, retstart: int, retmax: int, max_retries: int = 3) -> List[str]:
    params = {
        "db": "pubmed",
        "term": term,
        "retstart": retstart,
        "retmax": retmax,
        "reldate": 1,
        "datetype": "edat",
    }
    data = _entrez_read_with_retries(
        lambda: Entrez.esearch(**params),
        f"ESearch IDs retstart={retstart}",
        max_retries,
    )
    return [str(i) for i in data.get("IdList", [])]


def efetch_pubmed_batch(webenv: str, query_key: str, retstart: int, retmax: int, max_retries: int = 3) -> List[Dict[str, object]]:
    params = {
        "db": "pubmed",
        "WebEnv": webenv,
        "query_key": query_key,
        "retstart": retstart,
        "retmax": retmax,
        "retmode": "xml",
    }

    for attempt in range(max_retries):
        try:
            handle = Entrez.efetch(**params)
            try:
                return parse_pubmed_records_from_handle(handle)
            finally:
                handle.close()
        except (IncompleteRead, URLError, ConnectionError, OSError) as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                print(
                    f"[warn] Connection error at retstart={retstart}: {type(e).__name__}: {e}",
                    file=sys.stderr
                )
                print(f"[warn] Retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})...", file=sys.stderr)
                time.sleep(wait_time)
            else:
                print(
                    f"[error] Failed after {max_retries} attempts at retstart={retstart}: {type(e).__name__}: {e}",
                    file=sys.stderr
                )
                raise
        except ET.ParseError as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(
                    f"[warn] XML parse error at retstart={retstart}: {e}",
                    file=sys.stderr
                )
                print(f"[warn] Retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})...", file=sys.stderr)
                time.sleep(wait_time)
            else:
                print(
                    f"[error] Failed to parse XML after {max_retries} attempts at retstart={retstart}",
                    file=sys.stderr
                )
                raise

    # Should never reach here, but satisfy type checker
    return []


def efetch_pubmed_by_ids(pmids: List[str], max_retries: int = 3) -> List[Dict[str, object]]:
    if not pmids:
        return []
    params = {
        "db": "pubmed",
        "id": pmids,
        "retmode": "xml",
    }

    for attempt in range(max_retries):
        try:
            handle = Entrez.efetch(**params)
            try:
                return parse_pubmed_records_from_handle(handle)
            finally:
                handle.close()
        except (IncompleteRead, URLError, ConnectionError, OSError) as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                print(
                    f"[warn] Connection error for {len(pmids)} PMIDs: {type(e).__name__}: {e}",
                    file=sys.stderr
                )
                print(f"[warn] Retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})...", file=sys.stderr)
                time.sleep(wait_time)
            else:
                print(
                    f"[error] Failed after {max_retries} attempts for {len(pmids)} PMIDs: {type(e).__name__}: {e}",
                    file=sys.stderr
                )
                raise
        except ET.ParseError as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(
                    f"[warn] XML parse error for {len(pmids)} PMIDs: {e}",
                    file=sys.stderr
                )
                print(f"[warn] Retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})...", file=sys.stderr)
                time.sleep(wait_time)
            else:
                print(
                    f"[error] Failed to parse XML after {max_retries} attempts for {len(pmids)} PMIDs",
                    file=sys.stderr
                )
                raise

    # Should never reach here, but satisfy type checker
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="SQLite database path")
    ap.add_argument(
        "--query",
        default="all[sb]",
        help="PubMed query term. Default: all[sb] (broadest). Example: 'cerebellum AND eye tracking'",
    )
    ap.add_argument("--fetch-batch", type=int, default=200, help="Batch size for EFetch (PMIDs per request)")
    ap.add_argument("--max", type=int, default=0, help="Optional cap on total PMIDs processed (0 = no cap)")
    ap.add_argument(
        "--edirect",
        choices=["auto", "on", "off"],
        default="off",
        help="Use EDirect if available. auto=prefer, on=require, off=disable (default: off for stability).",
    )
    ap.add_argument(
        "--edirect-prefix",
        default="",
        help="Command prefix or EDirect directory (default: bundled ./edirect or sibling ../igather2/edirect when present).",
    )
    ap.add_argument(
        "--max-tries",
        type=int,
        default=None,
        help="Override Biopython Entrez.max_tries (retries for transient errors).",
    )
    ap.add_argument(
        "--sleep-between-tries",
        type=float,
        default=None,
        help="Override Biopython Entrez.sleep_between_tries (seconds).",
    )
    ap.add_argument(
        "--fetch-retries",
        type=int,
        default=3,
        help="Number of retries for failed EFetch requests (default: 3).",
    )
    ap.add_argument(
        "--start-from",
        type=int,
        default=0,
        help="Start fetching from this offset (useful for resuming after errors).",
    )
    args = ap.parse_args()

    load_dotenv()
    cfg = get_ncbi_config()
    configure_entrez(cfg, max_tries=args.max_tries, sleep_between_tries=args.sleep_between_tries)

    conn = init_db(args.db)

    count, webenv, query_key = esearch_last_24h(cfg, args.query)

    print(f"[info] Query: {args.query}")
    print(f"[info] NCBI email/tool: {cfg.email} / {cfg.tool}")
    print(f"[info] Found {count} PubMed records (reldate=1, datetype=edat).")
    print(f"[info] WebEnv/query_key obtained (history enabled).")

    to_process = count
    if args.max and args.max > 0:
        to_process = min(to_process, args.max)
        print(f"[info] Applying cap --max={args.max}; will process up to {to_process} PMIDs.")

    prefix = resolve_edirect_prefix(args.edirect_prefix or os.getenv("EDIRECT_PREFIX", ""))
    edirect_ok = edirect_available(prefix)
    if args.edirect == "on" and not edirect_ok:
        print("[error] EDirect requested but not found. Set PATH or use --edirect-prefix.", file=sys.stderr)
        conn.close()
        return 1
    use_edirect = args.edirect != "off" and edirect_ok

    total_seen = 0
    total_new = 0
    total_inserted = 0

    if to_process == 0:
        print(
            f"[done] total_seen={total_seen} total_new={total_new} total_inserted={total_inserted} "
            f"db={args.db} at={utc_now_iso()}"
        )
        conn.close()
        return 0

    if use_edirect:
        print("[info] Using EDirect to retrieve PMID list, then fetching records in batches.")
        try:
            ids = edirect_pubmed_ids(args.query, prefix)
        except Exception as e:
            print(f"[error] Error during EDirect PMID retrieval: {e}", file=sys.stderr)
            conn.close()
            return 1
        if len(ids) != count:
            print(f"[warn] EDirect returned {len(ids)} PMIDs but Entrez count was {count}.", file=sys.stderr)
        to_process = min(to_process, len(ids))
        retstart = args.start_from
        if retstart > 0:
            print(f"[info] Resuming from offset {retstart} (--start-from={retstart})")

        while retstart < to_process:
            this_batch = min(args.fetch_batch, to_process - retstart)
            pmid_batch = ids[retstart : retstart + this_batch]
            records = efetch_pubmed_by_ids(pmid_batch, max_retries=args.fetch_retries)
            retstart += this_batch

            pmids = [str(r["pmid"]) for r in records if r.get("pmid")]
            total_seen += len(pmids)
            already = existing_pmids(conn, pmids)
            new_records = [r for r in records if r.get("pmid") and r["pmid"] not in already]
            total_new += len(new_records)

            print(
                f"[page] retstart={retstart - this_batch} fetched={len(pmids)} "
                f"new={len(new_records)} total_seen={retstart}/{to_process}"
            )
            print(f"[progress] To resume from this point if interrupted, use: --start-from {retstart}", file=sys.stderr)
            if not new_records:
                continue

            inserted = insert_articles(conn, new_records)
            total_inserted += inserted
            print(f"[insert] parsed={len(records)} inserted={inserted}")

        print(
            f"[done] total_seen={total_seen} total_new={total_new} total_inserted={total_inserted} "
            f"db={args.db} at={utc_now_iso()}"
        )

        conn.close()
        return 0

    esearch_retstart_limit = 9998
    esearch_max_records = 9999
    use_history = True
    if count > esearch_max_records:
        print(
            f"[info] Found {count} records. Using History server; "
            f"ESearch ID-list mode is capped at {esearch_max_records} records."
        )

    # Page through the stable history snapshot via WebEnv/query_key when safe.
    retstart = args.start_from
    if retstart > 0:
        print(f"[info] Resuming from offset {retstart} (--start-from={retstart})")
    while retstart < to_process:
        this_batch = min(args.fetch_batch, to_process - retstart)
        if use_history:
            try:
                records = efetch_pubmed_batch(webenv, query_key, retstart, this_batch, max_retries=args.fetch_retries)
            except HTTPError as e:
                if e.code != 400:
                    raise
                print(
                    f"[warn] EFetch history returned HTTP 400 at retstart={retstart}. "
                    f"Refreshing ESearch history and retrying."
                )
                count, webenv, query_key = esearch_last_24h(cfg, args.query)
                if args.max and args.max > 0:
                    to_process = min(args.max, count)
                else:
                    to_process = count
                if retstart >= to_process:
                    print(f"[warn] retstart={retstart} >= total={to_process}; stopping early.")
                    break
                try:
                    records = efetch_pubmed_batch(webenv, query_key, retstart, this_batch, max_retries=args.fetch_retries)
                except HTTPError as e2:
                    if e2.code != 400:
                        raise
                    if count > esearch_max_records:
                        print(
                            f"[error] EFetch history failed and ESearch ID-list mode cannot fetch beyond "
                            f"{esearch_max_records} records. Consider narrowing the query or using EDirect.",
                            file=sys.stderr,
                        )
                        conn.close()
                        return 1
                    print(
                        f"[warn] EFetch history still failing with HTTP 400 at retstart={retstart}. "
                        f"Switching to ID-list mode."
                    )
                    use_history = False
                    ids = esearch_ids(args.query, retstart, this_batch)
                    if not ids:
                        print(f"[warn] ESearch returned no IDs at retstart={retstart}; stopping early.")
                        break
                    records = efetch_pubmed_by_ids(ids, max_retries=args.fetch_retries)
        else:
            if retstart > esearch_retstart_limit:
                print(
                    f"[error] ESearch retstart limit exceeded ({esearch_retstart_limit}). "
                    f"Consider narrowing the query or using EDirect.",
                    file=sys.stderr,
                )
                break
            ids = esearch_ids(args.query, retstart, this_batch)
            if not ids:
                print(f"[warn] ESearch returned no IDs at retstart={retstart}; stopping early.")
                break
            records = efetch_pubmed_by_ids(ids, max_retries=args.fetch_retries)

        retstart += this_batch

        pmids = [str(r["pmid"]) for r in records if r.get("pmid")]
        total_seen += len(pmids)

        # Dedup against DB
        already = existing_pmids(conn, pmids)
        new_records = [r for r in records if r.get("pmid") and r["pmid"] not in already]
        total_new += len(new_records)

        print(
            f"[page] retstart={retstart - this_batch} fetched={len(pmids)} "
            f"new={len(new_records)} total_seen={total_seen}/{to_process}"
        )
        print(f"[progress] To resume from this point if interrupted, use: --start-from {retstart}", file=sys.stderr)

        if not new_records:
            continue

        inserted = insert_articles(conn, new_records)
        total_inserted += inserted
        print(f"[insert] parsed={len(records)} inserted={inserted}")

    print(
        f"[done] total_seen={total_seen} total_new={total_new} total_inserted={total_inserted} "
        f"db={args.db} at={utc_now_iso()}"
    )

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
