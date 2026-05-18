import unittest
from typing import Any, Dict, List, Optional

from sigma_api_toolkit.healthcheck import (
    classify_element,
    column_null_stats,
    extract_flagged_rows,
    extract_sample_preview,
    health_check_workbook,
    is_page_hidden,
    list_workbook_page_summary,
    parse_csv,
    report_to_dict,
    report_to_markdown,
)


class IsPageHiddenTest(unittest.TestCase):
    def test_hidden_flag_true(self) -> None:
        self.assertTrue(is_page_hidden({"pageId": "p", "hidden": True}))

    def test_hidden_flag_false(self) -> None:
        self.assertFalse(is_page_hidden({"pageId": "p", "hidden": False}))

    def test_is_hidden_camelcase(self) -> None:
        self.assertTrue(is_page_hidden({"pageId": "p", "isHidden": True}))

    def test_visibility_field(self) -> None:
        self.assertTrue(is_page_hidden({"pageId": "p", "visibility": "Hidden"}))
        self.assertFalse(is_page_hidden({"pageId": "p", "visibility": "visible"}))

    def test_no_flag_returns_none(self) -> None:
        self.assertIsNone(is_page_hidden({"pageId": "p", "name": "Main"}))


class ColumnNullStatsTest(unittest.TestCase):
    def test_handles_no_rows(self) -> None:
        stats = column_null_stats(rows=[], header=["a", "b"])
        self.assertEqual([s.null_count for s in stats], [0, 0])
        self.assertEqual([s.null_pct for s in stats], [0.0, 0.0])

    def test_counts_null_tokens(self) -> None:
        rows = [
            ["1", "", "x"],
            ["2", "null", "y"],
            ["3", "None", "z"],
            ["4", "value", "w"],
        ]
        stats = column_null_stats(rows=rows, header=["id", "maybe_null", "name"])
        self.assertEqual(stats[0].null_count, 0)
        self.assertEqual(stats[1].null_count, 3)
        self.assertAlmostEqual(stats[1].null_pct, 0.75)
        self.assertEqual(stats[2].null_count, 0)

    def test_short_row_counts_as_null(self) -> None:
        rows = [["1", "a"], ["2"]]  # second row missing the column-c value
        stats = column_null_stats(rows=rows, header=["a", "b", "c"])
        self.assertEqual(stats[2].null_count, 2)


class ClassifyElementTest(unittest.TestCase):
    def test_error_is_fail(self) -> None:
        status, issues, fully, high = classify_element(
            sample_rows=0,
            stats=[],
            error="boom",
            high_null_threshold=0.5,
        )
        self.assertEqual(status, "fail")
        self.assertEqual(fully, [])
        self.assertEqual(high, [])
        self.assertEqual(len(issues), 1)

    def test_zero_rows_is_warn(self) -> None:
        status, issues, _, _ = classify_element(
            sample_rows=0,
            stats=[],
            error=None,
            high_null_threshold=0.5,
        )
        self.assertEqual(status, "warn")
        self.assertIn("0 rows", issues[0])

    def test_fully_null_column_is_warn(self) -> None:
        from sigma_api_toolkit.healthcheck import ColumnStat

        stats = [
            ColumnStat(name="ok", null_count=0, null_pct=0.0),
            ColumnStat(name="empty", null_count=100, null_pct=1.0),
        ]
        status, issues, fully, high = classify_element(
            sample_rows=100,
            stats=stats,
            error=None,
            high_null_threshold=0.5,
        )
        self.assertEqual(status, "warn")
        self.assertEqual(fully, ["empty"])
        self.assertEqual(high, [])

    def test_high_null_column_is_warn(self) -> None:
        from sigma_api_toolkit.healthcheck import ColumnStat

        stats = [
            ColumnStat(name="ok", null_count=0, null_pct=0.0),
            ColumnStat(name="mostly_null", null_count=70, null_pct=0.7),
        ]
        status, _, fully, high = classify_element(
            sample_rows=100,
            stats=stats,
            error=None,
            high_null_threshold=0.5,
        )
        self.assertEqual(status, "warn")
        self.assertEqual(fully, [])
        self.assertEqual([s.name for s in high], ["mostly_null"])

    def test_clean_sample_is_ok(self) -> None:
        from sigma_api_toolkit.healthcheck import ColumnStat

        stats = [ColumnStat(name="ok", null_count=0, null_pct=0.0)]
        status, issues, _, _ = classify_element(
            sample_rows=100,
            stats=stats,
            error=None,
            high_null_threshold=0.5,
        )
        self.assertEqual(status, "ok")
        self.assertEqual(issues, [])


