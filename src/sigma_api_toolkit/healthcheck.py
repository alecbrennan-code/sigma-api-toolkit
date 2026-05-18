"""Spot-check a Sigma workbook end-to-end.

Walks the workbook's pages, samples each exportable element under the
dashboard's saved default filters (Sigma's REST API does not expose the
currently-applied filter values, so the only honest behaviour is to call
/export without `parameters` and let Sigma serve the workbook's defaults),
and produces a structured report flagging failed exports, empty elements,
and columns that are unexpectedly null.

This is the v1 connectivity / null-spotting pass. A future extension can
diff the sampled output against a source-of-truth query.
"""
from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from sigma_api_toolkit.client import SigmaAPIClient, SigmaAPIError
from sigma_api_toolkit.service import exportable_elements


NULL_TOKENS = {"", "null", "NULL", "None", "NaN", "nan"}
ERROR_TOKENS = {
    "#ERROR",
    "#ERROR!",
    "#DIV/0!",
    "#REF!",
    "#N/A",
    "#NAME?",
    "#NULL!",
    "#NUM!",
    "#VALUE!",
    "undefined",
    "Infinity",
    "-Infinity",
}
DEFAULT_SAMPLE_ROWS = 1000
DEFAULT_HIGH_NULL_THRESHOLD = 0.5
DEFAULT_REQUEST_TIMEOUT_SECONDS = 120.0
DEFAULT_PREVIEW_ROWS = 5
DEFAULT_MAX_FLAGGED_ROWS = 10
DEFAULT_ROW_MOSTLY_NULL_THRESHOLD = 0.8
DEFAULT_CELL_MAX_WIDTH = 80


@dataclass
class ColumnStat:
    name: str
    null_count: int
    null_pct: float


@dataclass
class FlaggedRow:
    row_index: int  # 1-based index within the sampled rows
    reasons: List[str]
    row: Dict[str, str]


@dataclass
class ElementHealth:
    element_id: str
    name: str
    type: str
    status: str  # ok | warn | fail
    issues: List[str] = field(default_factory=list)
    sample_rows: int = 0
    sampled_columns: int = 0
    fully_null_columns: List[str] = field(default_factory=list)
    high_null_columns: List[ColumnStat] = field(default_factory=list)
    sample_preview: List[Dict[str, str]] = field(default_factory=list)
    flagged_rows: List[FlaggedRow] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    error: Optional[str] = None


@dataclass
class PageHealth:
    page_id: str
    name: str
    hidden: Optional[bool]
    element_count: int
    elements: List[ElementHealth] = field(default_factory=list)


@dataclass
class WorkbookHealthReport:
    workbook_id: str
    workbook_name: Optional[str]
    workbook_url: Optional[str]
    workbook_path: Optional[str]
    controls: List[Dict[str, Any]]
    pages: List[PageHealth]
    overall_status: str  # ok | warn | fail
    summary: Dict[str, int]
    notes: List[str] = field(default_factory=list)


def is_page_hidden(page: Dict[str, Any]) -> Optional[bool]:
    """Return True/False if Sigma exposed a hidden flag, else None.

    Sigma's /pages payload has varied over time. We probe the field names
    that have appeared in practice; if none are present we return None so
    the caller can decide whether to skip or check every page.
    """
    for key in ("hidden", "isHidden"):
        if key in page:
            return bool(page[key])
    visibility = page.get("visibility")
    if isinstance(visibility, str):
        return visibility.strip().lower() == "hidden"
    return None


def parse_csv(raw: bytes) -> tuple[List[str], List[List[str]]]:
    text = raw.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], []
    return rows[0], rows[1:]


def column_null_stats(rows: List[List[str]], header: List[str]) -> List[ColumnStat]:
    if not header:
        return []
    if not rows:
        return [ColumnStat(name=col, null_count=0, null_pct=0.0) for col in header]
    stats: List[ColumnStat] = []
    for col_idx, col_name in enumerate(header):
        null_count = 0
        for row in rows:
            value = row[col_idx] if col_idx < len(row) else ""
            if (value or "").strip() in NULL_TOKENS:
                null_count += 1
        stats.append(
            ColumnStat(
                name=col_name,
                null_count=null_count,
                null_pct=null_count / len(rows),
            )
        )
    return stats


