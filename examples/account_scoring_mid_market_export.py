from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from sigma_api_toolkit.client import (
    MAX_RESULTS_VALIDITY_TIME_MS,
    SigmaAPIClient,
    SigmaAPIError,
    _strip_first_line,
    count_csv_data_records,
    read_last_csv_data_records,
)
from sigma_api_toolkit.config import SigmaConfig
from sigma_api_toolkit.service import (
    inspect_workbook,
    pick_elements_for_export,
    resolve_workbook_node_selection,
)
from sigma_api_toolkit.utils import parse_workbook_locator


SOURCE_URL = (
    "https://app.sigmacomputing.com/flock-safety/workbook/"
    "Account-Scoring-Query-5J9dDvF9eJ2BVBFkWxBI5f?:nodeId=BHnQm4BePW"
)
DEFAULT_OUTPUT = Path("exports/account-scoring-query__mid-market.csv")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reference export for the Account Scoring Query / Mid Market Sigma tab."
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to a dotenv file with SIGMA_API_URL, SIGMA_CLIENT_ID, SIGMA_CLIENT_SECRET",
    )
    parser.add_argument(
        "--output-file",
        default=str(DEFAULT_OUTPUT),
        help=f"Output CSV path. Default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=500_000,
        help="Rows per Sigma export chunk. Default: 500000",
    )
    parser.add_argument(
        "--chunk-overlap-rows",
        type=int,
        default=1_000,
        help="Rows of CSV overlap to validate between chunk requests. Default: 1000",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=600.0,
        help="Per-request HTTP timeout in seconds. Default: 600",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=5.0,
        help="Seconds between download polls. Default: 5",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=3600.0,
        help="Max wait per chunk before failing. Default: 3600",
    )
    parser.add_argument(
        "--results-validity-time-ms",
        type=int,
        default=MAX_RESULTS_VALIDITY_TIME_MS,
        help=f"How long Sigma keeps each chunk downloadable. Default: {MAX_RESULTS_VALIDITY_TIME_MS}",
    )
    parser.add_argument(
        "--resume-offset",
        type=int,
        default=None,
        help="Resume the export at a Sigma row offset such as 11500001.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace any existing output/log/summary files.",
    )
    parser.add_argument(
        "--expected-rows",
        type=int,
        default=None,
        help=(
            "If set, count data rows in the completed CSV and fail if the count "
            "does not match. Useful as a post-export sanity check against a "
            "known row count from Sigma."
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    output_path = Path(args.output_file)
    log_path = output_path.with_suffix(output_path.suffix + ".log.txt")
    summary_path = output_path.with_suffix(output_path.suffix + ".summary.json")

    if args.resume_offset is None and not args.overwrite:
        existing = [path for path in (output_path, log_path, summary_path) if path.exists()]
        if existing:
            joined = ", ".join(str(path) for path in existing)
            raise FileExistsError(f"Existing files found: {joined}. Re-run with --overwrite.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if args.resume_offset is None:
        for path in (output_path, log_path, summary_path):
            if path.exists():
                path.unlink()

    start_iso = utc_now()
    start_dt = datetime.now(timezone.utc)
    chunk_stats = []
    total_bytes = 0
    chunk_count = 0

    if args.resume_offset is not None:
        if not output_path.exists():
            raise FileNotFoundError(
                f"Cannot resume because output file does not exist: {output_path}"
            )
        if summary_path.exists():
            with summary_path.open("r", encoding="utf-8") as handle:
                existing_summary = json.load(handle)
            start_iso = existing_summary.get("start_utc", start_iso)
            start_dt = datetime.fromisoformat(start_iso)
            chunk_stats = existing_summary.get("chunk_stats", [])
            total_bytes = existing_summary.get("total_bytes", output_path.stat().st_size)
            chunk_count = existing_summary.get("chunk_count", len(chunk_stats))
        else:
            total_bytes = output_path.stat().st_size

    def log(message: str) -> None:
        line = f"{utc_now()} {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    config = SigmaConfig.from_env(args.env_file)
    config = SigmaConfig(
        base_url=config.base_url,
        client_id=config.client_id,
        client_secret=config.client_secret,
        request_timeout_seconds=args.request_timeout_seconds,
    )
    client = SigmaAPIClient(config)
    workbook_id, node_id = parse_workbook_locator(SOURCE_URL)
    resolved = resolve_workbook_node_selection(client, workbook_id, node_id)
    page_id = resolved["page_id"]
    element_id = resolved["element_id"]

    log("starting reference export")
    log(f"source_url={SOURCE_URL}")
    log(f"output_path={output_path}")
    log(
        f"workbook_id={workbook_id} node_id={node_id} page_id={page_id} "
        f"element_id={element_id} resume_offset={args.resume_offset} "
        f"chunk_overlap_rows={args.chunk_overlap_rows} "
        f"results_validity_time_ms={args.results_validity_time_ms}"
    )

    snapshot = inspect_workbook(client, workbook_id, page_id=page_id)
    selected = pick_elements_for_export(
        snapshot["elements"],
        element_id=element_id,
    )
    if len(selected) != 1:
        raise ValueError(f"Expected exactly one exportable element for {SOURCE_URL}, got {len(selected)}")
    element = selected[0]
    element_id = str(element["elementId"])
    log(f"resolved_element_id={element_id} element_name={element.get('name')}")

    resume_tail_records: list[bytes] = []
    adjusted_start_offset = args.resume_offset
    if args.resume_offset is not None:
        log(f"resume requested at sigma_offset={args.resume_offset}, reading last "
            f"{args.chunk_overlap_rows} data records from existing file for continuity check")
        resume_tail_records = read_last_csv_data_records(output_path, args.chunk_overlap_rows)
        if not resume_tail_records:
            raise ValueError(
                f"Cannot validate resume continuity: existing file {output_path} has no data "
                "rows. Re-run without --resume-offset or supply a file that already contains "
                "the earlier chunks."
            )
        adjusted_start_offset = max(1, args.resume_offset - len(resume_tail_records))
        log(
            f"resume_continuity tail_records={len(resume_tail_records)} "
            f"adjusted_sigma_offset={adjusted_start_offset}"
        )

    try:
        for chunk in client.iter_export_chunks(
            workbook_id,
            format_type="csv",
            element_id=element_id,
            chunk_size=args.chunk_size,
            start_offset=adjusted_start_offset,
            poll_seconds=args.poll_seconds,
            timeout_seconds=args.timeout_seconds,
            results_validity_time_ms=args.results_validity_time_ms,
            csv_overlap_rows=args.chunk_overlap_rows,
            csv_resume_tail_records=resume_tail_records or None,
        ):
            chunk_count += 1
            is_first_write = chunk_count == 1 and args.resume_offset is None
            data = chunk if is_first_write else _strip_first_line(chunk)
            mode = "wb" if is_first_write else "ab"
            with output_path.open(mode) as handle:
                handle.write(data)
            total_bytes += len(data)
            record = {
                "chunk": chunk_count,
                "bytes_written": len(data),
                "newline_count": data.count(b"\n"),
                "total_bytes": total_bytes,
                "elapsed_seconds": round(
                    (datetime.now(timezone.utc) - start_dt).total_seconds(), 2
                ),
                "timestamp_utc": utc_now(),
            }
            chunk_stats.append(record)
            log("chunk_complete " + " ".join(f"{k}={v}" for k, v in record.items()))

        actual_row_count = count_csv_data_records(output_path)
        log(f"post_export_row_count rows={actual_row_count}")
        if args.expected_rows is not None and actual_row_count != args.expected_rows:
            raise SigmaAPIError(
                f"Row count mismatch: expected {args.expected_rows} data rows but the "
                f"completed CSV contains {actual_row_count}. Re-run the Sigma source "
                "count, confirm ORDER BY is deterministic, or switch to send-export."
            )

        summary = {
            "status": "completed",
            "source_url": SOURCE_URL,
            "workbook_id": workbook_id,
            "node_id": node_id,
            "page_id": page_id,
            "element_id": element_id,
            "output_path": str(output_path),
            "start_utc": start_iso,
            "end_utc": utc_now(),
            "duration_seconds": round(
                (datetime.now(timezone.utc) - start_dt).total_seconds(), 2
            ),
            "chunk_overlap_rows": args.chunk_overlap_rows,
            "results_validity_time_ms": args.results_validity_time_ms,
            "chunk_count": chunk_count,
            "total_bytes": total_bytes,
            "row_count": actual_row_count,
            "expected_rows": args.expected_rows,
            "chunk_stats": chunk_stats,
        }
    except Exception as exc:
        summary = {
            "status": "failed",
            "source_url": SOURCE_URL,
            "workbook_id": workbook_id,
            "node_id": node_id,
            "page_id": page_id,
            "element_id": element_id,
            "output_path": str(output_path),
            "start_utc": start_iso,
            "end_utc": utc_now(),
            "duration_seconds": round(
                (datetime.now(timezone.utc) - start_dt).total_seconds(), 2
            ),
            "chunk_overlap_rows": args.chunk_overlap_rows,
            "results_validity_time_ms": args.results_validity_time_ms,
            "chunk_count": chunk_count,
            "total_bytes": total_bytes,
            "chunk_stats": chunk_stats,
            "error": repr(exc),
        }
        with summary_path.open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)
        log(f"reference export failed error={exc!r}")
        log(f"summary_path={summary_path}")
        raise

    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
    log("reference export completed successfully")
    log(f"summary_path={summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
