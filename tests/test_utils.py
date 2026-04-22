import unittest

from sigma_api_toolkit.utils import (
    normalize_workbook_ref,
    parse_control_arg,
    parse_control_args,
    parse_workbook_locator,
    slugify,
)


class UtilsTest(unittest.TestCase):
    def test_normalize_workbook_ref_accepts_url(self) -> None:
        self.assertEqual(
            normalize_workbook_ref(
                "https://app.sigmacomputing.com/flock-safety/workbook/6rXhGgU6qBXYotvQfKtIl1"
            ),
            "6rXhGgU6qBXYotvQfKtIl1",
        )

    def test_normalize_workbook_ref_accepts_named_url_slug(self) -> None:
        self.assertEqual(
            normalize_workbook_ref(
                "https://app.sigmacomputing.com/flock-safety/workbook/Account-Scoring-Query-5J9dDvF9eJ2BVBFkWxBI5f?:nodeId=a78KJC6YSe"
            ),
            "5J9dDvF9eJ2BVBFkWxBI5f",
        )

    def test_normalize_workbook_ref_accepts_raw_id(self) -> None:
        self.assertEqual(normalize_workbook_ref("abc123"), "abc123")

    def test_parse_workbook_locator_extracts_node_id(self) -> None:
        workbook_ref, node_id = parse_workbook_locator(
            "https://app.sigmacomputing.com/flock-safety/workbook/Account-Scoring-Query-5J9dDvF9eJ2BVBFkWxBI5f?:nodeId=a78KJC6YSe"
        )
        self.assertEqual(workbook_ref, "5J9dDvF9eJ2BVBFkWxBI5f")
        self.assertEqual(node_id, "a78KJC6YSe")

    def test_slugify(self) -> None:
        self.assertEqual(slugify("Course Properties Parsed"), "course-properties-parsed")

    def test_parse_control_arg_plain_string(self) -> None:
        self.assertEqual(
            parse_control_arg("Sales-Team=Major Markets 1"),
            ("Sales-Team", "Major Markets 1"),
        )

    def test_parse_control_arg_json_array(self) -> None:
        self.assertEqual(
            parse_control_arg('Sales-Team=["Major Markets 1","Major Markets 2"]'),
            ("Sales-Team", ["Major Markets 1", "Major Markets 2"]),
        )

    def test_parse_control_arg_boolean_and_number(self) -> None:
        self.assertEqual(parse_control_arg("Include-Closed=true"), ("Include-Closed", True))
        self.assertEqual(parse_control_arg("Min-ACV=50000"), ("Min-ACV", 50000))

    def test_parse_control_arg_value_with_equals(self) -> None:
        self.assertEqual(parse_control_arg("Where=a=b=c"), ("Where", "a=b=c"))

    def test_parse_control_arg_rejects_missing_equals(self) -> None:
        with self.assertRaises(ValueError):
            parse_control_arg("Sales-Team")

    def test_parse_control_args_merges(self) -> None:
        result = parse_control_args(
            ["Sales-Team=Major Markets 1", "Include-Closed=true"]
        )
        self.assertEqual(
            result, {"Sales-Team": "Major Markets 1", "Include-Closed": True}
        )


if __name__ == "__main__":
    unittest.main()
