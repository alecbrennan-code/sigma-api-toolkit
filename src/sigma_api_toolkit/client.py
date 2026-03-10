from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional

import requests

from sigma_api_toolkit.config import SigmaConfig


MAX_EXPORT_ROWS = 1_000_000
CHUNKABLE_FORMATS = {"csv", "json", "xlsx"}


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
        if tag_name:
            params["tagName"] = tag_name
        if bookmark_id:
            params["bookmarkId"] = bookmark_id

        response = self.post(f"/v2/workbooks/{workbook_id}/export", json=payload, params=params)
        query_id = response.get("queryId")
        if not query_id:
            raise SigmaAPIError("Sigma export response did not include queryId.")
        return query_id

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
        tag_name: Optional[str] = None,
        bookmark_id: Optional[str] = None,
    ) -> Iterator[bytes]:
        if chunk_size and format_type in CHUNKABLE_FORMATS:
            next_offset: Optional[int] = start_offset
            next_start_row = start_offset if start_offset is not None else 1
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
                yield raw
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
        tag_name: Optional[str] = None,
        bookmark_id: Optional[str] = None,
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
            tag_name=tag_name,
            bookmark_id=bookmark_id,
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
    lines = raw.strip().splitlines()
    return len(lines) <= 1


def _strip_first_line(raw: bytes) -> bytes:
    newline_index = raw.find(b"\n")
    if newline_index == -1:
        return b""
    return raw[newline_index + 1 :]
