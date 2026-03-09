from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Sequence, Tuple


WORKBOOK_URL_RE = re.compile(r"/workbook/([^/?#]+)")
MULTISPACE_RE = re.compile(r"\s+")
NON_SLUG_RE = re.compile(r"[^a-z0-9._-]+")
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)
SIGMA_TOKEN_RE = re.compile(r"^[A-Za-z0-9]{10,}$")
NODE_ID_RE = re.compile(r"nodeId=([^&#]+)")


def normalize_workbook_ref(value: str) -> str:
    workbook_ref, _ = parse_workbook_locator(value)
    return workbook_ref


def parse_workbook_locator(value: str) -> Tuple[str, Optional[str]]:
    candidate = value.strip()
    node_id = _extract_node_id(candidate)

    match = WORKBOOK_URL_RE.search(candidate)
    if not match:
        return candidate, node_id

    segment = match.group(1)
    if UUID_RE.match(segment) or SIGMA_TOKEN_RE.match(segment):
        return segment, node_id

    last_token = segment.rsplit("-", 1)[-1]
    if SIGMA_TOKEN_RE.match(last_token):
        return last_token, node_id

    return segment, node_id


def slugify(value: str, fallback: str = "export") -> str:
    candidate = MULTISPACE_RE.sub("-", value.strip().lower())
    candidate = NON_SLUG_RE.sub("-", candidate).strip("-")
    return candidate or fallback


def default_output_path(
    output_dir: str,
    workbook_name: str,
    element_name: str,
    format_type: str,
) -> Path:
    filename = f"{slugify(workbook_name)}__{slugify(element_name)}.{format_type}"
    return Path(output_dir) / filename


def summarize_elements(elements: Sequence[Mapping[str, object]]) -> List[str]:
    lines = []
    for element in elements:
        columns = element.get("columns") or []
        column_count = len(columns) if isinstance(columns, list) else 0
        lines.append(
            f"- {element.get('elementId', 'unknown')} | "
            f"{element.get('type', 'unknown')} | "
            f"{element.get('name', 'unnamed')} | "
            f"columns={column_count}"
        )
    return lines


def comma_join(values: Iterable[str]) -> str:
    return ", ".join(values)


def _extract_node_id(value: str) -> Optional[str]:
    match = NODE_ID_RE.search(value)
    if not match:
        return None
    return match.group(1)