def _truncate_cell(value: str, max_width: int) -> str:
    if max_width <= 0 or len(value) <= max_width:
        return value
    return value[: max_width - 1] + "…"


def _row_as_dict(
    header: List[str],
    row: List[str],
    *,
    cell_max_width: int,
) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for col_idx, col_name in enumerate(header):
        raw = row[col_idx] if col_idx < len(row) else ""
        result[col_name] = _truncate_cell(raw, cell_max_width)
    return result


def extract_sample_preview(
    header: List[str],
    rows: List[List[str]],
    *,
    preview_rows: int,
    cell_max_width: int = DEFAULT_CELL_MAX_WIDTH,
) -> List[Dict[str, str]]:
    if preview_rows <= 0 or not header:
        return []
    return [
        _row_as_dict(header, row, cell_max_width=cell_max_width)
        for row in rows[:preview_rows]
    ]


def _row_is_mostly_null(
    row: List[str],
    column_count: int,
    threshold: float,
) -> bool:
    if column_count == 0:
        return False
    null_count = 0
    for col_idx in range(column_count):
        value = row[col_idx] if col_idx < len(row) else ""
        if (value or "").strip() in NULL_TOKENS:
            null_count += 1
    return (null_count / column_count) >= threshold


def _row_error_cells(
    header: List[str],
    row: List[str],
) -> List[str]:
    flagged_columns: List[str] = []
    for col_idx, col_name in enumerate(header):
        if col_idx >= len(row):
            continue
        stripped = (row[col_idx] or "").strip()
        if stripped in ERROR_TOKENS:
            flagged_columns.append(col_name)
    return flagged_columns


def extract_flagged_rows(
    header: List[str],
    rows: List[List[str]],
    *,
    max_flagged_rows: int,
    row_mostly_null_threshold: float = DEFAULT_ROW_MOSTLY_NULL_THRESHOLD,
    cell_max_width: int = DEFAULT_CELL_MAX_WIDTH,
) -> List[FlaggedRow]:
    if max_flagged_rows <= 0 or not header:
        return []
    flagged: List[FlaggedRow] = []
    column_count = len(header)
    for idx, row in enumerate(rows, start=1):
        reasons: List[str] = []
        if _row_is_mostly_null(row, column_count, row_mostly_null_threshold):
            pct = int(row_mostly_null_threshold * 100)
            reasons.append(f"row is ≥{pct}% null")
        error_columns = _row_error_cells(header, row)
        if error_columns:
            preview = ", ".join(error_columns[:3])
            more = "" if len(error_columns) <= 3 else f" (+{len(error_columns) - 3} more)"
            reasons.append(f"error token in column(s): {preview}{more}")
        if reasons:
            flagged.append(
                FlaggedRow(
                    row_index=idx,
                    reasons=reasons,
                    row=_row_as_dict(header, row, cell_max_width=cell_max_width),
                )
            )
            if len(flagged) >= max_flagged_rows:
                break
    return flagged


def classify_element(
    *,
    sample_rows: int,
    stats: List[ColumnStat],
    error: Optional[str],
    high_null_threshold: float,
    flagged_rows: Optional[List[FlaggedRow]] = None,
) -> tuple[str, List[str], List[str], List[ColumnStat]]:
    if error:
        return "fail", [f"export failed: {error}"], [], []
    issues: List[str] = []
    if sample_rows == 0:
        return (
            "warn",
            ["element returned 0 rows under default filters"],
            [],
            [],
        )
    fully_null = [s.name for s in stats if s.null_pct >= 0.999]
    high_null = [s for s in stats if high_null_threshold <= s.null_pct < 0.999]
    if fully_null:
        issues.append(f"{len(fully_null)} column(s) are 100% null in the sample")
    if high_null:
        threshold_pct = int(high_null_threshold * 100)
        issues.append(
            f"{len(high_null)} column(s) at or above {threshold_pct}% null"
        )
    flagged_rows = flagged_rows or []
    error_token_rows = [
        fr for fr in flagged_rows if any("error token" in r for r in fr.reasons)
    ]
    mostly_null_rows = [
        fr for fr in flagged_rows if any("null" in r for r in fr.reasons)
    ]
    if error_token_rows:
        issues.append(
            f"{len(error_token_rows)} row(s) contain spreadsheet error tokens "
            "(#ERROR, #DIV/0!, #REF!, etc.)"
        )
    if mostly_null_rows:
        issues.append(
            f"{len(mostly_null_rows)} row(s) are mostly null"
        )
    status = "warn" if issues else "ok"
    return status, issues, fully_null, high_null


