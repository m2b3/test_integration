"""
Fetch visible OpenReview submissions for a specific venue into SQLite.
exp:
  python openreview.py "https://openreview.net/group?id=ICLR.cc/2026/Conference#tab-accept-oral"
  python openreview.py "ICLR.cc/2026/Conference"
  python openreview.py "ICLR.cc/2026/Conference" --invitation "ICLR.cc/2026/Conference/-/Blind_Submission"
"""

from __future__ import annotations
import argparse
import importlib
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from typing import Any
from urllib.parse import parse_qs, urlparse

DB_PATH = "openreview.sqlite"
OPENREVIEW_API2_URL = "https://api2.openreview.net"
CLASSIFICATIONS = ("accepted", "rejected", "desk_rejected", "withdrawn", "submitted", "unknown")
VENUE_CONTENT_FALLBACK_PREFIX = "content.venueid="

def parse_venue_id(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Missing venue id or OpenReview venue URL.")

    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        query = parse_qs(parsed.query)
        venue_values = query.get("id") or []
        venue_id = venue_values[0].strip() if venue_values else ""
        if not venue_id:
            raise ValueError("OpenReview URL is missing the required `id` query parameter.")
        return venue_id

    return raw


def import_openreview() -> Any:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    original_path = list(sys.path)
    original_module = sys.modules.get("openreview")
    if getattr(original_module, "__file__", None) == os.path.abspath(__file__):
        sys.modules.pop("openreview", None)
    try:
        sys.path = [
            path
            for path in sys.path
            if path not in ("", script_dir) and os.path.abspath(path or os.curdir) != script_dir
        ]
        openreview = importlib.import_module("openreview")
    except ImportError as exc:
        raise RuntimeError("Missing dependency: install with `pip install openreview-py`.") from exc
    finally:
        sys.path = original_path
        if original_module is not None and "openreview" not in sys.modules:
            sys.modules["openreview"] = original_module
    return openreview


def make_client() -> Any:
    openreview = import_openreview()
    api_module = getattr(openreview, "api", None)
    if api_module is not None and hasattr(api_module, "OpenReviewClient"):
        return api_module.OpenReviewClient(baseurl=OPENREVIEW_API2_URL)
    if hasattr(openreview, "OpenReviewClient"):
        return openreview.OpenReviewClient(baseurl=OPENREVIEW_API2_URL)
    raise RuntimeError(
        "Installed openreview-py does not expose OpenReviewClient. "
        "Upgrade with `pip install -U openreview-py`."
    )


def unwrap_content_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def content_field(content: dict[str, Any], key: str, default: Any = None) -> Any:
    if not isinstance(content, dict):
        return default
    value = unwrap_content_value(content.get(key, default))
    return default if value is None else value


def as_text(value: Any) -> str:
    value = unwrap_content_value(value)
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if str(item).strip())
    return str(value).strip()


def as_list(value: Any) -> list[str]:
    value = unwrap_content_value(value)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def note_id(note: Any) -> str:
    return str(getattr(note, "id", "") or "").strip()


def invitation_id(invitation: Any) -> str:
    if isinstance(invitation, str):
        return invitation
    return str(getattr(invitation, "id", "") or "").strip()


def note_invitation_ids(note: Any) -> list[str]:
    invitations = getattr(note, "invitations", None)
    if isinstance(invitations, list):
        return [str(invitation).strip() for invitation in invitations if str(invitation).strip()]
    invitation = getattr(note, "invitation", None)
    if invitation:
        return [str(invitation).strip()]
    return []


def get_invitation(client: Any, invitation: str) -> Any | None:
    try:
        return client.get_invitation(invitation)
    except Exception:
        return None


