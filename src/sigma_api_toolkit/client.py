from __future__ import annotations

from collections import deque
import time
from pathlib import Path
from typing import BinaryIO, Deque, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import requests

from sigma_api_toolkit.config import SigmaConfig


MAX_EXPORT_ROWS = 1_000_000
CHUNKABLE_FORMATS = {"csv", "json", "xlsx"}
MAX_RESULTS_VALIDITY_TIME_MS = 21_600_000


class SigmaAPIError(RuntimeError):
    pass


class SigmaAPIClient:
    def __init__(self, config: SigmaConfig):
        self.config = config
        self.session = requests.Session()
        self._access_token: Optional[str] = None
        self._token_expiry_epoch = 0.0

    def authenticate(self) -> str:
        auth_url = f"{self.config.base_url}/v2/auth/token"
        payload = {"grant_type": "client_credentials"}

        response = requests.post(
            auth_url,
            data=payload,
            auth=(self.config.client_id, self.config.client_secret),
            timeout=self.config.request_timeout_seconds,
        )
        if response.status_code >= 400:
            response = requests.post(
                auth_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                },
                timeout=self.config.request_timeout_seconds,
            )

        self._raise_for_status(response)
        data = response.json()
        self._access_token = data["access_token"]
        self._token_expiry_epoch = time.time() + max(data.get("expires_in", 3600) - 60, 0)
        return self._access_token

    def get_access_token(self) -> str:
        if not self._access_token or time.time() >= self._token_expiry_epoch:
            return self.authenticate()
        return self._access_token

    def get_workbook(self, workbook_id: str) -> Dict:
        return self.get(f"/v2/workbooks/{workbook_id}")

    def list_workbook_pages(self, workbook_id: str) -> List[Dict]:
        payload = self.get(f"/v2/workbooks/{workbook_id}/pages")
        return payload.get("entries", [])

    def list_workbook_elements(
        self,
        workbook_id: str,
        *,
        page_id: Optional[str] = None,
        tag_name: Optional[str] = None,
        bookmark_id: Optional[str] = None,
    ) -> List[Dict]:
        params: Dict[str, str] = {}
        if tag_name:
            params["tagName"] = tag_name
        if bookmark_id:
            params["bookmarkId"] = bookmark_id

        entries: List[Dict] = []
        next_page: Optional[str] = None
        path = f"/v2/workbooks/{workbook_id}/elements"
        if page_id:
            path = f"/v2/workbooks/{workbook_id}/pages/{page_id}/elements"
        while True:
            page_params = dict(params)
            if next_page:
                page_params["page"] = next_page
            payload = self.get(path, params=page_params)
            entries.extend(payload.get("entries", []))
            next_page = payload.get("nextPage")
            if not next_page:
                break
        return entries

    def list_element_columns(self, workbook_id: str, element_id: str) -> List[Dict]:
        payload = self.get(f"/v2/workbooks/{workbook_id}/elements/{element_id}/columns")
        return payload.get("entries", payload if isinstance(payload, list) else [])

    def list_workbook_controls(self, workbook_id: str) -> List[Dict]:
        """Return every control defined on the workbook with its name and valueType.

        Sigma exposes controls as write-only: the response here does not include
        the currently-selected value. Use this to discover which control names
        are available so callers can pass them into /export or /send.
        """
        entries: List[Dict] = []
        next_page: Optional[str] = None
        path = f"/v2/workbooks/{workbook_id}/controls"
        while True:
            params: Dict[str, str] = {}
            if next_page:
                params["page"] = next_page
            payload = self.get(path, params=params)
            entries.extend(payload.get("entries", []))
            next_page = payload.get("nextPage")
            if not next_page:
                break
        return entries

    def get(self, path: str, params: Optional[Dict] = None) -> Dict:
        response = self._request("GET", path, params=params)
        return response.json()

    def post(self, path: str, json: Optional[Dict] = None, params: Optional[Dict] = None) -> Dict:
        response = self._request("POST", path, json=json, params=params)
        return response.json()

    def create_export(
        self,
        workbook_id: str,
        *,
        format_type: str,
        element_id: Optional[str] = None,
        page_id: Optional[str] = None,
        row_limit: Optional[int] = None,
        offset: Optional[int] = None,
        results_validity_time_ms: Optional[int] = None,
        tag_name: Optional[str] = None,
        bookmark_id: Optional[str] = None,
        parameters: Optional[Dict[str, object]] = None,
    ) -> str:
        payload: Dict[str, object] = {"format": {"type": format_type}}
        params: Dict[str, str] = {}

        if element_id:
            payload["elementId"] = element_id
        if page_id:
            payload["pageId"] = page_id
        if row_limit is not None:
            payload["rowLimit"] = min(row_limit, MAX_EXPORT_ROWS)
        if offset is not None:
            payload["offset"] = offset
        if results_validity_time_ms is not None:
            payload["resultsValidityTimeMs"] = results_validity_time_ms
        if parameters:
            payload["parameters"] = dict(parameters)
        if tag_name:
            params["tagName"] = tag_name
        if bookmark_id:
            params["bookmarkId"] = bookmark_id

        response = self.post(f"/v2/workbooks/{workbook_id}/export", json=payload, params=params)
        query_id = response.get("queryId")
        if not query_id:
            raise SigmaAPIError("Sigma export response did not include queryId.")
        return query_id

    def send_export(self, workbook_id: str, *, request_body: Dict) -> Dict:
        return self.post(f"/v2/workbooks/{workbook_id}/send", json=request_body)

    def wait_for_download(
        self,
        query_id: str,
        *,
        poll_seconds: float = 2.0,
        timeout_seconds: float = 300.0,
    ) -> bytes:
        started_at = time.time()
        while True:
            response = self._request(
                "GET",
                f"/v2/query/{query_id}/download",
                stream=True,
                allow_retry_statuses={202, 204, 404, 502, 503, 504},
            )
            if response is not None and response.status_code == 200:
                return response.content

            if time.time() - started_at >= timeout_seconds:
                raise TimeoutError(
                    f"Timed out waiting for Sigma export download for queryId={query_id}"
                )
            time.sleep(poll_seconds)

    def iter_export_chunks(
        self,
        workbook_id: str,
        *,
        format_type: str,
        element_id: Optional[str] = None,
        page_id: Optional[str] = None,
        chunk_size: Optional[int] = None,
        start_offset: Optional[int] = None,
        poll_seconds: float = 2.0,
        timeout_seconds: float = 300.0,
        results_validity_time_ms: Optional[int] = None,
        csv_overlap_rows: int = 0,
        csv_resume_tail_records: Optional[Sequence[bytes]] = None,
        tag_name: Optional[str] = None,
        bookmark_id: Optional[str] = None,
        parameters: Optional[Dict[str, object]] = None,
    ) -> Iterator[bytes]:
        if chunk_size and format_type in CHUNKABLE_FORMATS:
            if format_type == "csv" and csv_overlap_rows >= chunk_size:
                raise ValueError("--chunk-overlap-rows must be smaller than the chunk size")

            if results_validity_time_ms is None:
                results_validity_time_ms = MAX_RESULTS_VALIDITY_TIME_MS

            next_offset: Optional[int] = start_offset
            next_start_row = start_offset if start_offset is not None else 1
            previous_tail_records: List[bytes] = (
                list(csv_resume_tail_records) if csv_resume_tail_records else []
            )
            while True:
                query_id = self.create_export(
                    workbook_id,
                    format_type=format_type,
                    element_id=element_id,
                    page_id=page_id,
                    row_limit=chunk_size,
                    offset=next_offset,
                    results_validity_time_ms=results_validity_time_ms,
                    tag_name=tag_name,
                    bookmark_id=bookmark_id,
                    parameters=parameters,
                )
                raw = self.wait_for_download(
                    query_id,
                    poll_seconds=poll_seconds,
                    timeout_seconds=timeout_seconds,
                )
                if not raw:
                    break
                if format_type == "csv" and _csv_is_header_only(raw):
                    break

                original_row_count = _csv_data_row_count(raw) if format_type == "csv" else None
                if format_type == "csv" and csv_overlap_rows and previous_tail_records:
                    actual_overlap_rows = min(
                        csv_overlap_rows,
                        len(previous_tail_records),
                        original_row_count or 0,
                    )
                    expected_overlap = previous_tail_records[-actual_overlap_rows:]
                    observed_overlap = _csv_first_data_records(raw, actual_overlap_rows)
                    if expected_overlap != observed_overlap:
                        current_offset = next_offset if next_offset is not None else 1
                        raise SigmaAPIError(
                            "CSV chunk boundary validation failed. "
                            f"Expected the next chunk at offset={current_offset} to begin with "
                            f"{actual_overlap_rows} repeated rows from the prior chunk, but Sigma "
                            "returned a different boundary. This usually means offset-based chunking "
                            "is walking an unstable row order. Add a deterministic ORDER BY to the "
                            "source query, export smaller filtered slices so the download does not "
                            "need chunking, or switch to the toolkit's send-export flow so Sigma can "
                            "deliver one stable export to a destination without client-side chunking."
                        )
                    raw = _drop_first_data_records_preserve_header(raw, actual_overlap_rows)
                    if _csv_is_header_only(raw):
                        break

                yield raw
                if format_type == "csv" and (original_row_count or 0) < chunk_size:
                    break
                if format_type == "csv" and csv_overlap_rows:
                    previous_tail_records = _csv_last_data_records(raw, csv_overlap_rows)
                    next_start_row += chunk_size - csv_overlap_rows
                else:
                    next_start_row += chunk_size
                next_offset = next_start_row
        else:
            query_id = self.create_export(
                workbook_id,
                format_type=format_type,
                element_id=element_id,
                page_id=page_id,
                results_validity_time_ms=results_validity_time_ms,
                tag_name=tag_name,
                bookmark_id=bookmark_id,
                parameters=parameters,
            )
            yield self.wait_for_download(
                query_id,
                poll_seconds=poll_seconds,
                timeout_seconds=timeout_seconds,
            )

    def export_to_file(
        self,
        output_path: Path,
        workbook_id: str,
        *,
        format_type: str,
        element_id: Optional[str] = None,
        page_id: Optional[str] = None,
        chunk_size: Optional[int] = None,
        overwrite: bool = False,
        poll_seconds: float = 2.0,
        timeout_seconds: float = 300.0,
        results_validity_time_ms: Optional[int] = None,
        csv_overlap_rows: int = 0,
        tag_name: Optional[str] = None,
        bookmark_id: Optional[str] = None,
        parameters: Optional[Dict[str, object]] = None,
    ) -> int:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists() and not overwrite:
            raise FileExistsError(
                f"{output_path} already exists. Re-run with overwrite enabled to replace it."
            )

        total_bytes = 0
        first_chunk = True
        for chunk in self.iter_export_chunks(
            workbook_id,
            format_type=format_type,
            element_id=element_id,
            page_id=page_id,
            chunk_size=chunk_size,
            poll_seconds=poll_seconds,
            timeout_seconds=timeout_seconds,
            results_validity_time_ms=results_validity_time_ms,
            csv_overlap_rows=csv_overlap_rows,
            tag_name=tag_name,
            bookmark_id=bookmark_id,
            parameters=parameters,
        ):
            data = chunk
            if format_type == "csv" and not first_chunk:
                data = _strip_first_line(data)
            mode = "wb" if first_chunk else "ab"
            with output_path.open(mode) as handle:
                handle.write(data)
            total_bytes += len(data)
            first_chunk = False

        return total_bytes

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict] = None,
        json: Optional[Dict] = None,
        stream: bool = False,
        allow_retry_statuses: Optional[Iterable[int]] = None,
    ) -> Optional[requests.Response]:
        url = f"{self.config.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.get_access_token()}"}
        if json is not None:
            headers["Content-Type"] = "application/json"

        response = self.session.request(
            method,
            url,
            params=params,
            json=json,
            headers=headers,
            timeout=self.config.request_timeout_seconds,
            stream=stream,
        )

        if allow_retry_statuses and response.status_code in set(allow_retry_statuses):
            return None

        self._raise_for_status(response)
        return response

    @staticmethod
    def _raise_for_status(response: requests.Response) -> None:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            body = response.text.strip()
            detail = f"{response.status_code} {response.reason}"
            if body:
                detail = f"{detail}: {body[:500]}"
            raise SigmaAPIError(detail) from exc


