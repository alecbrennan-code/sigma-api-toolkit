from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Optional, Sequence

from sigma_api_toolkit.client import (
    CHUNKABLE_FORMATS,
    MAX_EXPORT_ROWS,
    MAX_RESULTS_VALIDITY_TIME_MS,
    SigmaAPIClient,
    SigmaAPIError,
)
from sigma_api_toolkit.config import SigmaConfig
from sigma_api_toolkit.service import (
    build_send_request,
    inspect_workbook,
    pick_elements_for_export,
    resolve_workbook_node_selection,
)
from sigma_api_toolkit.utils import (
    default_output_path,
    load_controls_file,
    parse_control_args,
    parse_workbook_locator,
    slugify,
    summarize_controls,
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

    list_controls_parser = subparsers.add_parser(
        "list-controls",
        help=(
            "List every workbook control (filter / parameter) with its name and "
            "valueType. Use the name with --control NAME=VALUE on export-data "
            "or send-export to apply a filter at export time."
        ),
    )
    list_controls_parser.add_argument(
        "--workbook", required=True, help="Workbook ID or Sigma workbook URL"
    )
    list_controls_parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of text"
    )

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
    export_parser.add_argument(
        "--chunk-overlap-rows",
        type=int,
        default=1_000,
        help=(
            "For chunked CSV exports, re-request this many trailing rows from the prior chunk "
            "and verify the boundary matches exactly. Default: 1000."
        ),
    )
    export_parser.add_argument("--disable-chunking", action="store_true")
    export_parser.add_argument("--overwrite", action="store_true")
    export_parser.add_argument("--tag-name")
    export_parser.add_argument("--bookmark-id")
    export_parser.add_argument(
        "--results-validity-time-ms",
        type=int,
        default=MAX_RESULTS_VALIDITY_TIME_MS,
        help=(
            "How long Sigma should keep the exported file downloadable. "
            f"Default: {MAX_RESULTS_VALIDITY_TIME_MS} (6 hours)."
        ),
    )
    export_parser.add_argument("--poll-seconds", type=float, default=2.0)
    export_parser.add_argument("--timeout-seconds", type=float, default=300.0)
    export_parser.add_argument(
        "--control",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help=(
            "Apply a workbook control (filter) value to this export. Repeat for "
            "multiple controls. Values starting with [ { \" or looking numeric/"
            "boolean are parsed as JSON, so text-list controls can take arrays "
            "like '[\"Major Markets 1\"]'. Use sigma-toolkit list-controls to "
            "discover valid control names."
        ),
    )
    export_parser.add_argument(
        "--controls-file",
        default=None,
        help=(
            "Path to a JSON object mapping control name to value. Merged with "
            "any --control NAME=VALUE flags, with --control taking precedence."
        ),
    )
    export_parser.add_argument(
        "--print-request",
        action="store_true",
        help="Print the resolved control parameters before exporting (does not send).",
    )

    send_parser = subparsers.add_parser(
        "send-export",
        help=(
            "Use Sigma's /send export flow to deliver a workbook/page/element export to "
            "a destination such as cloud storage, Google Drive, Slack, or a webhook"
        ),
    )
    send_parser.add_argument("--workbook", required=True, help="Workbook ID or Sigma workbook URL")
    send_parser.add_argument("--element-id")
    send_parser.add_argument("--element-name")
    send_parser.add_argument("--page-id")
    send_parser.add_argument("--all-elements", action="store_true")
    send_parser.add_argument("--format", default="csv", help="Attachment format. Default: csv")
    send_parser.add_argument(
        "--request-file",
        required=True,
        help=(
            "Path to a JSON file containing the Sigma /send target config. "
            "The toolkit will inject attachments based on the workbook/page/element selection."
        ),
    )
    send_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the fully built Sigma /send request body instead of sending it.",
    )
    send_parser.add_argument("--json", action="store_true", help="Emit the Sigma response as JSON")
    send_parser.add_argument(
        "--control",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help=(
            "Apply a workbook control value to the send export. Repeat for "
            "multiple controls. Same parsing rules as export-data --control."
        ),
    )
    send_parser.add_argument(
        "--controls-file",
        default=None,
        help=(
            "Path to a JSON object mapping control name to value. Merged with "
            "any --control flags."
        ),
    )

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

        if args.command == "list-controls":
            return _run_list_controls(client, args)

        if args.command == "inspect-workbook":
            return _run_inspect(client, args)

        if args.command == "export-data":
            return _run_export(client, args)

        if args.command == "send-export":
            return _run_send_export(client, args)
    except (EnvironmentError, SigmaAPIError, TimeoutError, ValueError, FileExistsError) as exc:
        print(f"Error: {exc}")
        return 1

    parser.print_help()
    return 1


