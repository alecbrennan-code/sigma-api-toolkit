import unittest

from sigma_api_toolkit.client import SigmaAPIClient
from sigma_api_toolkit.config import SigmaConfig


class RecordingSigmaClient(SigmaAPIClient):
    def __init__(self) -> None:
        super().__init__(
            SigmaConfig(
                base_url="https://example.com",
                client_id="id",
                client_secret="secret",
            )
        )
        self.offsets = []
        self.downloads = iter(
            [
                b"col_a\n1\n2\n",
                b"col_a\n3\n4\n",
                b"col_a\n",
            ]
        )

    def create_export(self, workbook_id: str, **kwargs) -> str:  # type: ignore[override]
        self.offsets.append(kwargs.get("offset"))
        return f"query_{len(self.offsets)}"

    def wait_for_download(self, query_id: str, **kwargs) -> bytes:  # type: ignore[override]
        return next(self.downloads)


class ClientTest(unittest.TestCase):
    def test_chunk_offsets_follow_sigma_docs(self) -> None:
        client = RecordingSigmaClient()
        list(
            client.iter_export_chunks(
                "workbook_1",
                format_type="csv",
                element_id="element_1",
                chunk_size=2,
            )
        )
        self.assertEqual(client.offsets, [None, 3, 5])


if __name__ == "__main__":
    unittest.main()
