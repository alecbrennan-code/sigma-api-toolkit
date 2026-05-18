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
from sigma_api_toolkit.healthcheck import (
    DEFAULT_CELL_MAX_WIDTH,
    DEFAULT_HIGH_NULL_THRESHOLD,
    DEFAULT_MAX_FLAGGED_ROWS,
    DEFAULT_PREVIEW_ROWS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_ROW_MOSTLY_NULL_THRESHOLD,
    DEFAULT_SAMPLE_ROWS,
    health_check_workbook,
    list_workbook_page_summary,
    report_to_dict,
    report_to_markdown,
)
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

    list_pages_parser = subparsers.add_parser(
        "list-pages",
        help=(
            "Preflight a workbook: confirm access and list every page with its "
            "hidden flag (when Sigma exposes one). Used by the dashboard-"
            "healthcheck skill to let the user pick which tabs to spot-check."
        ),
    )
    list_pages_parser.add_argument(
        "--workbook", required=True, help="Workbook ID or Sigma workbook URL"
    )
    list_pages_parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of text"
    )

    health_parser = subparsers.add_parser(
        "health-check",
        help=(
            "Spot-check a Sigma workbook end-to-end: walk visible pages, "
            "sample each exportable element under the dashboard's saved default "
            "filters, and report failed exports, empty elements, or columns "
            "that are unexpectedly null. Sigma's API does not expose currently-"
            "applied filter values, so the run reflects the workbook's published defaults."
        ),
    )
    health_parser.add_argument(
        "--workbook", required=True, help="Workbook ID or Sigma workbook URL"
    )
    health_parser.add_argument(
        "--sample-rows",
        type=int,
        default=DEFAULT_SAMPLE_ROWS,
        help=f"Rows to sample per element. Default: {DEFAULT_SAMPLE_ROWS}.",
    )
    health_parser.add_argument(
        "--include-hidden-pages",
        action="store_true",
        help="Check hidden pages too (only meaningful if Sigma exposed a hidden flag).",
    )
    health_parser.add_argument(
        "--page-id",
        action="append",
        default=[],
        dest="page_ids",
        metavar="PAGE_ID",
        help=(
            "Scope the check to specific page(s). Repeatable. When provided, "
            "only these pages are sampled and the --include-hidden-pages flag "
            "is ignored (an explicit selection wins). Use sigma-toolkit "
            "list-pages to discover page IDs."
        ),
    )
    health_parser.add_argument(
        "--high-null-threshold",
        type=float,
        default=DEFAULT_HIGH_NULL_THRESHOLD,
        help=(
            "Per-column null fraction at or above which a column is flagged "
            f"(but not as fully-null). Default: {DEFAULT_HIGH_NULL_THRESHOLD}."
        ),
    )
    health_parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=DEFAULT_REQUEST_TIMEOUT_SECONDS,
        help=(
            "Per-element export download timeout. Default: "
            f"{DEFAULT_REQUEST_TIMEOUT_SECONDS}."
        ),
    )
    health_parser.add_argument(
        "--preview-rows",
        type=int,
        default=DEFAULT_PREVIEW_ROWS,
        help=(
            "Number of head rows to capture per element for semantic review. "
            f"Default: {DEFAULT_PREVIEW_ROWS}. Set to 0 to skip."
        ),
    )
    health_parser.add_argument(
        "--max-flagged-rows",
        type=int,
        default=DEFAULT_MAX_FLAGGED_ROWS,
        help=(
            "Maximum number of suspicious rows (error tokens or mostly-null) "
            f"to surface per element. Default: {DEFAULT_MAX_FLAGGED_ROWS}. "
            "Set to 0 to skip flagged-row extraction."
        ),
    )
    health_parser.add_argument(
        "--row-mostly-null-threshold",
        type=float,
        default=DEFAULT_ROW_MOSTLY_NULL_THRESHOLD,
        help=(
            "Row-level null fraction at or above which a row is flagged. "
            f"Default: {DEFAULT_ROW_MOSTLY_NULL_THRESHOLD}."
        ),
    )
    health_parser.add_argument(
        "--cell-max-width",
        type=int,
        default=DEFAULT_CELL_MAX_WIDTH,
        help=(
            "Maximum cell width (chars) in preview / flagged-row output. "
            f"Default: {DEFAULT_CELL_MAX_WIDTH}. Set to 0 to disable truncation."
        ),
    )
    health_parser.add_argument(
        "--output-json",
        default=None,
        help="Write the structured JSON report to this path.",
    )
    health_parser.add_argument(
        "--output-markdown",
        default=None,
        help="Write the markdown digest to this path.",
    )
    health_parser.add_argument(
        "--json",
        action="store_true",
        help="Also print the structured JSON report to stdout after the markdown digest.",
    )
    health_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-element progress lines.",
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

        if args.command == "list-pages":
            return _run_list_pages(client, args)

        if args.command == "inspect-workbook":
            return _run_inspect(client, args)

        if args.command == "export-data":
            return _run_export(client, args)

        if args.command == "send-export":
            return _run_send_export(client, args)

        if args.command == "health-check":
            return _run_health_check(client, args)
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


