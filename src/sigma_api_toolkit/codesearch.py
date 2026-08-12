"""Search and snapshot Sigma workbook *definitions* (custom SQL + element queries).

Why this exists
---------------
Sigma's REST API does not expose per-version edit history. There is no
``/v2/workbooks/{id}/versions`` endpoint, and the ``workbookVersion`` query
param is silently ignored on ``/elements``, ``/queries``, and ``/lineage``
unless the workbook has *version tags* — an invalid value like ``abc`` still
returns HTTP 200 with current content. See ``docs/version-history.md``.

So attribution of "who added this line" cannot come from the API. What the API
*can* do is tell you exactly where a string lives right now, across every
workbook, and — if you snapshot on a schedule — when it appeared and who last
touched the workbook in that window.

Two modes:

``search``   grep the current definition of one or all workbooks for a string.
``snapshot`` write a workbook's definition to a timestamped JSON file so that
             successive runs can be diffed to bracket when a change landed.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence

from sigma_api_toolkit.client import SigmaAPIClient, SigmaAPIError

# Sigma caps page size well above this, but 500 keeps single responses sane
# while cutting the round-trips on wide workbooks (some have 1000+ elements).
PAGE_SIZE = 500

# Sigma sits behind Cloudflare and rate-limits an org-wide sweep hard. Probing
# 498 workbooks with 8 threads got 216 of them 429'd, and kept going until even
# /v2/auth/token was refused with a Cloudflare block page — which locks out the
# whole toolkit, not just the sweep. So: serial requests, a pause between
# workbooks, and exponential backoff on 429.
INTER_WORKBOOK_PAUSE_SECONDS = 0.35
RATE_LIMIT_MAX_ATTEMPTS = 5
RATE_LIMIT_BASE_DELAY_SECONDS = 2.0


def _is_rate_limited(exc: Exception) -> bool:
    return "429" in str(exc)


def _get_with_backoff(client: SigmaAPIClient, path: str, params: Dict[str, str]) -> Dict:
    """GET with exponential backoff on 429.

    Anything other than a rate-limit is re-raised immediately: a 400 on
    ``/lineage`` is a permanent property of that workbook, and retrying it just
    burns quota that the rest of the sweep needs.
    """
    delay = RATE_LIMIT_BASE_DELAY_SECONDS
    last: Optional[Exception] = None
    for _ in range(RATE_LIMIT_MAX_ATTEMPTS):
        try:
            return client.get(path, params=params)
        except SigmaAPIError as exc:
            if not _is_rate_limited(exc):
                raise
            last = exc
            time.sleep(delay)
            delay *= 2
    raise SigmaAPIError(
        f"Rate limited by Sigma after {RATE_LIMIT_MAX_ATTEMPTS} attempts on {path}. "
        f"Wait a few minutes before retrying. Last error: {last}"
    )


@dataclass
class Hit:
    """One matching line inside one definition."""

    workbook_url_id: str
    workbook_name: str
    source: str  # "customSQL" | "query"
    node: str  # lineage node name, or elementId for queries
    element_name: Optional[str]
    line_no: int
    line: str

    def as_dict(self) -> Dict:
        return {
            "workbook_url_id": self.workbook_url_id,
            "workbook_name": self.workbook_name,
            "source": self.source,
            "node": self.node,
            "element_name": self.element_name,
            "line_no": self.line_no,
            "line": self.line,
        }


@dataclass
class WorkbookDefinition:
    """Everything the API will tell us about a workbook's current definition."""

    workbook_url_id: str
    name: str
    latest_version: Optional[int]
    updated_at: Optional[str]
    updated_by: Optional[str]
    lineage: List[Dict] = field(default_factory=list)
    queries: List[Dict] = field(default_factory=list)


def _paginate(client: SigmaAPIClient, path: str) -> Iterator[Dict]:
    """Walk Sigma's cursor pagination, yielding entries.

    Sigma returns an opaque, already-URL-encoded ``nextPage`` cursor. It is
    passed straight back through so it is never double-encoded.
    """
    next_page: Optional[str] = None
    while True:
        params: Dict[str, str] = {"limit": str(PAGE_SIZE)}
        if next_page:
            params["page"] = next_page
        payload = _get_with_backoff(client, path, params)
        for entry in payload.get("entries", []):
            yield entry
        next_page = payload.get("nextPage")
        if not next_page:
            break


def fetch_definition(
    client: SigmaAPIClient,
    workbook_url_id: str,
    *,
    include_queries: bool = True,
) -> WorkbookDefinition:
    """Pull a workbook's metadata, lineage, and (optionally) element queries.

    ``lineage`` is where hand-written custom SQL lives, so it is the only part
    needed to find source-level text. ``queries`` returns Sigma's *generated*
    SQL, which is both much slower to fetch and non-deterministic between calls
    (column aliases are regenerated), so it is opt-out for search and excluded
    from snapshots.
    """
    meta = client.get_workbook(workbook_url_id)
    definition = WorkbookDefinition(
        workbook_url_id=workbook_url_id,
        name=meta.get("name", ""),
        latest_version=meta.get("latestVersion"),
        updated_at=meta.get("updatedAt"),
        updated_by=meta.get("updatedBy"),
        lineage=list(_paginate(client, f"/v2/workbooks/{workbook_url_id}/lineage")),
    )
    if include_queries:
        definition.queries = list(
            _paginate(client, f"/v2/workbooks/{workbook_url_id}/queries")
        )
    return definition


def _element_names(definition: WorkbookDefinition) -> Dict[str, str]:
    return {
        q.get("elementId", ""): q.get("name", "")
        for q in definition.queries
        if q.get("elementId")
    }