def _csv_is_header_only(raw: bytes) -> bool:
    return _csv_record_count(raw) <= 1


def _csv_data_row_count(raw: bytes) -> int:
    return max(_csv_record_count(raw) - 1, 0)


def _strip_first_line(raw: bytes) -> bytes:
    return _drop_first_csv_records(raw, 1)


def _csv_record_count(raw: bytes) -> int:
    return sum(1 for _ in _iter_csv_record_spans(raw))


def _csv_first_data_records(raw: bytes, count: int) -> List[bytes]:
    if count <= 0:
        return []

    records: List[bytes] = []
    for index, (start, end) in enumerate(_iter_csv_record_spans(raw)):
        if index == 0:
            continue
        records.append(raw[start:end])
        if len(records) >= count:
            break
    return records


def _csv_last_data_records(raw: bytes, count: int) -> List[bytes]:
    if count <= 0:
        return []

    tail: Deque[bytes] = deque(maxlen=count)
    for index, (start, end) in enumerate(_iter_csv_record_spans(raw)):
        if index == 0:
            continue
        tail.append(raw[start:end])
    return list(tail)


def _drop_first_data_records_preserve_header(raw: bytes, count: int) -> bytes:
    if count <= 0:
        return raw

    spans = list(_iter_csv_record_spans(raw))
    if not spans:
        return raw

    header_start, header_end = spans[0]
    kept_start = header_end
    data_spans = spans[1:]
    if not data_spans:
        return raw[header_start:header_end]

    if count >= len(data_spans):
        return raw[header_start:header_end]

    kept_start = data_spans[count][0]
    return raw[header_start:header_end] + raw[kept_start:]


