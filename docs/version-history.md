# Sigma workbook version history via the REST API

**Short answer: you cannot get it.** Sigma's REST API exposes only a workbook's
*current* definition. There is no way to retrieve a past version's content, and
no way to learn which member authored a given version.

This document records the probes behind that conclusion so nobody has to redo
them. Probed 2026-08-11 against `https://aws-api.sigmacomputing.com`,
workbook `1TWwiPtRVv4EkG7aBjXFJS` (`latestVersion: 492`).

## No version-listing endpoint

All of these return **404**:

```
/v2/workbooks/{id}/versions
/v2/workbooks/{id}/versionHistory
/v2/workbooks/{id}/history
/v2/workbooks/{id}/revisions
/v2/workbooks/{id}/versionTags
/v1/workbooks/{id}/versions
/v2/auditLogs   /v2/audit   /v2/activity   /v2/activityLogs   /v2/queryHistory
```

`GET /v2/workbooks/{id}` returns `latestVersion` (an integer), which proves
Sigma tracks versions internally — but it is the only version field exposed.

## `workbookVersion` is silently ignored

Several endpoints accept a `workbookVersion` query param. On an untagged
workbook it is **accepted and ignored** — it does not error, it returns current
content:

| `workbookVersion` | `/v2/workbooks/{id}/elements` |
|---|---|
| *(omitted)* | 200, sha `4121cee068d5` |
| `1` | 200, sha `4121cee068d5` |
| `492` | 200, sha `4121cee068d5` |
| `99999` | 200, sha `4121cee068d5` |
| `-5` | 200, sha `4121cee068d5` |
| `abc` | 200, sha `4121cee068d5` |

Byte-identical responses for a nonexistent version, a negative version, and a
non-numeric string. The param resolves only against **version tags**, and this
workbook has none (`/v2/workbooks/{id}/tags` → `total: 0`). Sigma does not
report the fallback.

`/lineage` behaves the same way: the custom-SQL node `yTT-UL_bPV` returned an
identical 10,356-character definition at `workbookVersion` 1, 300, and 492.

### The `/queries` false positive

`/v2/workbooks/{id}/queries?workbookVersion=N` *appears* to vary by version —
different response hashes per `N`. It does not. **Two consecutive calls at the
same version also differ**, because Sigma regenerates column aliases
(`ZN_269`, `DIV_266`, …) on each call. Any bisect built on hashing `/queries`
will chase noise. `/lineage` is the deterministic endpoint; `snapshot-definition`
uses it and excludes `/queries` for exactly this reason.

## Where hand-written SQL actually lives

`/lineage` (paginated, 1,240 entries on this workbook; 48 carry a
`definition`). Nodes with `"type": "customSQL"` hold the SQL a person typed.
Note `/lineage` defaults to 50 entries per page — an unpaginated call misses
almost everything.

Generated element SQL in `/queries` does **not** contain source custom SQL when
the source is materialized, so searching `/queries` alone can produce a false
negative.

## What to use instead

1. **`search-code`** — find where a string lives now, across one workbook or all
   of them. Answers "where", not "who".
2. **`snapshot-definition`** on a schedule — diff consecutive snapshots to
   bracket when a change landed. Attribution is the workbook's `updatedBy` at
   snapshot time, so it is only as precise as the cadence, and it names the last
   editor of the *workbook*, not of the specific line.
3. **Sigma UI version history** — the workbook's Version History panel does show
   per-version authors. This is the only reliable source for "who added this
   line", and it is UI-only.
4. **Sigma Audit Logs** — `GET /v2/connections` lists a connection named
   `Sigma Audit Logs` with `"isAuditLog": true`. It points at a **Sigma-hosted**
   Snowflake account (`wib96079`, warehouse `AUDIT_LOG_WH`), *not* Flock's own
   account, so `snow -c flock` cannot reach it; there is no `SIGMA_*` database in
   Flock's Snowflake. Reaching it means building a Sigma dataset/workbook on that
   connection and exporting it with this toolkit. Sigma documents the logs as
   `SIGMA_SHARED.AUDIT_LOGS` recording "who did what and when" — **whether they
   include workbook edit/version events at the granularity needed for line-level
   attribution is unverified.** Validate before building on it.

## Two limits that cap the org-wide sweep

Both were found the hard way while benchmarking a 498-workbook sweep.

### `/lineage` returns a permanent 400 for many workbooks

`GET /v2/workbooks/{id}` succeeds while `GET /v2/workbooks/{id}/lineage` returns
`400 invalid_request` with only a generic incident ID. It is not a pagination or
`limit` artifact — it fails identically with `limit=500` and with the default.
Affected workbooks in the sample included `Operations Metrics`,
`cARR Waterfall Dashboard`, `Executive Dashboard`, `NPS Dashboard`, and
`Crime Dashboard (1)`.

In a single-pass probe of all 498 workbooks: **123 returned 200, 157 returned a
permanent 400**, 2 returned 409, and 216 were rate-limited (see below). Excluding
the rate-limited ones, roughly **one in three workbooks cannot be read at all**.
The root cause is unconfirmed — plausibly workbooks whose connections the API
service account lacks access to.

**Consequence: an org-wide `search-code` sweep is not exhaustive.** A no-match
result is not proof of absence. `search-code` therefore tracks and prints every
skipped workbook, and refuses to let a no-match result stand unqualified.

### Sigma rate-limits aggressively, and the lockout hits auth

Sweeping with 8 concurrent workers got 216 of 498 workbooks 429'd, and then
Cloudflare began refusing **`/v2/auth/token` itself** with an HTML block page —
which disables the entire toolkit, not just the sweep, until it clears.

`codesearch` therefore issues requests **serially**, sleeps
`INTER_WORKBOOK_PAUSE_SECONDS` between workbooks, and retries 429s with
exponential backoff (non-429 errors are re-raised immediately rather than
burning quota). Do not add concurrency here. If you get the Cloudflare block
page, stop and wait several minutes.

## Member ID resolution

Every `createdBy` / `updatedBy` / `ownerId` field is an opaque member ID.
`GET /v2/members` (paginated; 2,246 entries) maps them to names and emails.
`resolve_member_names()` wraps this.
