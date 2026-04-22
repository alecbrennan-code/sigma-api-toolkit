import tempfile
import unittest
from pathlib import Path

from sigma_api_toolkit.client import (
    SigmaAPIClient,
    SigmaAPIError,
    _csv_data_row_count,
    _strip_first_line,
    count_csv_data_records,
    read_last_csv_data_records,
)
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


class ShortFinalChunkSigmaClient(RecordingSigmaClient):
    def __init__(self) -> None:
        super().__init__()
        self.downloads = iter(
            [
                b"col_a\n1\n",
            ]
        )


class OverlapValidatedSigmaClient(RecordingSigmaClient):
    def __init__(self) -> None:
        super().__init__()
        self.downloads = iter(
            [
                b"col_a\n1\n2\n",
                b"col_a\n2\n3\n",
                b"col_a\n3\n",
            ]
        )


class BoundaryMismatchSigmaClient(RecordingSigmaClient):
    def __init__(self) -> None:
        super().__init__()
        self.downloads = iter(
            [
                b"col_a\n1\n2\n",
                b"col_a\n9\n3\n",
            ]
        )


class ResumeOverlapSigmaClient(RecordingSigmaClient):
    def __init__(self) -> None:
        super().__init__()
        self.downloads = iter(
            [
                b"col_a\nseed_row\nnew1\nnew2\n",
                b"col_a\nnew2\nnew3\n",
            ]
        )


class ResumeMismatchSigmaClient(RecordingSigmaClient):
    def __init__(self) -> None:
        super().__init__()
        self.downloads = iter(
            [
                b"col_a\nwrong_row\nnew1\nnew2\n",
            ]
        )


class SendSigmaClient(SigmaAPIClient):
    def __init__(self) -> None:
        super().__init__(
            SigmaConfig(
                base_url="https://example.com",
                client_id="id",
                client_secret="secret",
            )
        )
        self.path = None
        self.payload = None

    def post(self, path: str, json=None, params=None):  # type: ignore[override]
        self.path = path
        self.payload = json
        return {"status": "accepted"}


class ClientTest(unittest.TestCase):
    def test_chunk_offsets_follow_sigma_docs(self) -> None:
        client = RecordingSigmaClient()
        list(
            client.iter_export_chunks(
                "workbook_1",
                format_type="csv",
                element_id="element_1",
                chunk_size=2,
                csv_overlap_rows=0,
            )
        )
        self.assertEqual(client.offsets, [None, 3, 5])

    def test_short_csv_chunk_stops_without_follow_up_export(self) -> None:
        client = ShortFinalChunkSigmaClient()
        chunks = list(
            client.iter_export_chunks(
                "workbook_1",
                format_type="csv",
                element_id="element_1",
                chunk_size=2,
                csv_overlap_rows=0,
            )
        )
        self.assertEqual(len(chunks), 1)
        self.assertEqual(client.offsets, [None])

    def test_csv_overlap_validation_deduplicates_boundary_rows(self) -> None:
        client = OverlapValidatedSigmaClient()
        chunks = list(
            client.iter_export_chunks(
                "workbook_1",
                format_type="csv",
                element_id="element_1",
                chunk_size=2,
                csv_overlap_rows=1,
            )
        )
        self.assertEqual(client.offsets, [None, 2, 3])
        combined = chunks[0] + _strip_first_line(chunks[1])
        self.assertEqual(combined, b"col_a\n1\n2\n3\n")

    def test_csv_overlap_validation_fails_on_unstable_boundary(self) -> None:
        client = BoundaryMismatchSigmaClient()
        with self.assertRaises(SigmaAPIError):
            list(
                client.iter_export_chunks(
                    "workbook_1",
                    format_type="csv",
                    element_id="element_1",
                    chunk_size=2,
                    csv_overlap_rows=1,
                )
            )

    def test_csv_row_count_handles_embedded_newlines(self) -> None:
        raw = b'col_a,col_b\n1,"two\nlines"\n3,4\n'
        self.assertEqual(_csv_data_row_count(raw), 2)

    def test_csv_resume_tail_seed_validates_first_chunk(self) -> None:
        client = ResumeOverlapSigmaClient()
        chunks = list(
            client.iter_export_chunks(
                "workbook_1",
                format_type="csv",
                element_id="element_1",
                chunk_size=3,
                csv_overlap_rows=1,
                csv_resume_tail_records=[b"seed_row\n"],
                start_offset=2,
            )
        )
        self.assertEqual(client.offsets, [2, 4])
        self.assertEqual(chunks[0], b"col_a\nnew1\nnew2\n")
        self.assertEqual(chunks[1], b"col_a\nnew3\n")

    def test_csv_resume_tail_mismatch_raises(self) -> None:
        client = ResumeMismatchSigmaClient()
        with self.assertRaises(SigmaAPIError):
            list(
                client.iter_export_chunks(
                    "workbook_1",
                    format_type="csv",
                    element_id="element_1",
                    chunk_size=3,
                    csv_overlap_rows=1,
                    csv_resume_tail_records=[b"expected_seed\n"],
                    start_offset=2,
                )
            )

    def test_read_last_csv_data_records_returns_tail(self) -> None:
        with tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False
        ) as handle:
            handle.write(b"col_a,col_b\n1,a\n2,b\n3,c\n4,d\n5,e\n")
            path = Path(handle.name)
        try:
            self.assertEqual(
                read_last_csv_data_records(path, 2),
                [b"4,d\n", b"5,e\n"],
            )
        finally:
            path.unlink()

    def test_read_last_csv_data_records_crosses_read_buffer(self) -> None:
        with tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False
        ) as handle:
            handle.write(b'col_a,col_b\n1,"two\nlines"\n3,d\n')
            path = Path(handle.name)
        try:
            self.assertEqual(
                read_last_csv_data_records(path, 2, read_chunk_bytes=4),
                [b'1,"two\nlines"\n', b"3,d\n"],
            )
        finally:
            path.unlink()

    def test_count_csv_data_records_respects_embedded_newlines(self) -> None:
        with tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False
        ) as handle:
            handle.write(b'col_a,col_b\n1,"embedded\nnewline"\n3,d\n')
            path = Path(handle.name)
        try:
            self.assertEqual(count_csv_data_records(path), 2)
        finally:
            path.unlink()

    def test_send_export_posts_to_send_endpoint(self) -> None:
        client = SendSigmaClient()
        response = client.send_export(
            "workbook_1",
            request_body={"targets": [{"type": "webhook", "webhookUrl": "https://example.com"}]},
        )
        self.assertEqual(client.path, "/v2/workbooks/workbook_1/send")
        self.assertEqual(
            client.payload,
            {"targets": [{"type": "webhook", "webhookUrl": "https://example.com"}]},
        )
        self.assertEqual(response, {"status": "accepted"})


if __name__ == "__main__":
    unittest.main()