def get_all_invitations_with_kwargs(client: Any, **kwargs: Any) -> list[Any]:
    attempts = [
        kwargs,
        {key: value for key, value in kwargs.items() if key != "domain"},
        {key: value for key, value in kwargs.items() if key not in {"domain", "type"}},
    ]
    seen_attempts: set[tuple[tuple[str, str], ...]] = set()
    last_error: Exception | None = None
    for attempt in attempts:
        signature = tuple(sorted((key, str(value)) for key, value in attempt.items()))
        if signature in seen_attempts:
            continue
        seen_attempts.add(signature)
        try:
            invitations = client.get_all_invitations(**attempt)
            return list(invitations or [])
        except TypeError as exc:
            last_error = exc
            continue
        except Exception as exc:
            last_error = exc
            break

    if last_error is not None:
        print(f"[warn] Invitation search failed for args={kwargs!r}: {last_error}", file=sys.stderr)
    return []


def get_all_invitations(client: Any, regex: str, venue_id: str) -> list[Any]:
    try:
        invitations = client.get_all_invitations(regex=regex)
        return list(invitations or [])
    except TypeError:
        pass
    except Exception as exc:
        print(f"[warn] Invitation regex search failed for regex={regex!r}: {exc}", file=sys.stderr)

    pattern = re.compile(regex)
    invitation_ids: dict[str, Any] = {}
    prefixes = [
        f"{venue_id}/-/",
        f"{venue_id}/",
    ]
    for prefix in prefixes:
        invitations = get_all_invitations_with_kwargs(
            client,
            prefix=prefix,
            type="invitation",
            domain=venue_id,
        )
        for invitation in invitations:
            inv_id = invitation_id(invitation)
            if inv_id and pattern.search(inv_id):
                invitation_ids[inv_id] = invitation
    return list(invitation_ids.values())


def invitation_score(invitation: str) -> tuple[int, int, str]:
    lowered = invitation.lower()
    score = 0
    if "/-/submission" in lowered:
        score += 100
    if "/-/blind_submission" in lowered:
        score += 90
    if "submission" in lowered:
        score += 20
    if "blind" in lowered:
        score += 5
    if "revision" in lowered:
        score -= 20
    if "withdraw" in lowered:
        score -= 30
    return (score, -len(invitation), invitation)


def discover_submission_invitation(client: Any, venue_id: str) -> str:
    print("Discovering submission invitation...")

    candidates = [
        f"{venue_id}/-/Submission",
        f"{venue_id}/-/Blind_Submission",
        f"{venue_id}/Authors/-/Submission",
        f"{venue_id}/Authors/-/Blind_Submission",
    ]
    for candidate in candidates:
        if get_invitation(client, candidate) is not None:
            return candidate

    invitation_ids: set[str] = set()
    escaped_venue = re.escape(venue_id)
    regexes = [
        rf"{escaped_venue}/-/.*Submission.*",
        rf"{escaped_venue}/-/.*Blind_Submission.*",
        rf".*{escaped_venue}.*/-/.*Submission.*",
        rf".*{escaped_venue}.*/-/.*Blind_Submission.*",
    ]
    for regex in regexes:
        for invitation in get_all_invitations(client, regex, venue_id):
            inv_id = invitation_id(invitation)
            if inv_id:
                invitation_ids.add(inv_id)

    if invitation_ids:
        return sorted(invitation_ids, key=invitation_score, reverse=True)[0]

    print("[warn] Could not discover invitations directly; falling back to visible notes by content.venueid.", file=sys.stderr)
    notes = fetch_notes_by_venueid(client, venue_id)
    inferred_invitation_ids: set[str] = set()
    for note in notes:
        for inv_id in note_invitation_ids(note):
            if "submission" in inv_id.casefold():
                inferred_invitation_ids.add(inv_id)
    if inferred_invitation_ids:
        return sorted(inferred_invitation_ids, key=invitation_score, reverse=True)[0]

    if notes:
        return f"{VENUE_CONTENT_FALLBACK_PREFIX}{venue_id}"

    raise RuntimeError(
        "Could not discover a submission invitation for this venue. "
        "The venue may be private, may use a nonstandard invitation, or may require legacy API 1/authenticated access."
    )


