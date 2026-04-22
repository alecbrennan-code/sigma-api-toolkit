from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


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


def parse_control_arg(raw: str) -> Tuple[str, Any]:
    """Parse a CLI --control NAME=VALUE string into (name, parsed_value).

    VALUE is parsed as JSON if it looks like JSON (begins with [ { " true false
    null or a digit/minus). Otherwise it is returned as a plain string. This
    lets callers pass JSON arrays for text-list controls while keeping simple
    scalar values ergonomic:

        --control Sales-Team="Major Markets 1"
        --control Sales-Team='["Major Markets 1","Major Markets 2"]'
        --control Include-Closed=true
        --control Min-ACV=50000
    """
    if "=" not in raw:
        raise ValueError(
            f"--control expects NAME=VALUE, got {raw!r}. Example: "
            "--control Sales-Team='Major Markets 1'"
        )
    name, value = raw.split("=", 1)
    name = name.strip()
    if not name:
        raise ValueError(f"--control is missing a name before '=': {raw!r}")
    stripped = value.strip()
    if not stripped:
        return name, ""
    first_char = stripped[0]
    if first_char in "[{\"" or stripped in {"true", "false", "null"} or _looks_numeric(stripped):
        try:
            return name, json.loads(stripped)
        except json.JSONDecodeError:
            return name, value
    return name, value


def parse_control_args(raw_values: Optional[Iterable[str]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for raw in raw_values or []:
        name, value = parse_control_arg(raw)
        result[name] = value
    return result


def load_controls_file(path: str) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError(
            f"--controls-file must contain a JSON object mapping control name to value. "
            f"Got {type(data).__name__} at {path}."
        )
    return data


def summarize_controls(controls: Sequence[Mapping[str, object]]) -> List[str]:
    lines = []
    for control in controls:
        lines.append(
            f"- {control.get('name', 'unknown')} | {control.get('valueType', 'unknown')}"
        )
    return lines


_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$")


def _looks_numeric(value: str) -> bool:
    return bool(_NUMERIC_RE.match(value))