def search_definition(
    definition: WorkbookDefinition,
    pattern: str,
    *,
    regex: bool = False,
    ignore_case: bool = True,
) -> List[Hit]:
    """Return every line in the workbook's definition matching ``pattern``."""
    flags = re.IGNORECASE if ignore_case else 0
    needle = re.compile(pattern if regex else re.escape(pattern), flags)
    names = _element_names(definition)
    hits: List[Hit] = []

    def scan(text: str, source: str, node: str, element_name: Optional[str]) -> None:
        if not text:
            return
        for idx, line in enumerate(text.splitlines(), start=1):
            if needle.search(line):
                hits.append(
                    Hit(
                        workbook_url_id=definition.workbook_url_id,
                        workbook_name=definition.name,
                        source=source,
                        node=node,
                        element_name=element_name,
                        line_no=idx,
                        line=line.strip(),
                    )
                )

    for node in definition.lineage:
        scan(
            node.get("definition", ""),
            node.get("type", "lineage") or "lineage",
            node.get("name", "") or "",
            None,
        )
    for query in definition.queries:
        element_id = query.get("elementId", "") or ""
        scan(query.get("sql", ""), "query", element_id, names.get(element_id))

    return hits


def list_workbooks(client: SigmaAPIClient) -> List[Dict]:
    return list(_paginate(client, "/v2/workbooks"))


def search_workbooks(
    client: SigmaAPIClient,
    pattern: str,
    *,
    workbook_url_ids: Optional[Sequence[str]] = None,
    regex: bool = False,
    ignore_case: bool = True,
    include_queries: bool = False,
    on_progress=None,
    skipped_out: Optional[List[str]] = None,
) -> List[Hit]:
    """Search one, several, or all workbooks for ``pattern``.

    Defaults to lineage-only (``include_queries=False``) because an org-wide
    sweep with generated SQL turned on means hundreds of slow calls for text
    that, being generated, was never typed by a person anyway.

    Pass a list as ``skipped_out`` to receive the workbooks that could not be
    read. Always check it before trusting a no-match result.
    """
    if workbook_url_ids:
        targets = [{"workbookUrlId": wid, "name": ""} for wid in workbook_url_ids]
    else:
        targets = list_workbooks(client)

    hits: List[Hit] = []
    skipped: List[str] = []
    for index, workbook in enumerate(targets, start=1):
        url_id = workbook.get("workbookUrlId")
        if not url_id:
            continue
        if on_progress:
            on_progress(index, len(targets), workbook.get("name") or url_id)
        try:
            definition = fetch_definition(
                client, url_id, include_queries=include_queries
            )
        except Exception as exc:
            # ~1 in 3 workbooks return a permanent 400 on /lineage. One
            # unreadable workbook must never abort a sweep of hundreds, but the
            # count has to surface — a silent skip looks identical to "no match"
            # and would turn an incomplete sweep into a false negative.
            skipped.append(f"{workbook.get('name') or url_id} ({url_id}): {exc}")
            if on_progress:
                on_progress(index, len(targets), f"SKIP {url_id}: {exc}")
            continue
        hits.extend(
            search_definition(
                definition, pattern, regex=regex, ignore_case=ignore_case
            )
        )
        if len(targets) > 1:
            time.sleep(INTER_WORKBOOK_PAUSE_SECONDS)

    if skipped_out is not None:
        skipped_out.extend(skipped)
    return hits


def snapshot_definition(
    client: SigmaAPIClient,
    workbook_url_id: str,
    out_dir: Path,
) -> Path:
    """Write the workbook's current custom SQL to a timestamped snapshot file.

    Diffing two snapshots brackets a change between two collection times; the
    workbook's ``updatedBy`` in each snapshot names the last editor as of that
    moment. That is the closest the API gets to attribution — it is a real
    signal on a daily cadence, and a guess on a monthly one.
    """
    definition = fetch_definition(client, workbook_url_id, include_queries=False)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{workbook_url_id}__{stamp}.json"
    payload = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "workbook_url_id": definition.workbook_url_id,
        "name": definition.name,
        "latest_version": definition.latest_version,
        "updated_at": definition.updated_at,
        "updated_by": definition.updated_by,
        # Only nodes carrying hand-written SQL — the rest is Sigma-managed
        # plumbing that churns without anyone editing anything.
        "custom_sql": {
            node.get("name", ""): node.get("definition", "")
            for node in definition.lineage
            if node.get("definition")
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return path


def diff_snapshots(old_path: Path, new_path: Path) -> Dict[str, List[str]]:
    """Compare two snapshot files, reporting which custom-SQL nodes changed."""
    old = json.loads(old_path.read_text())
    new = json.loads(new_path.read_text())
    old_sql: Dict[str, str] = old.get("custom_sql", {})
    new_sql: Dict[str, str] = new.get("custom_sql", {})
    return {
        "added": sorted(set(new_sql) - set(old_sql)),
        "removed": sorted(set(old_sql) - set(new_sql)),
        "changed": sorted(
            k for k in set(old_sql) & set(new_sql) if old_sql[k] != new_sql[k]
        ),
    }


def resolve_member_names(client: SigmaAPIClient) -> Dict[str, str]:
    """Map memberId -> "First Last <email>" so ``updated_by`` is readable."""
    members = {}
    for member in _paginate(client, "/v2/members"):
        member_id = member.get("memberId")
        if not member_id:
            continue
        name = " ".join(
            part for part in (member.get("firstName"), member.get("lastName")) if part
        )
        email = member.get("email") or ""
        members[member_id] = f"{name} <{email}>".strip()
    return members