class ExtractSamplePreviewTest(unittest.TestCase):
    def test_returns_first_n_rows_as_dicts(self) -> None:
        header = ["a", "b"]
        rows = [["1", "x"], ["2", "y"], ["3", "z"]]
        preview = extract_sample_preview(header, rows, preview_rows=2)
        self.assertEqual(preview, [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}])

    def test_truncates_long_cells(self) -> None:
        header = ["a"]
        rows = [["x" * 200]]
        preview = extract_sample_preview(header, rows, preview_rows=1, cell_max_width=50)
        self.assertEqual(len(preview[0]["a"]), 50)
        self.assertTrue(preview[0]["a"].endswith("…"))

    def test_zero_preview_rows_returns_empty(self) -> None:
        self.assertEqual(
            extract_sample_preview(["a"], [["1"]], preview_rows=0),
            [],
        )

    def test_pads_short_rows_to_header(self) -> None:
        header = ["a", "b", "c"]
        rows = [["1", "x"]]  # row is shorter than header
        preview = extract_sample_preview(header, rows, preview_rows=1)
        self.assertEqual(preview, [{"a": "1", "b": "x", "c": ""}])


class ExtractFlaggedRowsTest(unittest.TestCase):
    def test_flags_error_tokens(self) -> None:
        header = ["a", "b", "c"]
        rows = [
            ["1", "ok", "3"],
            ["2", "#DIV/0!", "4"],
            ["3", "ok", "#REF!"],
        ]
        flagged = extract_flagged_rows(header, rows, max_flagged_rows=10)
        self.assertEqual(len(flagged), 2)
        self.assertEqual(flagged[0].row_index, 2)
        self.assertIn("error token", flagged[0].reasons[0])
        self.assertEqual(flagged[1].row_index, 3)

    def test_flags_mostly_null_rows(self) -> None:
        header = ["a", "b", "c", "d"]
        rows = [
            ["1", "x", "y", "z"],
            ["2", "", "", ""],  # 75% null
            ["3", "", "", "v"],  # 50% null
        ]
        flagged = extract_flagged_rows(
            header, rows, max_flagged_rows=10, row_mostly_null_threshold=0.7
        )
        self.assertEqual([fr.row_index for fr in flagged], [2])

    def test_respects_max_flagged_rows(self) -> None:
        header = ["a"]
        rows = [["#ERROR"] for _ in range(20)]
        flagged = extract_flagged_rows(header, rows, max_flagged_rows=3)
        self.assertEqual(len(flagged), 3)

    def test_zero_max_flagged_rows_returns_empty(self) -> None:
        self.assertEqual(
            extract_flagged_rows(["a"], [["#ERROR"]], max_flagged_rows=0),
            [],
        )