def fetch_notes_by_venueid(client: Any, venue_id: str) -> list[Any]:
    attempts = [
        {"content": {"venueid": venue_id}},
        {"content": {"venue_id": venue_id}},
        {"content": {"venue": venue_id}},
    ]
    last_error: Exception | None = None
    for kwargs in attempts:
        try:
            notes = client.get_all_notes(**kwargs)
            return list(notes or [])
        except TypeError as exc:
            last_error = exc
            continue
        except Exception as exc:
            last_error = exc
            continue
    if last_error is not None:
        print(f"[warn] Note fallback search by content.venueid failed: {last_error}", file=sys.stderr)
    return []


def fetch_visible_submissions(client: Any, invitation: str) -> list[Any]:
    if invitation.startswith(VENUE_CONTENT_FALLBACK_PREFIX):
        venue_id = invitation.removeprefix(VENUE_CONTENT_FALLBACK_PREFIX)
        return fetch_notes_by_venueid(client, venue_id)
    try:
        notes = client.get_all_notes(invitation=invitation)
    except Exception as exc:
        raise RuntimeError(f"OpenReview note fetch failed for invitation `{invitation}`: {exc}") from exc
    return list(notes or [])


def openreview_timestamp(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def pdf_url_from_note(note: Any, content: dict[str, Any]) -> str:
    pdf = as_text(content_field(content, "pdf"))
    if pdf.startswith("http://") or pdf.startswith("https://"):
        return pdf
    if pdf.startswith("/"):
        return f"https://openreview.net{pdf}"

    note_pdf = as_text(getattr(note, "pdf", None))
    if note_pdf.startswith("http://") or note_pdf.startswith("https://"):
        return note_pdf
    if note_pdf.startswith("/"):
        return f"https://openreview.net{note_pdf}"

    forum = str(getattr(note, "forum", "") or note_id(note)).strip()
    return f"https://openreview.net/pdf?id={forum}" if forum else ""


def normalize_note(note: Any, venue_id: str) -> dict[str, Any]:
    content = getattr(note, "content", {}) or {}
    if not isinstance(content, dict):
        content = {}

    record = {
        "source": "openreview",
        "id": note_id(note),
        "forum": str(getattr(note, "forum", "") or "").strip(),
        "number": content_field(content, "number"),
        "title": as_text(content_field(content, "title")),
        "authors": as_list(content_field(content, "authors")),
        "abstract": as_text(content_field(content, "abstract")),
        "pdf_url": pdf_url_from_note(note, content),
        "venue_id": venue_id,
        "venue": as_text(content_field(content, "venue")),
        "venueid": as_text(content_field(content, "venueid")),
        "decision": as_text(content_field(content, "decision")),
        "status": as_text(content_field(content, "status")),
        "presentation": as_text(content_field(content, "presentation")),
        "readers": as_list(getattr(note, "readers", [])),
        "cdate": openreview_timestamp(getattr(note, "cdate", None)),
        "mdate": openreview_timestamp(getattr(note, "mdate", None)),
        "classification": "unknown",
        "raw_content": content,
    }
    record["classification"] = classify_paper(record)
    return record


def classify_paper(record: dict[str, Any]) -> str:
    fields = [
        record.get("decision"),
        record.get("venue"),
        record.get("venueid"),
        record.get("status"),
        record.get("presentation"),
    ]
    text = " ".join(str(field or "") for field in fields).casefold()
    if "desk reject" in text or "desk-reject" in text or "desk_reject" in text:
        return "desk_rejected"
    if "withdraw" in text:
        return "withdrawn"
    if any(token in text for token in ("accept", "oral", "spotlight", "poster")):
        return "accepted"
    if "reject" in text:
        return "rejected"
    if any(str(field or "").strip() for field in fields):
        return "submitted"
    if record.get("id"):
        return "submitted"
    return "unknown"


def init_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS papers (
            id              TEXT PRIMARY KEY,
            source          TEXT,
            forum           TEXT,
            number          TEXT,
            title           TEXT,
            authors         TEXT,
            abstract        TEXT,
            pdf_url         TEXT,
            venue_id        TEXT,
            venue           TEXT,
            venueid         TEXT,
            decision        TEXT,
            status          TEXT,
            presentation    TEXT,
            readers         TEXT,
            cdate           INTEGER,
            mdate           INTEGER,
            classification  TEXT,
            raw_content     TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_openreview_papers_venue_id
        ON papers(venue_id);
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_openreview_papers_classification
        ON papers(classification);
        """
    )
    conn.commit()
    return conn


def save_papers(conn: sqlite3.Connection, papers: list[dict[str, Any]]) -> int:
    saved = 0
    cur = conn.cursor()
    for paper in papers:
        try:
            cur.execute(
                """
                INSERT OR REPLACE INTO papers
                (
                    id, source, forum, number, title, authors, abstract,
                    pdf_url, venue_id, venue, venueid, decision, status,
                    presentation, readers, cdate, mdate, classification,
                    raw_content
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    paper.get("id"),
                    paper.get("source"),
                    paper.get("forum"),
                    str(paper.get("number") or ""),
                    paper.get("title"),
                    json.dumps(paper.get("authors", []), ensure_ascii=False),
                    paper.get("abstract"),
                    paper.get("pdf_url"),
                    paper.get("venue_id"),
                    paper.get("venue"),
                    paper.get("venueid"),
                    paper.get("decision"),
                    paper.get("status"),
                    paper.get("presentation"),
                    json.dumps(paper.get("readers", []), ensure_ascii=False),
                    paper.get("cdate"),
                    paper.get("mdate"),
                    paper.get("classification"),
                    json.dumps(paper.get("raw_content", {}), ensure_ascii=False),
                ),
            )
            saved += 1
        except sqlite3.Error as exc:
            print(f"[warn] Failed to save OpenReview note id={paper.get('id')}: {exc}", file=sys.stderr)
    conn.commit()
    return saved


def print_classification_summary(records: list[dict[str, Any]]) -> None:
    counts = Counter(record.get("classification") or "unknown" for record in records)
    print("Classification summary:")
    for label in CLASSIFICATIONS:
        print(f"  {label}: {counts.get(label, 0)}")


def print_used_invitation(invitation: str) -> None:
    print(f"Invitation used: {invitation}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch publicly visible OpenReview submissions for one venue into openreview.sqlite. "
            "The actual final invitation used is printed after fetching submissions."
        )
    )
    parser.add_argument("venue", help="OpenReview venue URL or raw venue ID")
    parser.add_argument(
        "--invitation",
        help=(
            "Optional OpenReview submission invitation ID for advanced users who already know the exact "
            "submission invitation. If provided, skips automatic invitation discovery."
        ),
    )
    args = parser.parse_args()

    try:
        venue_id = parse_venue_id(args.venue)
        print(f"Parsed venue_id: {venue_id}")
        client = make_client()
        if args.invitation:
            invitation = args.invitation.strip()
            print(f"Using provided invitation: {invitation}")
        else:
            invitation = discover_submission_invitation(client, venue_id)
            print(f"Using discovered invitation: {invitation}")
        print("Fetching visible submissions...")
        notes = fetch_visible_submissions(client, invitation)
        print(f"Fetched {len(notes)} visible submissions.")
        print_used_invitation(invitation)

        if not notes:
            print(
                "[warn] No visible submissions were found. The venue may be private, may use a different invitation, "
                "or may require legacy API 1/authenticated access.",
                file=sys.stderr,
            )

        # OpenReview collection is venue-based. The API cannot reliably fetch all recent papers from the
        # whole OpenReview platform. Rejected papers are only available if the venue makes them publicly
        # readable or if the authenticated user has permission.
        papers = [normalize_note(note, venue_id) for note in notes]

        conn = init_db(DB_PATH)
        try:
            saved = save_papers(conn, papers)
        finally:
            conn.close()

        print(f"Saved {saved} papers to {DB_PATH}.")
        print_classification_summary(papers)
        return 0
    except (RuntimeError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("[error] Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