def check_element_health(
    client: SigmaAPIClient,
    workbook_id: str,
    element: Dict[str, Any],
    *,
    sample_rows: int = DEFAULT_SAMPLE_ROWS,
    high_null_threshold: float = DEFAULT_HIGH_NULL_THRESHOLD,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    preview_rows: int = DEFAULT_PREVIEW_ROWS,
    max_flagged_rows: int = DEFAULT_MAX_FLAGGED_ROWS,
    row_mostly_null_threshold: float = DEFAULT_ROW_MOSTLY_NULL_THRESHOLD,
    cell_max_width: int = DEFAULT_CELL_MAX_WIDTH,
) -> ElementHealth:
    element_id = str(element.get("elementId"))
    name = str(element.get("name", element_id))
    element_type = str(element.get("type", ""))
    started = time.time()
    try:
        query_id = client.create_export(
            workbook_id,
            format_type="csv",
            element_id=element_id,
            row_limit=sample_rows,
        )
        raw = client.wait_for_download(
            query_id,
            poll_seconds=2.0,
            timeout_seconds=request_timeout_seconds,
        )
    except (SigmaAPIError, TimeoutError) as exc:
        return ElementHealth(
            element_id=element_id,
            name=name,
            type=element_type,
            status="fail",
            issues=[f"export failed: {exc}"],
            error=str(exc),
            elapsed_seconds=round(time.time() - started, 2),
        )

    header, rows = parse_csv(raw)
    stats = column_null_stats(rows, header)
    sample_preview = extract_sample_preview(
        header,
        rows,
        preview_rows=preview_rows,
        cell_max_width=cell_max_width,
    )
    flagged_rows = extract_flagged_rows(
        header,
        rows,
        max_flagged_rows=max_flagged_rows,
        row_mostly_null_threshold=row_mostly_null_threshold,
        cell_max_width=cell_max_width,
    )
    status, issues, fully_null, high_null = classify_element(
        sample_rows=len(rows),
        stats=stats,
        error=None,
        high_null_threshold=high_null_threshold,
        flagged_rows=flagged_rows,
    )
    return ElementHealth(
        element_id=element_id,
        name=name,
        type=element_type,
        status=status,
        issues=issues,
        sample_rows=len(rows),
        sampled_columns=len(header),
        fully_null_columns=fully_null,
        high_null_columns=high_null,
        sample_preview=sample_preview,
        flagged_rows=flagged_rows,
        elapsed_seconds=round(time.time() - started, 2),
    )


def list_workbook_page_summary(
    client: SigmaAPIClient,
    workbook_id: str,
) -> Dict[str, Any]:
    """Lightweight preflight: confirm access + return workbook metadata + pages.

    Used by the dashboard-healthcheck skill to surface a selectable list of
    pages to the user before running the (slower) per-element sampling pass.
    """
    workbook = client.get_workbook(workbook_id)
    pages = client.list_workbook_pages(workbook_id)
    return {
        "workbook": {
            "workbook_id": workbook_id,
            "name": workbook.get("name"),
            "url": workbook.get("url"),
            "path": workbook.get("path"),
        },
        "pages": [
            {
                "page_id": str(p.get("pageId")),
                "name": str(p.get("name", p.get("pageId"))),
                "hidden": is_page_hidden(p),
            }
            for p in pages
        ],
        "hidden_flag_exposed": any(is_page_hidden(p) is not None for p in pages),
    }