def _run_list_controls(client: SigmaAPIClient, args: argparse.Namespace) -> int:
    workbook_id, _ = parse_workbook_locator(args.workbook)
    controls = client.list_workbook_controls(workbook_id)
    if args.json:
        print(json.dumps({"entries": controls}, indent=2))
        return 0
    print(f"Workbook ID: {workbook_id}")
    print(f"Controls: {len(controls)}")
    for line in summarize_controls(controls):
        print(line)
    print()
    print(
        "Note: Sigma exposes control names and value types, but not the currently-"
        "selected value. Pass control values into an export with --control NAME=VALUE."
    )
    return 0


def _collect_control_parameters(
    control_args: Sequence[str],
    controls_file: Optional[str],
) -> Dict[str, object]:
    merged: Dict[str, object] = {}
    if controls_file:
        merged.update(load_controls_file(controls_file))
    merged.update(parse_control_args(control_args))
    return merged


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

    parameters = _collect_control_parameters(args.control, args.controls_file)
    if parameters:
        print(
            "Applying control parameters: "
            + json.dumps(parameters, sort_keys=True)
        )
    if getattr(args, "print_request", False):
        print(json.dumps({"parameters": parameters}, indent=2, sort_keys=True))
        return 0

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
            csv_overlap_rows=args.chunk_overlap_rows if format_type == "csv" else 0,
            tag_name=args.tag_name,
            bookmark_id=args.bookmark_id,
            parameters=parameters or None,
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
            csv_overlap_rows=args.chunk_overlap_rows if format_type == "csv" else 0,
            tag_name=args.tag_name,
            bookmark_id=args.bookmark_id,
            parameters=parameters or None,
        )
        print(f"Wrote {bytes_written} bytes to {output_path} ({element_name})")

    return 0


def _run_send_export(client: SigmaAPIClient, args: argparse.Namespace) -> int:
    workbook_id, inferred_node_id = parse_workbook_locator(args.workbook)
    resolved = resolve_workbook_node_selection(client, workbook_id, inferred_node_id)
    page_id = args.page_id or resolved["page_id"]
    resolved_element_id = resolved["element_id"]
    format_type = args.format.lower()

    if page_id and (args.element_id or args.element_name or resolved_element_id):
        raise ValueError("--page-id cannot be combined with element selection")

    request_body = _load_json_file(args.request_file)
    parameters = _collect_control_parameters(args.control, args.controls_file)

    if page_id:
        built_request = build_send_request(
            request_body,
            format_type=format_type,
            page_id=page_id,
            parameters=parameters or None,
        )
    else:
        snapshot = inspect_workbook(
            client,
            workbook_id,
            page_id=None,
            include_columns=False,
        )
        selected = pick_elements_for_export(
            snapshot["elements"],
            element_id=args.element_id or resolved_element_id,
            element_name=args.element_name,
            all_elements=args.all_elements,
        )
        built_request = build_send_request(
            request_body,
            format_type=format_type,
            selected_elements=selected,
            parameters=parameters or None,
        )

    if args.dry_run:
        print(json.dumps(built_request, indent=2))
        return 0

    response = client.send_export(workbook_id, request_body=built_request)
    if args.json:
        print(json.dumps(response, indent=2))
        return 0

    target_count = len(built_request.get("targets", []))
    attachment_count = len(built_request.get("attachments", []))
    print(
        "Triggered Sigma send export "
        f"for workbook {workbook_id} to {target_count} target(s) "
        f"with {attachment_count} attachment(s)."
    )
    return 0


def _load_json_file(path: str) -> object:
    return json.loads(Path(path).read_text())