def _run_list_pages(client: SigmaAPIClient, args: argparse.Namespace) -> int:
    workbook_id, _ = parse_workbook_locator(args.workbook)
    summary = list_workbook_page_summary(client, workbook_id)
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    wb = summary["workbook"]
    pages = summary["pages"]
    print(f"Workbook ID: {wb['workbook_id']}")
    print(f"Workbook name: {wb.get('name') or '—'}")
    print(f"Workbook URL: {wb.get('url') or '—'}")
    print(f"Pages: {len(pages)}")
    if not summary["hidden_flag_exposed"]:
        print(
            "Note: Sigma /pages did not expose a hidden/visibility flag for "
            "this workbook — every page is shown without a hidden marker."
        )
    for idx, page in enumerate(pages, start=1):
        if page["hidden"] is True:
            tag = "hidden "
        elif page["hidden"] is False:
            tag = "visible"
        else:
            tag = "       "
        print(f"  {idx:>3}. [{tag}] {page['name']}  (page_id={page['page_id']})")
    return 0


def _run_health_check(client: SigmaAPIClient, args: argparse.Namespace) -> int:
    from sys import stderr

    workbook_id, _ = parse_workbook_locator(args.workbook)
    if not args.quiet:
        print(f"Running health check on workbook {workbook_id}...", file=stderr)

    def progress(page_name: str, element_name: str) -> None:
        if not args.quiet:
            print(f"  - {page_name} -> {element_name}", file=stderr)

    page_ids = args.page_ids or None
    report = health_check_workbook(
        client,
        workbook_id,
        sample_rows=args.sample_rows,
        include_hidden_pages=args.include_hidden_pages,
        page_ids=page_ids,
        high_null_threshold=args.high_null_threshold,
        request_timeout_seconds=args.request_timeout_seconds,
        preview_rows=args.preview_rows,
        max_flagged_rows=args.max_flagged_rows,
        row_mostly_null_threshold=args.row_mostly_null_threshold,
        cell_max_width=args.cell_max_width,
        on_progress=progress,
    )

    payload = report_to_dict(report)
    markdown = report_to_markdown(report)

    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2))
        if not args.quiet:
            print(f"Wrote JSON report to {out_path}", file=stderr)
    if args.output_markdown:
        out_path = Path(args.output_markdown)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown)
        if not args.quiet:
            print(f"Wrote markdown report to {out_path}", file=stderr)

    print(markdown)
    if args.json:
        print()
        print(json.dumps(payload, indent=2))

    return 0 if report.overall_status != "fail" else 2


def _load_json_file(path: str) -> object:
    return json.loads(Path(path).read_text())