def health_check_workbook(
    client: SigmaAPIClient,
    workbook_id: str,
    *,
    sample_rows: int = DEFAULT_SAMPLE_ROWS,
    include_hidden_pages: bool = False,
    page_ids: Optional[List[str]] = None,
    high_null_threshold: float = DEFAULT_HIGH_NULL_THRESHOLD,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    preview_rows: int = DEFAULT_PREVIEW_ROWS,
    max_flagged_rows: int = DEFAULT_MAX_FLAGGED_ROWS,
    row_mostly_null_threshold: float = DEFAULT_ROW_MOSTLY_NULL_THRESHOLD,
    cell_max_width: int = DEFAULT_CELL_MAX_WIDTH,
    on_progress: Optional[Callable[[str, str], None]] = None,
) -> WorkbookHealthReport:
    workbook = client.get_workbook(workbook_id)
    controls = client.list_workbook_controls(workbook_id)
    pages = client.list_workbook_pages(workbook_id)

    notes: List[str] = []
    if not any(is_page_hidden(p) is not None for p in pages):
        notes.append(
            "Sigma /pages did not expose a hidden/visibility flag — every "
            "non-filtered page was checked."
        )

    selected_page_ids: Optional[set] = None
    if page_ids is not None:
        selected_page_ids = {str(pid) for pid in page_ids}
        available_ids = {str(p.get("pageId")) for p in pages}
        unknown = selected_page_ids - available_ids
        if unknown:
            raise ValueError(
                "Unknown page id(s) for this workbook: "
                + ", ".join(sorted(unknown))
            )
        notes.append(
            f"Scoped to {len(selected_page_ids)} of {len(pages)} page(s) "
            "as requested by the caller."
        )

    page_reports: List[PageHealth] = []
    for page in pages:
        page_id = str(page.get("pageId"))
        page_name = str(page.get("name", page_id))
        hidden = is_page_hidden(page)

        if selected_page_ids is not None and page_id not in selected_page_ids:
            continue

        # If the caller explicitly picked this page, honour the selection
        # even when the page is hidden — the user opted in by selecting it.
        skip_for_hidden = (
            hidden
            and not include_hidden_pages
            and selected_page_ids is None
        )
        if skip_for_hidden:
            page_reports.append(
                PageHealth(
                    page_id=page_id,
                    name=page_name,
                    hidden=True,
                    element_count=0,
                )
            )
            continue

        elements = client.list_workbook_elements(workbook_id, page_id=page_id)
        exportable = exportable_elements(elements)
        per_element: List[ElementHealth] = []
        for element in exportable:
            if on_progress:
                on_progress(page_name, str(element.get("name", "")))
            per_element.append(
                check_element_health(
                    client,
                    workbook_id,
                    element,
                    sample_rows=sample_rows,
                    high_null_threshold=high_null_threshold,
                    request_timeout_seconds=request_timeout_seconds,
                    preview_rows=preview_rows,
                    max_flagged_rows=max_flagged_rows,
                    row_mostly_null_threshold=row_mostly_null_threshold,
                    cell_max_width=cell_max_width,
                )
            )
        page_reports.append(
            PageHealth(
                page_id=page_id,
                name=page_name,
                hidden=hidden,
                element_count=len(exportable),
                elements=per_element,
            )
        )

    summary = {"ok": 0, "warn": 0, "fail": 0, "elements_checked": 0}
    for pr in page_reports:
        for el in pr.elements:
            summary[el.status] += 1
            summary["elements_checked"] += 1

    if summary["fail"] > 0:
        overall = "fail"
    elif summary["warn"] > 0:
        overall = "warn"
    else:
        overall = "ok"

    return WorkbookHealthReport(
        workbook_id=workbook_id,
        workbook_name=workbook.get("name"),
        workbook_url=workbook.get("url"),
        workbook_path=workbook.get("path"),
        controls=controls,
        pages=page_reports,
        overall_status=overall,
        summary=summary,
        notes=notes,
    )


def report_to_dict(report: WorkbookHealthReport) -> Dict[str, Any]:
    return {
        "workbook_id": report.workbook_id,
        "workbook_name": report.workbook_name,
        "workbook_url": report.workbook_url,
        "workbook_path": report.workbook_path,
        "overall_status": report.overall_status,
        "summary": report.summary,
        "notes": report.notes,
        "controls": [
            {"name": c.get("name"), "valueType": c.get("valueType")}
            for c in report.controls
        ],
        "pages": [
            {
                "page_id": p.page_id,
                "name": p.name,
                "hidden": p.hidden,
                "element_count": p.element_count,
                "elements": [
                    {
                        "element_id": e.element_id,
                        "name": e.name,
                        "type": e.type,
                        "status": e.status,
                        "issues": e.issues,
                        "sample_rows": e.sample_rows,
                        "sampled_columns": e.sampled_columns,
                        "fully_null_columns": e.fully_null_columns,
                        "high_null_columns": [
                            {
                                "name": s.name,
                                "null_pct": round(s.null_pct, 3),
                                "null_count": s.null_count,
                            }
                            for s in e.high_null_columns
                        ],
                        "sample_preview": e.sample_preview,
                        "flagged_rows": [
                            {
                                "row_index": fr.row_index,
                                "reasons": fr.reasons,
                                "row": fr.row,
                            }
                            for fr in e.flagged_rows
                        ],
                        "elapsed_seconds": e.elapsed_seconds,
                        "error": e.error,
                    }
                    for e in p.elements
                ],
            }
            for p in report.pages
        ],
    }


