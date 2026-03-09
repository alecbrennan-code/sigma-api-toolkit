import unittest

from sigma_api_toolkit.service import exportable_elements, pick_elements_for_export


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


if __name__ == "__main__":
    unittest.main()