class ClassifyElementWithFlaggedRowsTest(unittest.TestCase):
    def test_error_token_rows_are_warn(self) -> None:
        from sigma_api_toolkit.healthcheck import ColumnStat, FlaggedRow

        stats = [ColumnStat(name="a", null_count=0, null_pct=0.0)]
        flagged = [FlaggedRow(row_index=1, reasons=["error token in column(s): b"], row={"a": "1"})]
        status, issues, _, _ = classify_element(
            sample_rows=10,
            stats=stats,
            error=None,
            high_null_threshold=0.5,
            flagged_rows=flagged,
        )
        self.assertEqual(status, "warn")
        self.assertTrue(any("error tokens" in i for i in issues))

    def test_mostly_null_rows_are_warn(self) -> None:
        from sigma_api_toolkit.healthcheck import ColumnStat, FlaggedRow

        stats = [ColumnStat(name="a", null_count=0, null_pct=0.0)]
        flagged = [FlaggedRow(row_index=4, reasons=["row is ≥80% null"], row={"a": "1"})]
        status, issues, _, _ = classify_element(
            sample_rows=10,
            stats=stats,
            error=None,
            high_null_threshold=0.5,
            flagged_rows=flagged,
        )
        self.assertEqual(status, "warn")
        self.assertTrue(any("mostly null" in i for i in issues))


class ParseCsvTest(unittest.TestCase):
    def test_parses_header_and_rows(self) -> None:
        raw = b"a,b,c\n1,,3\n4,5,6\n"
        header, rows = parse_csv(raw)
        self.assertEqual(header, ["a", "b", "c"])
        self.assertEqual(rows, [["1", "", "3"], ["4", "5", "6"]])

    def test_empty_input(self) -> None:
        header, rows = parse_csv(b"")
        self.assertEqual(header, [])
        self.assertEqual(rows, [])


class ListWorkbookPageSummaryTest(unittest.TestCase):
    def test_returns_normalized_pages_and_workbook_meta(self) -> None:
        client = FakeHealthCheckClient(
            workbook={"name": "WB", "url": "https://w", "path": "/p"},
            controls=[],
            pages=[
                {"pageId": "p1", "name": "Visible", "hidden": False},
                {"pageId": "p2", "name": "Scratch", "hidden": True},
                {"pageId": "p3", "name": "Unknown"},
            ],
            elements_by_page={},
            export_outputs={},
        )
        summary = list_workbook_page_summary(client, "wb_1")
        self.assertEqual(summary["workbook"]["workbook_id"], "wb_1")
        self.assertEqual(summary["workbook"]["name"], "WB")
        self.assertEqual(
            [(p["page_id"], p["name"], p["hidden"]) for p in summary["pages"]],
            [("p1", "Visible", False), ("p2", "Scratch", True), ("p3", "Unknown", None)],
        )
        self.assertTrue(summary["hidden_flag_exposed"])

    def test_flags_when_no_hidden_marker_seen(self) -> None:
        client = FakeHealthCheckClient(
            workbook={"name": "WB"},
            controls=[],
            pages=[{"pageId": "p1", "name": "Only"}],
            elements_by_page={},
            export_outputs={},
        )
        summary = list_workbook_page_summary(client, "wb_1")
        self.assertFalse(summary["hidden_flag_exposed"])