STATUS_ICON = {"ok": "[OK]", "warn": "[WARN]", "fail": "[FAIL]"}


def _md_escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _render_row_dicts_as_markdown(
    rows: List[Dict[str, str]],
    header: List[str],
) -> List[str]:
    if not rows or not header:
        return []
    lines = [
        "| " + " | ".join(_md_escape_cell(col) for col in header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(_md_escape_cell(row.get(col, "")) for col in header)
            + " |"
        )
    return lines


def report_to_markdown(report: WorkbookHealthReport) -> str:
    lines = [
        f"# Dashboard health check: {report.workbook_name or report.workbook_id}",
        "",
        f"- Overall: {STATUS_ICON[report.overall_status]} {report.overall_status.upper()}",
        f"- Workbook ID: `{report.workbook_id}`",
        f"- URL: {report.workbook_url or '—'}",
        (
            f"- Elements checked: {report.summary['elements_checked']} "
            f"({STATUS_ICON['ok']} {report.summary['ok']} / "
            f"{STATUS_ICON['warn']} {report.summary['warn']} / "
            f"{STATUS_ICON['fail']} {report.summary['fail']})"
        ),
        (
            f"- Filter surface: {len(report.controls)} control(s). "
            "Default values were applied — Sigma API does not expose the "
            "currently-selected filter state."
        ),
        "",
    ]
    if report.notes:
        lines.append("Notes:")
        for note in report.notes:
            lines.append(f"- {note}")
        lines.append("")

    for page in report.pages:
        hidden_tag = " (hidden)" if page.hidden else ""
        lines.append(f"## {page.name}{hidden_tag}")
        if page.hidden and not page.elements:
            lines.append("_skipped — page is hidden_")
            lines.append("")
            continue
        if not page.elements:
            lines.append("_no exportable elements on this page_")
            lines.append("")
            continue
        for el in page.elements:
            lines.append(
                f"- {STATUS_ICON[el.status]} **{el.name}** "
                f"(`{el.element_id}` / {el.type}) — "
                f"{el.sample_rows} rows × {el.sampled_columns} cols "
                f"in {el.elapsed_seconds}s"
            )
            for issue in el.issues:
                lines.append(f"    - {issue}")
            if el.fully_null_columns:
                preview = ", ".join(el.fully_null_columns[:5])
                more = (
                    ""
                    if len(el.fully_null_columns) <= 5
                    else f" (+{len(el.fully_null_columns) - 5} more)"
                )
                lines.append(f"    - 100% null columns: {preview}{more}")
            if el.high_null_columns:
                preview = ", ".join(
                    f"{s.name} ({int(s.null_pct * 100)}%)"
                    for s in el.high_null_columns[:5]
                )
                more = (
                    ""
                    if len(el.high_null_columns) <= 5
                    else f" (+{len(el.high_null_columns) - 5} more)"
                )
                lines.append(f"    - high-null columns: {preview}{more}")
            if el.sample_preview:
                preview_header = list(el.sample_preview[0].keys())
                lines.append("")
                lines.append(
                    f"    Sample (first {len(el.sample_preview)} row(s)):"
                )
                for table_line in _render_row_dicts_as_markdown(
                    el.sample_preview, preview_header
                ):
                    lines.append(f"    {table_line}")
            if el.flagged_rows:
                flagged_header = list(el.flagged_rows[0].row.keys())
                lines.append("")
                lines.append(
                    f"    Flagged rows ({len(el.flagged_rows)} shown):"
                )
                for fr in el.flagged_rows:
                    reasons = "; ".join(fr.reasons)
                    lines.append(f"    - row {fr.row_index}: {reasons}")
                for table_line in _render_row_dicts_as_markdown(
                    [fr.row for fr in el.flagged_rows], flagged_header
                ):
                    lines.append(f"    {table_line}")
        lines.append("")

    return "\n".join(lines)
