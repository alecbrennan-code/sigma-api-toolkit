from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from sigma_api_toolkit.client import CHUNKABLE_FORMATS, MAX_EXPORT_ROWS, SigmaAPIClient, SigmaAPIError
from sigma_api_toolkit.config import SigmaConfig
from sigma_api_toolkit.service import (
    inspect_workbook,
    pick_elements_for_export,
    resolve_workbook_node_selection,
)
from sigma_api_toolkit.utils import (
    default_output_path,
    parse_workbook_locator,
    slugify,
    summarize_elements,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repeatable Sigma workbook export toolkit")
    parser.add_argument(
        "--env-file",
        default=None,
        help="Optional path to a dotenv file. Defaults to ./.env when present.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("test-auth", help="Validate Sigma API authentication")

    inspect_parser = subparsers.add_parser(
        "inspect-workbook",
        help="Inspect a workbook and list exportable elements",
    )
    inspect_parser.add_argument("--workbook", required=True, help="Workbook ID or Sigma workbook URL")
    inspect_parser.add_argument("--page-id")
    inspect_parser.add_argument("--include-columns", action="store_true")
    inspect_parser.add_argument("--tag-name")
    inspect_parser.add_argument("--bookmark-id")
    inspect_parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")

    export_parser = subparsers.add_parser(
        "export-data",
        help="Export one element, one page, or all exportable elements from a workbook",
    )
    export_parser.add_argument("--workbook", required=True, help="Workbook ID or Sigma workbook URL")
    export_parser.add_argument("--element-id")
    export_parser.add_argument("--element-name")
    export_parser.add_argument("--page-id")
    export_parser.add_argument("--all-elements", action="store_true")
    export_parser.add_argument("--format", default="csv", help="Export format. Default: csv")
    export_parser.add_argument("--output-file", help="Single output file path")
    export_parser.add_argument("--output-dir", default="exports", help="Output directory for generated files")
    export_parser.add_argument(
        "--chunk-size",
        type=int,
        default=250_000,
        help=f"Chunk size for CSV/JSON/XLSX exports. Max {MAX_EXPORT_ROWS}.",
    )
    export_parser.add_argument("--disable-chunking", action="store_true")
    export_parser.add_argument("--overwrite", action="store_true")
    export_parser.add_argument("--tag-name")
    export_parser.add_argument("--bookmark-id")
    export_parser.add_argument("--results-validity-time-ms", type=int, default=None)
    export_parser.add_argument("--poll-seconds", type=float, default=2.0)
    export_parser.add_argument("--timeout-seconds", type=float, default=300.0)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = SigmaConfig.from_env(args.env_file)
        client = SigmaAPIClient(config)

        if args.command == "test-auth":
            token = client.get_access_token()
            print(f"Auth succeeded. Access token received (length={len(token)}).")
            return 0

        if args.command == "inspect-workbook":
            return _run_inspect(client, args)

        if args.command == "export-data":
            return _run_export(client, args)
    except (EnvironmentError, SigmaAPIError, TimeoutError, ValueError, FileExistsError) as exc:
        print(f"Error: {exc}")
        return 1

    parser.print_help()
    return 1


def _run_inspect(client: SigmaAPIClient, args: argparse.Namespace) -> int:
    workbook_id, inferred_node_id = parse_workbook_locator(args.workbook)
    resolved = resolve_workbook_node_selection(client, workbook_id, inferred_node_id)
    page_id = args.page_id or resolved["page_id"]
    snapshot = inspect_workbook(
        client,
        workbook_id,
        page_id=page_id,
        tag_name=args.tag_name,
        bookmark_id=args.bookmark_id,
        include_columns=args.include_columns,
    )

    if args.json:
        print(json.dumps(snapshot, indent=2))
        return 0

    workbook = snapshot["workbook"]
    exportable = snapshot["exportable_elements"]
    if resolved["element_id"]:
        exportable = [
            element
            for element in exportable
            if str(element.get("elementId")) == resolved["element_id"]
        ]

    print(f"Workbook ID: {workbook_id}")
    print(f"Workbook name: {workbook.get('name')}")
    print(f"Workbook path: {workbook.get('path')}")
    print(f"Workbook URL: {workbook.get('url')}")
    if page_id:
        print(f"Page ID: {page_id}")
    if resolved["element_id"]:
        print(f"Element ID: {resolved['element_id']}")
    print(f"Exportable elements: {len(exportable)}")
    for line in summarize_elements(exportable):
        print(line)
    return 0


def _run_export(client: SigmaAPIClient, args: argparse.Namespace) -> int:
    workbook_id, inferred_node_id = parse_workbook_locator(args.workbook)
    resolved = resolve_workbook_node_selection(client, workbook_id, inferred_node_id)
    page_id = args.page_id or resolved["page_id"]
    resolved_element_id = resolved["element_id"]
    format_type = args.format.lower()

    if page_id and (args.element_id or args.element_name or resolved_element_id):
        raise ValueError("--page-id cannot be combined with element selection")

    if page_id and args.all_elements and args.output_file:
        raise ValueError("--output-file cannot be used together with --all-elements")

    if args.output_file and args.all_elements:
        raise ValueError("--output-file cannot be used together with --all-elements")

    chunk_size = None
    if not args.disable_chunking and format_type in CHUNKABLE_FORMATS:
        chunk_size = min(args.chunk_size, MAX_EXPORT_ROWS)

    if page_id and format_type in {"pdf", "png", "xlsx"}:
        workbook = client.get_workbook(workbook_id)
        output_file = Path(args.output_file) if args.output_file else Path(args.output_dir) / (
            f"{slugify(workbook.get('name', workbook_id))}__page-{page_id}.{format_type}"
        )
        bytes_written = client.export_to_file(
            output_file,
            workbook_id,
            format_type=format_type,
            page_id=page_id,
            chunk_size=chunk_size,
            overwrite=args.overwrite,
            poll_seconds=args.poll_seconds,
            timeout_seconds=args.timeout_seconds,
            results_validity_time_ms=args.results_validity_time_ms,
            tag_name=args.tag_name,
            bookmark_id=args.bookmark_id,
        )
        print(f"Wrote {bytes_written} bytes to {output_file}")
        return 0

    snapshot = inspect_workbook(
        client,
        workbook_id,
        page_id=page_id,
        tag_name=args.tag_name,
        bookmark_id=args.bookmark_id,
        include_columns=False,
    )
    workbook = snapshot["workbook"]
    selected = pick_elements_for_export(
        snapshot["elements"],
        element_id=args.element_id or resolved_element_id,
        element_name=args.element_name,
        all_elements=args.all_elements,
    )

    for element in selected:
        element_id = str(element.get("elementId"))
        element_name = str(element.get("name", element_id))
        output_path = (
            Path(args.output_file)
            if args.output_file
            else default_output_path(args.output_dir, workbook.get("name", workbook_id), element_name, format_type)
        )
        bytes_written = client.export_to_file(
            output_path,
            workbook_id,
            format_type=format_type,
            element_id=element_id,
            chunk_size=chunk_size,
            overwrite=args.overwrite,
            poll_seconds=args.poll_seconds,
            timeout_seconds=args.timeout_seconds,
            results_validity_time_ms=args.results_validity_time_ms,
            tag_name=args.tag_name,
            bookmark_id=args.bookmark_id,
        )
        print(f"Wrote {bytes_written} bytes to {output_path} ({element_name})")

    return 0
