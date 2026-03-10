from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from sigma_api_toolkit.client import SigmaAPIClient, _strip_first_line
from sigma_api_toolkit.config import SigmaConfig
from sigma_api_toolkit.service import inspect_workbook, pick_elements_for_export
from sigma_api_toolkit.utils import parse_workbook_locator


SOURCE_URL = (
    "https://app.sigmacomputing.com/flock-safety/workbook/"
    "Account-Scoring-Query-5J9dDvF9eJ2BVBFkWxBI5f?:nodeId=a78KJC6YSe"
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
        default=1_000_000,
        help="Rows per Sigma export chunk. Default: 1000000",
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
        "--overwrite",
        action="store_true",
        help="Replace any existing output/log/summary files.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    output_path = Path(args.output_file)
    log_path = output_path.with_suffix(output_path.suffix + ".log.txt")
    summary_path = output_path.with_suffix(output_path.suffix + ".summary.json")

    if not args.overwrite:
        existing = [path for path in (output_path, log_path, summary_path) if path.exists()]
        if existing:
            joined = ", ".join(str(path) for path in existing)
            raise FileExistsError(f"Existing files found: {joined}. Re-run with --overwrite.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    for path in (output_path, log_path, summary_path):
        if path.exists():
            path.unlink()

    start_epoch = time.time()
    start_iso = utc_now()

    def log(message: str) -> None:
        line = f"{utc_now()} {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    config = SigmaConfig.from_env(args.env_file)
    client = SigmaAPIClient(config)
    workbook_id, page_id = parse_workbook_locator(SOURCE_URL)

    log("starting reference export")
    log(f"source_url={SOURCE_URL}")
    log(f"output_path={output_path}")
    log(f"workbook_id={workbook_id} page_id={page_id}")

    snapshot = inspect_workbook(client, workbook_id, page_id=page_id)
    selected = pick_elements_for_export(snapshot["elements"])
    element = selected[0]
    element_id = str(element["elementId"])
    log(f"element_id={element_id} element_name={element.get('name')}")

    chunk_stats = []
    total_bytes = 0
    chunk_count = 0

    try:
        for chunk in client.iter_export_chunks(
            workbook_id,
            format_type="csv",
            element_id=element_id,
            chunk_size=args.chunk_size,
            poll_seconds=args.poll_seconds,
            timeout_seconds=args.timeout_seconds,
        ):
            chunk_count += 1
            data = chunk if chunk_count == 1 else _strip_first_line(chunk)
            mode = "wb" if chunk_count == 1 else "ab"
            with output_path.open(mode) as handle:
                handle.write(data)
            total_bytes += len(data)
            record = {
                "chunk": chunk_count,
                "bytes_written": len(data),
                "newline_count": data.count(b"\n"),
                "total_bytes": total_bytes,
                "elapsed_seconds": round(time.time() - start_epoch, 2),
                "timestamp_utc": utc_now(),
            }
            chunk_stats.append(record)
            log("chunk_complete " + " ".join(f"{k}={v}" for k, v in record.items()))

        summary = {
            "status": "completed",
            "source_url": SOURCE_URL,
            "workbook_id": workbook_id,
            "page_id": page_id,
            "element_id": element_id,
            "output_path": str(output_path),
            "start_utc": start_iso,
            "end_utc": utc_now(),
            "duration_seconds": round(time.time() - start_epoch, 2),
            "chunk_count": chunk_count,
            "total_bytes": total_bytes,
            "chunk_stats": chunk_stats,
        }
    except Exception as exc:
        summary = {
            "status": "failed",
            "source_url": SOURCE_URL,
            "workbook_id": workbook_id,
            "page_id": page_id,
            "element_id": element_id,
            "output_path": str(output_path),
            "start_utc": start_iso,
            "end_utc": utc_now(),
            "duration_seconds": round(time.time() - start_epoch, 2),
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