def _drop_first_csv_records(raw: bytes, count: int) -> bytes:
    if count <= 0:
        return raw

    seen = 0
    for _, end in _iter_csv_record_spans(raw):
        seen += 1
        if seen == count:
            return raw[end:]
    return b""


def _iter_csv_record_spans(raw: bytes) -> Iterator[Tuple[int, int]]:
    start = 0
    index = 0
    in_quotes = False

    while index < len(raw):
        current = raw[index]
        if current == 34:
            if in_quotes and index + 1 < len(raw) and raw[index + 1] == 34:
                index += 2
                continue
            in_quotes = not in_quotes
        elif current == 10 and not in_quotes:
            yield (start, index + 1)
            start = index + 1
        index += 1

    if start < len(raw):
        yield (start, len(raw))


class _CsvRecordStream:
    """Stateful CSV parser that yields complete records as bytes arrive.

    Tracks quote state across `feed` calls so records with embedded newlines
    still parse correctly when the file is streamed in chunks.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._scan_index = 0
        self._record_start = 0
        self._in_quotes = False

    def feed(self, data: bytes) -> Iterator[bytes]:
        self._buffer.extend(data)
        while self._scan_index < len(self._buffer):
            byte = self._buffer[self._scan_index]
            if byte == 34:
                if (
                    self._in_quotes
                    and self._scan_index + 1 < len(self._buffer)
                    and self._buffer[self._scan_index + 1] == 34
                ):
                    self._scan_index += 2
                    continue
                self._in_quotes = not self._in_quotes
            elif byte == 10 and not self._in_quotes:
                end = self._scan_index + 1
                record = bytes(self._buffer[self._record_start:end])
                self._scan_index = end
                self._record_start = end
                yield record
                continue
            self._scan_index += 1

        if self._record_start > 0:
            del self._buffer[:self._record_start]
            self._scan_index -= self._record_start
            self._record_start = 0

    def flush(self) -> Optional[bytes]:
        if self._record_start < len(self._buffer):
            record = bytes(self._buffer[self._record_start:])
            self._record_start = len(self._buffer)
            return record
        return None


def count_csv_data_records(path: Path, *, read_chunk_bytes: int = 1 << 20) -> int:
    """Return the number of data records (header excluded) in a CSV file.

    Streams the file and respects quoted embedded newlines so the count
    matches what a CSV parser would report.
    """
    stream = _CsvRecordStream()
    total = 0
    with path.open("rb") as handle:
        while True:
            block = handle.read(read_chunk_bytes)
            if not block:
                break
            for _ in stream.feed(block):
                total += 1
    if stream.flush() is not None:
        total += 1
    return max(total - 1, 0)


def read_last_csv_data_records(
    path: Path,
    count: int,
    *,
    read_chunk_bytes: int = 1 << 20,
) -> List[bytes]:
    """Return the last `count` data records (header excluded) from a CSV file.

    Streams the file so memory usage stays proportional to `count` plus one
    read buffer, which lets this safely read the tail of a multi-GB export.
    Records are returned byte-exact so they can be compared against the head
    of a resumed Sigma chunk.
    """
    if count <= 0:
        return []

    tail: Deque[bytes] = deque(maxlen=count)
    stream = _CsvRecordStream()
    header_seen = False
    with path.open("rb") as handle:
        while True:
            block = handle.read(read_chunk_bytes)
            if not block:
                break
            for record in stream.feed(block):
                if not header_seen:
                    header_seen = True
                    continue
                tail.append(record)

    remainder = stream.flush()
    if remainder is not None:
        if not header_seen:
            header_seen = True
        else:
            tail.append(remainder)

    return list(tail)
