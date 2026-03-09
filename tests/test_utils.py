import unittest

from sigma_api_toolkit.utils import normalize_workbook_ref, slugify


class UtilsTest(unittest.TestCase):
    def test_normalize_workbook_ref_accepts_url(self) -> None:
        self.assertEqual(
            normalize_workbook_ref(
                "https://app.sigmacomputing.com/flock-safety/workbook/6rXhGgU6qBXYotvQfKtIl1"
            ),
            "6rXhGgU6qBXYotvQfKtIl1",
        )

    def test_normalize_workbook_ref_accepts_raw_id(self) -> None:
        self.assertEqual(normalize_workbook_ref("abc123"), "abc123")

    def test_slugify(self) -> None:
        self.assertEqual(slugify("Course Properties Parsed"), "course-properties-parsed")


if __name__ == "__main__":
    unittest.main()

