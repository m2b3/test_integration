"""
Run the complete daily scientific-paper pipeline.

Examples:
  python pipeline.py
  python pipeline.py --sources arxiv pubmed medrxiv
  python pipeline.py --fail-fast
  python pipeline.py --dry-run
  python pipeline.py --skip-index

OpenReview is intentionally excluded. Fetching is delegated to the existing
source scripts, while merging, SPECTER embedding, FAISS, metadata, manifest,
and FTS5 creation are delegated to All_embedding.py.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO


SQLITE_SIDECAR_SUFFIXES = ("", "-wal", "-shm", "-journal")
COMBINED_ARTIFACT_NAMES = (
    "all.sqlite",
    "all_specter.index",
    "all_metadata.json",
    "all_manifest.json",
)


@dataclass(frozen=True)
class SourceConfig:
    name: str
    script_name: str
    database_name: str
    command_args: tuple[str, ...]
    expected_tables: tuple[str, ...]

    def command(self, project_dir: Path) -> list[str]:
        return [
            sys.executable,
            str(project_dir / self.script_name),
            *self.command_args,
        ]


@dataclass
class CommandResult:
    command: list[str]
    exit_code: int
    elapsed_seconds: float
    error: str | None = None


@dataclass
class SourceRunResult:
    source: str
    command: list[str]
    database_path: str
    status: str = "pending"
    detected_table: str | None = None
    row_count: int | None = None
    fetch_elapsed_seconds: float = 0.0
    exit_code: int | None = None
    validation_error: str | None = None
    warning: str | None = None


@dataclass
class CombinedBuildResult:
    status: str = "not_run"
    elapsed_seconds: float = 0.0
    merged_row_count: int | None = None
    indexed_count: int | None = None
    skipped_count: int | None = None
    source_counts: dict[str, int] = field(default_factory=dict)
    artifact_paths: list[str] = field(default_factory=list)
    error: str | None = None


class PipelineLogger:
    def __init__(self, log_file: TextIO):
        self.log_file = log_file

    def log(self, message: str = "") -> None:
        print(message, flush=True)
        self.log_file.write(message + "\n")
        self.log_file.flush()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def format_seconds(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {sec:.1f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h {int(minutes)}m {sec:.1f}s"


def build_source_registry() -> dict[str, SourceConfig]:
    return {
        "arxiv": SourceConfig(
            name="arxiv",
            script_name="arxiv.py",
            database_name="arxiv.sqlite",
            command_args=("--db", "arxiv.sqlite", "--max-retries", "6"),
            expected_tables=("arxiv_articles",),
        ),
        "pubmed": SourceConfig(
            name="pubmed",
            script_name="base.py",
            database_name="pubmed.sqlite",
            command_args=("--db", "pubmed.sqlite", "--edirect", "auto"),
            expected_tables=("pubmed_articles",),
        ),
        "biorxiv": SourceConfig(
            name="biorxiv",
            script_name="biorxiv.py",
            database_name="biorxiv.sqlite",
            command_args=("--db", "biorxiv.sqlite", "--server", "biorxiv"),
            expected_tables=("biorxiv_articles",),
        ),
        "medrxiv": SourceConfig(
            name="medrxiv",
            script_name="medrxiv.py",
            database_name="medrxiv.sqlite",
            command_args=("--db", "medrxiv.sqlite"),
            expected_tables=("medrxiv_articles",),
        ),
        "psyarxiv": SourceConfig(
            name="psyarxiv",
            script_name="psyarxiv.py",
            database_name="psyarxiv.sqlite",
            command_args=("--db", "psyarxiv.sqlite"),
            expected_tables=("papers",),
        ),
        "socarxiv": SourceConfig(
            name="socarxiv",
            script_name="socarxiv.py",
            database_name="socarxiv.sqlite",
            command_args=("--db", "socarxiv.sqlite"),
            expected_tables=("papers",),
        ),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    registry = build_source_registry()
    parser = argparse.ArgumentParser(description="Run the complete daily paper pipeline.")
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=list(registry),
        default=list(registry),
        help="Daily sources to fetch (default: all six supported sources).",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop immediately after the first source fetch or validation failure.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print operations without deleting files or running subprocesses.",
    )
    parser.add_argument(
        "--skip-index",
        action="store_true",
        help="Run clean, fetch, and validation without rebuilding combined artifacts.",
    )
    args = parser.parse_args(argv)
    args.sources = list(dict.fromkeys(args.sources))
    return args


def sqlite_paths(database_path: Path) -> list[Path]:
    return [Path(str(database_path) + suffix) for suffix in SQLITE_SIDECAR_SUFFIXES]


def remove_sqlite_with_sidecars(
    database_path: Path,
    *,
    dry_run: bool,
    logger: PipelineLogger,
) -> None:
    for path in sqlite_paths(database_path):
        if path.exists():
            logger.log(f"[clean] {'would remove' if dry_run else 'removing'} {path.name}")
            if not dry_run:
                path.unlink()
        else:
            logger.log(f"[clean] absent {path.name}")


def run_command(
    command: list[str],
    cwd: Path,
    log_file: TextIO,
) -> CommandResult:
    start = time.perf_counter()
    script_path = Path(command[1]) if len(command) > 1 else None
    if script_path is not None and script_path.suffix == ".py" and not script_path.exists():
        return CommandResult(
            command=command,
            exit_code=1,
            elapsed_seconds=time.perf_counter() - start,
            error=f"Script file does not exist: {script_path}",
        )

    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log_file.write(line)
            log_file.flush()
        exit_code = process.wait()
        elapsed = time.perf_counter() - start
        return CommandResult(
            command=command,
            exit_code=exit_code,
            elapsed_seconds=elapsed,
            error=None if exit_code == 0 else f"Command exited with code {exit_code}",
        )
    except KeyboardInterrupt:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise
    except OSError as exc:
        return CommandResult(
            command=command,
            exit_code=1,
            elapsed_seconds=time.perf_counter() - start,
            error=f"Could not start command: {exc}",
        )


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    return {str(row[1]) for row in rows}


def _detect_supported_table(
    conn: sqlite3.Connection,
    config: SourceConfig,
) -> str:
    table_rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = ?",
        ("table",),
    ).fetchall()
    tables = {str(row[0]) for row in table_rows}

    for table_name in config.expected_tables:
        if table_name not in tables:
            continue
        if table_name == "papers":
            columns = _table_columns(conn, table_name)
            unified_columns = {"source", "external_id", "published_date"}
            openreview_columns = {"id", "forum", "classification", "raw_content"}
            if openreview_columns.issubset(columns) and not unified_columns.issubset(columns):
                raise RuntimeError("OpenReview-style papers table is excluded from the daily pipeline.")
            if not unified_columns.issubset(columns):
                raise RuntimeError(
                    "The papers table is not a supported unified scientific-paper schema."
                )
        return table_name

    expected = ", ".join(config.expected_tables)
    raise RuntimeError(f"Expected supported table not found (expected: {expected}).")


def validate_source_database(
    config: SourceConfig,
    database_path: Path,
) -> tuple[str, int, str | None]:
    if not database_path.exists():
        raise RuntimeError(f"Expected database was not created: {database_path.name}")
    if not database_path.is_file():
        raise RuntimeError(f"Expected database path is not a file: {database_path}")

    try:
        conn = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
        try:
            integrity_rows = conn.execute("PRAGMA integrity_check").fetchall()
            integrity_messages = [str(row[0]) for row in integrity_rows]
            if integrity_messages != ["ok"]:
                raise RuntimeError(
                    "SQLite integrity_check failed: " + "; ".join(integrity_messages)
                )
            table_name = _detect_supported_table(conn, config)
            row = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()
            row_count = int(row[0]) if row else 0
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise RuntimeError(f"Invalid SQLite database: {exc}") from exc

    warning = None
    if row_count == 0:
        warning = f"{database_path.name} is valid but contains 0 rows"
    return table_name, row_count, warning


def run_source(
    config: SourceConfig,
    project_dir: Path,
    *,
    dry_run: bool,
    logger: PipelineLogger,
    log_file: TextIO,
) -> SourceRunResult:
    command = config.command(project_dir)
    database_path = project_dir / config.database_name
    result = SourceRunResult(
        source=config.name,
        command=command,
        database_path=str(database_path),
    )
    logger.log(f"[source] {config.name}")
    logger.log("[run] " + " ".join(command))

    if dry_run:
        result.status = "dry_run"
        return result

    command_result = run_command(command, project_dir, log_file)
    result.fetch_elapsed_seconds = command_result.elapsed_seconds
    result.exit_code = command_result.exit_code
    if command_result.exit_code != 0:
        result.status = "failed"
        result.validation_error = command_result.error
        logger.log(f"[error] {config.name}: {command_result.error}")
        remove_sqlite_with_sidecars(database_path, dry_run=False, logger=logger)
    else:
        result.status = "fetched"
    return result


def validate_source_result(
    result: SourceRunResult,
    config: SourceConfig,
    *,
    dry_run: bool,
    logger: PipelineLogger,
) -> None:
    database_path = Path(result.database_path)
    logger.log(f"[validate] {database_path.name}")
    if dry_run:
        logger.log(f"[validate] would validate expected table(s): {', '.join(config.expected_tables)}")
        return
    try:
        table_name, row_count, warning = validate_source_database(config, database_path)
        result.status = "success"
        result.detected_table = table_name
        result.row_count = row_count
        result.warning = warning
        logger.log(f"[validate] table={table_name} rows={row_count} status=ok")
        if warning:
            logger.log(f"[warn] {warning}")
    except RuntimeError as exc:
        result.status = "failed"
        result.validation_error = str(exc)
        logger.log(f"[error] {config.name} validation failed: {exc}")
        remove_sqlite_with_sidecars(database_path, dry_run=False, logger=logger)


def copy_successful_databases_to_staging(
    successful_results: list[SourceRunResult],
    staging_dir: Path,
    *,
    dry_run: bool,
    logger: PipelineLogger,
) -> None:
    for result in successful_results:
        source_path = Path(result.database_path)
        destination = staging_dir / source_path.name
        logger.log(
            f"[stage] {'would copy' if dry_run else 'copying'} "
            f"{source_path.name} -> {destination}"
        )
        if not dry_run:
            shutil.copy2(source_path, destination)


def _load_json(path: Path, expected_type: type[Any]) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid JSON artifact `{path.name}`: {exc}") from exc
    if not isinstance(value, expected_type):
        raise RuntimeError(
            f"Invalid JSON artifact `{path.name}`: expected {expected_type.__name__}."
        )
    return value


def validate_combined_outputs(staging_dir: Path) -> CombinedBuildResult:
    paths = {name: staging_dir / name for name in COMBINED_ARTIFACT_NAMES}
    for name, path in paths.items():
        if not path.exists():
            raise RuntimeError(f"Missing combined output artifact: {name}")
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError(f"Combined output artifact is empty: {name}")

    sqlite_path = paths["all.sqlite"]
    try:
        conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
        try:
            integrity = [str(row[0]) for row in conn.execute("PRAGMA integrity_check").fetchall()]
            if integrity != ["ok"]:
                raise RuntimeError("Merged SQLite integrity_check failed: " + "; ".join(integrity))
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = ?",
                    ("table",),
                ).fetchall()
            }
            if "papers" not in tables:
                raise RuntimeError("Merged all.sqlite does not contain the papers table.")
            if "papers_fts" not in tables:
                raise RuntimeError("Merged all.sqlite does not contain the papers_fts FTS5 table.")
            merged_row = conn.execute("SELECT COUNT(*) FROM papers").fetchone()
            merged_row_count = int(merged_row[0]) if merged_row else 0
            source_counts = {
                str(source): int(count)
                for source, count in conn.execute(
                    "SELECT source, COUNT(*) FROM papers GROUP BY source ORDER BY source"
                ).fetchall()
            }
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise RuntimeError(f"Invalid merged all.sqlite: {exc}") from exc

    if "openreview" in {source.casefold() for source in source_counts}:
        raise RuntimeError("OpenReview was found in merged all.sqlite; refusing replacement.")

    metadata = _load_json(paths["all_metadata.json"], list)
    manifest = _load_json(paths["all_manifest.json"], dict)
    manifest_indexed = manifest.get("num_indexed_papers")
    if manifest_indexed is not None and int(manifest_indexed) != len(metadata):
        raise RuntimeError(
            "Manifest/metadata count mismatch: "
            f"manifest={manifest_indexed}, metadata={len(metadata)}."
        )

    try:
        import faiss
    except ImportError:
        faiss = None
    if faiss is not None:
        index = faiss.read_index(str(paths["all_specter.index"]))
        if int(index.ntotal) != len(metadata):
            raise RuntimeError(
                f"FAISS/metadata count mismatch: index={index.ntotal}, metadata={len(metadata)}."
            )

    return CombinedBuildResult(
        status="validated",
        merged_row_count=merged_row_count,
        indexed_count=len(metadata),
        skipped_count=(
            int(manifest["num_skipped_papers"])
            if manifest.get("num_skipped_papers") is not None
            else None
        ),
        source_counts=source_counts,
        artifact_paths=[str(paths[name]) for name in COMBINED_ARTIFACT_NAMES],
    )


def atomically_replace_outputs(
    staging_dir: Path,
    project_dir: Path,
    run_id: str,
) -> list[str]:
    new_paths: dict[str, Path] = {}
    backup_paths: dict[str, Path] = {}
    replaced_names: list[str] = []

    try:
        for name in COMBINED_ARTIFACT_NAMES:
            source = staging_dir / name
            temporary = project_dir / f"{name}.new"
            if temporary.exists():
                temporary.unlink()
            shutil.copy2(source, temporary)
            if temporary.stat().st_size <= 0:
                raise RuntimeError(f"Temporary replacement artifact is empty: {temporary.name}")
            new_paths[name] = temporary

        for name in COMBINED_ARTIFACT_NAMES:
            target = project_dir / name
            if target.exists():
                backup = project_dir / f".{name}.pipeline-backup-{run_id}"
                if backup.exists():
                    backup.unlink()
                os.replace(target, backup)
                backup_paths[name] = backup
            os.replace(new_paths[name], target)
            replaced_names.append(name)
    except Exception as exc:
        for name in reversed(replaced_names):
            target = project_dir / name
            if target.exists():
                target.unlink()
            backup = backup_paths.get(name)
            if backup is not None and backup.exists():
                os.replace(backup, target)
        for name, backup in backup_paths.items():
            target = project_dir / name
            if backup.exists() and not target.exists():
                os.replace(backup, target)
        for path in new_paths.values():
            if path.exists():
                path.unlink()
        raise RuntimeError(f"Atomic artifact replacement failed: {exc}") from exc

    for backup in backup_paths.values():
        if backup.exists():
            backup.unlink()
    return [str(project_dir / name) for name in COMBINED_ARTIFACT_NAMES]


def run_merge_and_index(
    successful_results: list[SourceRunResult],
    project_dir: Path,
    run_id: str,
    *,
    dry_run: bool,
    logger: PipelineLogger,
    log_file: TextIO,
) -> CombinedBuildResult:
    if not successful_results:
        return CombinedBuildResult(
            status="failed",
            error="No successful validated source databases; merge/index was not run.",
        )

    all_embedding_path = project_dir / "All_embedding.py"
    if not all_embedding_path.exists():
        return CombinedBuildResult(
            status="failed",
            error=f"Missing All_embedding.py: {all_embedding_path}",
        )

    command = [sys.executable, str(all_embedding_path), "all.sqlite"]
    if dry_run:
        logger.log(f"[info] Would create an isolated staging directory")
        for result in successful_results:
            logger.log(f"[stage] would copy {Path(result.database_path).name} to staging")
        logger.log(f"[info] Successful daily databases copied to staging: {len(successful_results)}")
        logger.log("[info] OpenReview excluded")
        logger.log("[run] " + " ".join(command))
        for name in COMBINED_ARTIFACT_NAMES:
            logger.log(f"[replace] would atomically replace {name} after validation")
        return CombinedBuildResult(status="dry_run")

    start = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="daily-paper-pipeline-") as temp_dir:
            staging_dir = Path(temp_dir)
            copy_successful_databases_to_staging(
                successful_results,
                staging_dir,
                dry_run=False,
                logger=logger,
            )
            logger.log(
                f"[info] Successful daily databases copied to staging: {len(successful_results)}"
            )
            logger.log("[info] OpenReview excluded")
            logger.log("[run] " + " ".join(command))
            command_result = run_command(command, staging_dir, log_file)
            if command_result.exit_code != 0:
                raise RuntimeError(command_result.error or "Merge/index command failed.")

            build_result = validate_combined_outputs(staging_dir)
            build_result.artifact_paths = atomically_replace_outputs(
                staging_dir,
                project_dir,
                run_id,
            )
            build_result.status = "success"
            build_result.elapsed_seconds = time.perf_counter() - start
            return build_result
    except RuntimeError as exc:
        return CombinedBuildResult(
            status="failed",
            elapsed_seconds=time.perf_counter() - start,
            error=str(exc),
        )


def write_json_report(report_path: Path, report: dict[str, Any]) -> None:
    temporary = Path(str(report_path) + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, report_path)


def print_final_report(
    *,
    logger: PipelineLogger,
    started_at: datetime,
    finished_at: datetime,
    elapsed_seconds: float,
    source_results: list[SourceRunResult],
    build_result: CombinedBuildResult,
    final_status: str,
) -> None:
    logger.log("")
    logger.log("=" * 50)
    logger.log("Daily Pipeline Summary")
    logger.log("=" * 50)
    logger.log(f"Started: {iso_utc(started_at)}")
    logger.log(f"Finished: {iso_utc(finished_at)}")
    logger.log(f"Total elapsed: {format_seconds(elapsed_seconds)}")
    logger.log("")
    logger.log("Sources:")
    for result in source_results:
        details = result.status
        if result.row_count is not None:
            details += f", {result.row_count} rows"
        if result.fetch_elapsed_seconds:
            details += f", {format_seconds(result.fetch_elapsed_seconds)}"
        if result.validation_error:
            details += f", {result.validation_error}"
        if result.warning:
            details += f", warning: {result.warning}"
        logger.log(f"- {result.source}: {details}")

    successful = [result for result in source_results if result.status == "success"]
    failed = [result for result in source_results if result.status == "failed"]
    logger.log("")
    logger.log(f"Successful sources: {len(successful)}")
    logger.log(f"Failed sources: {len(failed)}")
    logger.log(
        "Daily source rows before merge: "
        + str(sum(result.row_count or 0 for result in successful))
    )
    logger.log(f"Merged rows: {build_result.merged_row_count if build_result.merged_row_count is not None else 'N/A'}")
    logger.log(f"Indexed papers: {build_result.indexed_count if build_result.indexed_count is not None else 'N/A'}")
    logger.log(f"Skipped papers: {build_result.skipped_count if build_result.skipped_count is not None else 'N/A'}")
    if build_result.source_counts:
        logger.log("Merged source counts:")
        for source, count in build_result.source_counts.items():
            logger.log(f"- {source}: {count}")
    logger.log("")
    logger.log("OpenReview included: no")
    logger.log("")
    logger.log("Outputs:")
    if build_result.artifact_paths:
        for path in build_result.artifact_paths:
            logger.log(f"- {path}")
    else:
        logger.log("- none produced by this run")
    if build_result.error:
        logger.log(f"Build error: {build_result.error}")
    logger.log("")
    logger.log(f"Pipeline status: {final_status}")


def _final_status_and_code(
    source_results: list[SourceRunResult],
    build_result: CombinedBuildResult,
    *,
    dry_run: bool,
    skip_index: bool,
    fail_fast_stopped: bool,
) -> tuple[str, int]:
    if dry_run:
        return "dry run completed", 0
    failed_sources = any(result.status == "failed" for result in source_results)
    successful_sources = any(result.status == "success" for result in source_results)
    if fail_fast_stopped or not successful_sources:
        return "failed before indexing", 1
    if skip_index:
        if failed_sources:
            return "fetch/validation completed with source failures; indexing skipped", 2
        return "fetch/validation completed; indexing skipped", 0
    if build_result.status != "success":
        return "failed during merged-index build", 1
    if failed_sources:
        return "completed with source failures", 2
    return "success", 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_dir = Path(__file__).resolve().parent
    registry = build_source_registry()
    selected_configs = [registry[name] for name in args.sources]

    started_at = utc_now()
    start_perf = time.perf_counter()
    run_id = started_at.strftime("%Y%m%dT%H%M%SZ")
    logs_dir = project_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"pipeline_{run_id}.log"
    report_path = logs_dir / f"pipeline_report_{run_id}.json"

    source_results: list[SourceRunResult] = []
    build_result = CombinedBuildResult()
    fail_fast_stopped = False
    interrupted = False

    with log_path.open("w", encoding="utf-8") as log_file:
        logger = PipelineLogger(log_file)
        logger.log(f"[info] Run ID: {run_id}")
        logger.log(f"[info] Project directory: {project_dir}")
        logger.log(f"[info] Selected sources: {', '.join(args.sources)}")
        logger.log("[info] OpenReview is excluded from the daily pipeline.")

        try:
            logger.log("")
            logger.log("[stage 1/5] Cleaning daily source databases")
            for config in selected_configs:
                remove_sqlite_with_sidecars(
                    project_dir / config.database_name,
                    dry_run=args.dry_run,
                    logger=logger,
                )

            logger.log("")
            logger.log("[stage 2/5] Fetching papers")
            for config in selected_configs:
                result = run_source(
                    config,
                    project_dir,
                    dry_run=args.dry_run,
                    logger=logger,
                    log_file=log_file,
                )
                source_results.append(result)
                if result.status == "failed" and args.fail_fast:
                    fail_fast_stopped = True
                    logger.log("[error] --fail-fast active; stopping after source fetch failure.")
                    break

            logger.log("")
            logger.log("[stage 3/5] Validating source databases")
            if not fail_fast_stopped:
                for result in source_results:
                    if result.status not in {"fetched", "dry_run"}:
                        continue
                    config = registry[result.source]
                    validate_source_result(
                        result,
                        config,
                        dry_run=args.dry_run,
                        logger=logger,
                    )
                    if result.status == "failed" and args.fail_fast:
                        fail_fast_stopped = True
                        logger.log("[error] --fail-fast active; stopping after validation failure.")
                        break
            else:
                logger.log("[info] Validation skipped because fail-fast stopped the run.")

            successful_results = [
                result for result in source_results if result.status == "success"
            ]
            if args.dry_run:
                successful_results = source_results

            logger.log("")
            logger.log("[stage 4/5] Merging and building search artifacts")
            if fail_fast_stopped:
                build_result = CombinedBuildResult(
                    status="not_run",
                    error="Merge/index skipped because --fail-fast stopped the pipeline.",
                )
            elif args.skip_index:
                build_result = CombinedBuildResult(status="skipped")
                logger.log("[info] --skip-index active; combined artifacts were not rebuilt.")
            else:
                build_result = run_merge_and_index(
                    successful_results,
                    project_dir,
                    run_id,
                    dry_run=args.dry_run,
                    logger=logger,
                    log_file=log_file,
                )
                if build_result.status == "failed":
                    logger.log(f"[error] {build_result.error}")
                    logger.log("[info] Previous combined artifacts were retained unchanged.")
        except KeyboardInterrupt:
            interrupted = True
            logger.log("")
            logger.log("[error] Pipeline interrupted. Previous combined artifacts were retained.")
        except Exception as exc:
            build_result = CombinedBuildResult(status="failed", error=str(exc))
            logger.log(f"[error] Unexpected pipeline failure: {exc}")
            log_file.write(traceback.format_exc())
            log_file.flush()

        finished_at = utc_now()
        total_elapsed = time.perf_counter() - start_perf
        if interrupted:
            final_status, exit_code = "interrupted", 130
        else:
            final_status, exit_code = _final_status_and_code(
                source_results,
                build_result,
                dry_run=args.dry_run,
                skip_index=args.skip_index,
                fail_fast_stopped=fail_fast_stopped,
            )

        logger.log("")
        logger.log("[stage 5/5] Final report")
        print_final_report(
            logger=logger,
            started_at=started_at,
            finished_at=finished_at,
            elapsed_seconds=total_elapsed,
            source_results=source_results,
            build_result=build_result,
            final_status=final_status,
        )

        report = {
            "run_id": run_id,
            "started_at": iso_utc(started_at),
            "finished_at": iso_utc(finished_at),
            "selected_sources": args.sources,
            "source_results": [asdict(result) for result in source_results],
            "successful_source_databases": [
                result.database_path
                for result in source_results
                if result.status == "success"
            ],
            "failed_sources": [
                result.source for result in source_results if result.status == "failed"
            ],
            "source_row_counts": {
                result.source: result.row_count
                for result in source_results
                if result.row_count is not None
            },
            "merged_row_count": build_result.merged_row_count,
            "indexed_count": build_result.indexed_count,
            "skipped_count": build_result.skipped_count,
            "merged_source_counts": build_result.source_counts,
            "artifact_paths": build_result.artifact_paths,
            "total_elapsed_seconds": total_elapsed,
            "final_status": final_status,
            "exit_code": exit_code,
            "log_file_path": str(log_path),
            "openreview_included": False,
            "dry_run": args.dry_run,
            "skip_index": args.skip_index,
        }
        try:
            write_json_report(report_path, report)
            logger.log(f"[info] JSON report: {report_path}")
        except OSError as exc:
            logger.log(f"[error] Could not write JSON report: {exc}")
            if exit_code == 0:
                exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
