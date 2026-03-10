from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from sigma_api_toolkit.client import SigmaAPIClient


EXPORTABLE_TYPES = {"table", "pivot", "pivotTable", "inputTable"}


def inspect_workbook(
    client: SigmaAPIClient,
    workbook_id: str,
    *,
    page_id: Optional[str] = None,
    tag_name: Optional[str] = None,
    bookmark_id: Optional[str] = None,
    include_columns: bool = False,
) -> Dict:
    workbook = client.get_workbook(workbook_id)
    elements = client.list_workbook_elements(
        workbook_id,
        page_id=page_id,
        tag_name=tag_name,
        bookmark_id=bookmark_id,
    )

    if include_columns:
        for element in elements:
            element_id = element.get("elementId")
            if not element_id:
                continue
            try:
                element["columns"] = client.list_element_columns(workbook_id, str(element_id))
            except Exception:
                # Some element types do not expose columns even though they appear in the workbook.
                element.setdefault("columns", [])

    return {
        "workbook": workbook,
        "elements": elements,
        "exportable_elements": exportable_elements(elements),
    }


def resolve_workbook_node_selection(
    client: SigmaAPIClient,
    workbook_id: str,
    node_id: Optional[str],
) -> Dict[str, Optional[str]]:
    if not node_id:
        return {"page_id": None, "element_id": None}

    pages = client.list_workbook_pages(workbook_id)
    for page in pages:
        if str(page.get("pageId")) == node_id:
            return {"page_id": node_id, "element_id": None}

    elements = client.list_workbook_elements(workbook_id)
    for element in elements:
        if str(element.get("elementId")) == node_id:
            return {"page_id": None, "element_id": node_id}

    return {"page_id": None, "element_id": None}


def exportable_elements(elements: Sequence[Dict]) -> List[Dict]:
    candidates = []
    for element in elements:
        element_type = str(element.get("type", ""))
        columns = element.get("columns") or []
        if element_type in EXPORTABLE_TYPES or bool(columns):
            candidates.append(element)
    return candidates


def pick_elements_for_export(
    elements: Sequence[Dict],
    *,
    element_id: Optional[str] = None,
    element_name: Optional[str] = None,
    all_elements: bool = False,
) -> List[Dict]:
    candidates = exportable_elements(elements)
    if all_elements:
        return candidates

    if element_id:
        matches = [element for element in candidates if str(element.get("elementId")) == element_id]
        if not matches:
            raise ValueError(f"No exportable workbook element matched element_id={element_id}")
        return matches

    if element_name:
        name_key = element_name.strip().lower()
        exact = [
            element
            for element in candidates
            if str(element.get("name", "")).strip().lower() == name_key
        ]
        if len(exact) == 1:
            return exact

        partial = [
            element
            for element in candidates
            if name_key in str(element.get("name", "")).strip().lower()
        ]
        matches = exact if len(exact) > 1 else partial
        if len(matches) == 1:
            return matches
        if not matches:
            raise ValueError(f"No exportable workbook element matched element_name={element_name!r}")
        raise ValueError(
            "Element name matched multiple exportable elements: "
            + ", ".join(str(element.get("name")) for element in matches)
        )

    if len(candidates) == 1:
        return candidates

    raise ValueError(
        "Workbook has multiple exportable elements. Re-run with --element-id, "
        "--element-name, or --all-elements."
    )
