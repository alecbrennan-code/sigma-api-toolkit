import unittest

from sigma_api_toolkit.service import (
    exportable_elements,
    pick_elements_for_export,
    resolve_workbook_node_selection,
)


class ServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.elements = [
            {"elementId": "table_1", "name": "Users", "type": "table", "columns": [{"name": "id"}]},
            {"elementId": "chart_1", "name": "Users by Month", "type": "line", "columns": []},
            {"elementId": "pivot_1", "name": "Pivot", "type": "pivotTable", "columns": [{"name": "m"}]},
        ]

    def test_exportable_elements_filters_visuals(self) -> None:
        self.assertEqual(
            [item["elementId"] for item in exportable_elements(self.elements)],
            ["table_1", "pivot_1"],
        )

    def test_pick_elements_for_export_by_name(self) -> None:
        selected = pick_elements_for_export(self.elements, element_name="users")
        self.assertEqual([item["elementId"] for item in selected], ["table_1"])

    def test_pick_elements_for_export_requires_disambiguation(self) -> None:
        with self.assertRaises(ValueError):
            pick_elements_for_export(self.elements)

    def test_resolve_workbook_node_selection_prefers_page_ids(self) -> None:
        client = FakeSigmaClient(
            pages=[{"pageId": "page_1", "name": "Main"}],
            elements=self.elements,
        )
        resolved = resolve_workbook_node_selection(client, "workbook_1", "page_1")
        self.assertEqual(resolved, {"page_id": "page_1", "element_id": None})

    def test_resolve_workbook_node_selection_finds_element_ids(self) -> None:
        client = FakeSigmaClient(
            pages=[{"pageId": "page_1", "name": "Main"}],
            elements=self.elements,
        )
        resolved = resolve_workbook_node_selection(client, "workbook_1", "pivot_1")
        self.assertEqual(resolved, {"page_id": None, "element_id": "pivot_1"})


class FakeSigmaClient:
    def __init__(self, pages, elements) -> None:
        self._pages = pages
        self._elements = elements

    def list_workbook_pages(self, workbook_id):
        return self._pages

    def list_workbook_elements(self, workbook_id, **kwargs):
        return self._elements


if __name__ == "__main__":
    unittest.main()
