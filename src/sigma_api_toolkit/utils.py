from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Mapping, Sequence


WORKBOOK_URL_RE = re.compile(r"/workbook/([^/?#]+)")
MULTISPACE_RE = re.compile(r"\s+")
NON_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def normalize_workbook_ref(value: str) -> str:
    candidate = value.strip()
    match = WORKBOOK_URL_RE.search(candidate)
    if match:
        return match.group(1)
    return candidate


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