class HealthCheckEndToEndTest(unittest.TestCase):
    def test_full_workflow_against_fake_client(self) -> None:
        client = FakeHealthCheckClient(
            workbook={"name": "Test WB", "url": "https://example/w", "path": "/test"},
            controls=[{"name": "Sales-Team", "valueType": "text-list"}],
            pages=[
                {"pageId": "p_visible", "name": "Main"},
                {"pageId": "p_hidden", "name": "Scratch", "hidden": True},
            ],
            elements_by_page={
                "p_visible": [
                    {"elementId": "e_ok", "name": "Healthy Table", "type": "table", "columns": [{}]},
                    {"elementId": "e_empty", "name": "Empty Table", "type": "table", "columns": [{}]},
                    {"elementId": "e_null", "name": "Null Column", "type": "table", "columns": [{}]},
                    {"elementId": "e_fail", "name": "Broken Table", "type": "table", "columns": [{}]},
                ],
                "p_hidden": [],
            },
            export_outputs={
                "e_ok": (b"a,b\n1,x\n2,y\n", None),
                "e_empty": (b"a,b\n", None),
                "e_null": (b"a,b\n1,\n2,\n", None),
                "e_fail": (None, "503 Service Unavailable"),
            },
        )

        report = health_check_workbook(
            client,
            "wb_1",
            sample_rows=100,
        )

        self.assertEqual(report.workbook_name, "Test WB")
        self.assertEqual(report.overall_status, "fail")
        self.assertEqual(
            report.summary,
            {"ok": 1, "warn": 2, "fail": 1, "elements_checked": 4},
        )

        visible_page = next(p for p in report.pages if p.page_id == "p_visible")
        hidden_page = next(p for p in report.pages if p.page_id == "p_hidden")

        self.assertEqual([e.element_id for e in visible_page.elements],
                         ["e_ok", "e_empty", "e_null", "e_fail"])
        statuses = {e.element_id: e.status for e in visible_page.elements}
        self.assertEqual(statuses, {"e_ok": "ok", "e_empty": "warn", "e_null": "warn", "e_fail": "fail"})

        null_element = next(e for e in visible_page.elements if e.element_id == "e_null")
        self.assertEqual(null_element.fully_null_columns, ["b"])

        self.assertTrue(hidden_page.hidden)
        self.assertEqual(hidden_page.elements, [])

        payload = report_to_dict(report)
        self.assertEqual(payload["overall_status"], "fail")
        self.assertEqual(payload["controls"], [{"name": "Sales-Team", "valueType": "text-list"}])

        ok_element = next(
            e for p in payload["pages"] for e in p["elements"] if e["element_id"] == "e_ok"
        )
        self.assertEqual(
            ok_element["sample_preview"],
            [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}],
        )
        self.assertEqual(ok_element["flagged_rows"], [])

        markdown = report_to_markdown(report)
        self.assertIn("Dashboard health check: Test WB", markdown)
        self.assertIn("[FAIL]", markdown)
        self.assertIn("Broken Table", markdown)
        self.assertIn("Sample (first", markdown)

    def test_full_workflow_surfaces_error_tokens_and_flagged_rows(self) -> None:
        client = FakeHealthCheckClient(
            workbook={"name": "WB"},
            controls=[],
            pages=[{"pageId": "p1", "name": "Main"}],
            elements_by_page={
                "p1": [{
                    "elementId": "e_dirty",
                    "name": "Dirty Table",
                    "type": "table",
                    "columns": [{}],
                }],
            },
            export_outputs={
                "e_dirty": (b"a,b,c\n1,#DIV/0!,3\n2,ok,4\n3,,\n", None),
            },
        )
        report = health_check_workbook(client, "wb")
        element = report.pages[0].elements[0]
        self.assertEqual(element.status, "warn")
        flagged_reasons_combined = " ; ".join(
            r for fr in element.flagged_rows for r in fr.reasons
        )
        self.assertIn("error token", flagged_reasons_combined)

        payload = report_to_dict(report)
        flagged = payload["pages"][0]["elements"][0]["flagged_rows"]
        self.assertTrue(any("error token" in r for fr in flagged for r in fr["reasons"]))

        markdown = report_to_markdown(report)
        self.assertIn("Flagged rows", markdown)
        self.assertIn("#DIV/0!", markdown)

    def test_page_ids_filter_scopes_to_selected_pages(self) -> None:
        client = FakeHealthCheckClient(
            workbook={"name": "WB"},
            controls=[],
            pages=[
                {"pageId": "p_a", "name": "A"},
                {"pageId": "p_b", "name": "B"},
                {"pageId": "p_c", "name": "C"},
            ],
            elements_by_page={
                "p_a": [{"elementId": "e_a", "name": "A1", "type": "table", "columns": [{}]}],
                "p_b": [{"elementId": "e_b", "name": "B1", "type": "table", "columns": [{}]}],
                "p_c": [{"elementId": "e_c", "name": "C1", "type": "table", "columns": [{}]}],
            },
            export_outputs={
                "e_a": (b"x\n1\n", None),
                "e_b": (b"x\n1\n", None),
                "e_c": (b"x\n1\n", None),
            },
        )
        report = health_check_workbook(client, "wb", page_ids=["p_a", "p_c"])
        page_ids = [p.page_id for p in report.pages]
        self.assertEqual(page_ids, ["p_a", "p_c"])
        self.assertEqual(report.summary["elements_checked"], 2)

    def test_page_ids_filter_includes_explicitly_picked_hidden_page(self) -> None:
        client = FakeHealthCheckClient(
            workbook={"name": "WB"},
            controls=[],
            pages=[{"pageId": "p_hidden", "name": "H", "hidden": True}],
            elements_by_page={"p_hidden": [
                {"elementId": "e", "name": "n", "type": "table", "columns": [{}]},
            ]},
            export_outputs={"e": (b"x\n1\n", None)},
        )
        report = health_check_workbook(client, "wb", page_ids=["p_hidden"])
        self.assertEqual(report.summary["elements_checked"], 1)

    def test_page_ids_filter_rejects_unknown_id(self) -> None:
        client = FakeHealthCheckClient(
            workbook={}, controls=[],
            pages=[{"pageId": "p_a", "name": "A"}],
            elements_by_page={"p_a": []},
            export_outputs={},
        )
        with self.assertRaises(ValueError):
            health_check_workbook(client, "wb", page_ids=["p_a", "p_missing"])

    def test_skips_hidden_unless_opted_in(self) -> None:
        client = FakeHealthCheckClient(
            workbook={"name": "WB"},
            controls=[],
            pages=[{"pageId": "p_hidden", "name": "H", "hidden": True}],
            elements_by_page={"p_hidden": [
                {"elementId": "e", "name": "n", "type": "table", "columns": [{}]},
            ]},
            export_outputs={"e": (b"a\n1\n", None)},
        )
        report = health_check_workbook(client, "wb")
        self.assertEqual(report.summary["elements_checked"], 0)

        report_included = health_check_workbook(client, "wb", include_hidden_pages=True)
        self.assertEqual(report_included.summary["elements_checked"], 1)


class FakeHealthCheckClient:
    """Stand-in for SigmaAPIClient covering only the methods the healthcheck needs."""

    def __init__(
        self,
        workbook: Dict[str, Any],
        controls: List[Dict[str, Any]],
        pages: List[Dict[str, Any]],
        elements_by_page: Dict[str, List[Dict[str, Any]]],
        export_outputs: Dict[str, tuple],
    ) -> None:
        self._workbook = workbook
        self._controls = controls
        self._pages = pages
        self._elements_by_page = elements_by_page
        self._export_outputs = export_outputs
        self._pending_query_to_element: Dict[str, str] = {}
        self._next_query_id = 0

    def get_workbook(self, workbook_id: str) -> Dict[str, Any]:
        return self._workbook

    def list_workbook_controls(self, workbook_id: str) -> List[Dict[str, Any]]:
        return self._controls

    def list_workbook_pages(self, workbook_id: str) -> List[Dict[str, Any]]:
        return self._pages

    def list_workbook_elements(
        self,
        workbook_id: str,
        *,
        page_id: Optional[str] = None,
        **_: Any,
    ) -> List[Dict[str, Any]]:
        if page_id is None:
            return [e for els in self._elements_by_page.values() for e in els]
        return self._elements_by_page.get(page_id, [])

    def create_export(
        self,
        workbook_id: str,
        *,
        format_type: str,
        element_id: Optional[str] = None,
        **_: Any,
    ) -> str:
        if element_id is None:
            raise ValueError("element_id required for health-check fake")
        query_id = f"q_{self._next_query_id}"
        self._next_query_id += 1
        self._pending_query_to_element[query_id] = element_id
        raw, error = self._export_outputs.get(element_id, (b"", None))
        if error is not None:
            from sigma_api_toolkit.client import SigmaAPIError

            raise SigmaAPIError(error)
        return query_id

    def wait_for_download(self, query_id: str, **_: Any) -> bytes:
        element_id = self._pending_query_to_element.pop(query_id)
        raw, _error = self._export_outputs[element_id]
        return raw or b""


if __name__ == "__main__":
    unittest.main()
