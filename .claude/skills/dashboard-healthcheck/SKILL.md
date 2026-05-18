---
name: dashboard-healthcheck
description: Spot-check whether a Sigma dashboard is online and rendering reasonable data. Use when the user gives a Sigma workbook URL/ID and asks "is this working", "spot check this dashboard", "does this dashboard look right", "are all the tabs pulling in correctly", or wants a quick health pass on a workbook before sharing it. The skill is two-phase — first it confirms access and lists every page so the user can pick which tabs to check (most workbooks have many hidden / scratch tabs the user does not want checked), then it samples each exportable element on the selected tabs under the dashboard's saved default filters and reports failed exports, empty elements, and columns that are unexpectedly null. It does NOT compare values against a source-of-truth (that's a future extension) and cannot read currently-applied filter values (Sigma API limitation).
---

# Dashboard health check

Two-phase spot-check: (1) confirm the workbook is accessible and show the user the list of tabs, (2) sample only the tabs the user selects.

## When to invoke

- User pastes a Sigma URL with a question like "is this working", "spot check this", "are the tabs pulling in correctly", "does this look broken"
- User asks to validate a workbook before sharing it externally
- User wants a quick connectivity sanity pass after a Sigma data-source change

Do NOT invoke for:
- A specific data-correctness question ("does the pipeline number match Salesforce?") — that's an SOT comparison, not in scope yet
- A request to *change* filter values to specific known values — use `sigma-toolkit export-data --control` for that instead

## The two-phase flow

### Phase 1 — preflight + tab selection

1. Resolve the workbook URL/ID and confirm access by running:
   ```bash
   cd ~/Documents/sigma-api-toolkit
   sigma-toolkit list-pages --workbook "<url-or-id>" --json
   ```
   If this fails (auth error, 404, permissions), stop and surface the exact error to the user — do not proceed to sampling.

2. Parse the JSON. You'll get `{workbook: {...}, pages: [{page_id, name, hidden}], hidden_flag_exposed: bool}`.

3. Present a numbered list to the user, marking hidden tabs clearly. Lead with visible tabs, then hidden. Example:
   ```
   I can reach **Foo Dashboard** (12 pages). Which tabs should I spot-check?

    1. [visible] Executive Summary
    2. [visible] Pipeline by Segment
    3. [visible] Forecast Submission
    4. [visible] Team Performance
    5. [hidden]  scratch_v2
    6. [hidden]  zzz_old_pipeline

   Reply with numbers (e.g., `1,2,3`), or:
   - `all-visible` — every non-hidden tab
   - `all` — every tab including hidden
   ```
   If `hidden_flag_exposed` is `false`, replace the per-tab `[hidden]/[visible]` markers with a single note: "Sigma did not report hidden status for this workbook — all tabs shown without that flag." Still let the user pick by index.

4. **Why a numbered list instead of the multi-select picker:** Claude Code's `AskUserQuestion` caps at 4 options per question. Most workbooks have more than 4 pages, so we use chat-numbered selection as the consistent path. Selecting by index still avoids typing tab names.

5. Wait for the user's response. Map their selection back to `page_id` values. Validate that every number they gave maps to a page; if anything is out of range, ask them to re-pick instead of guessing.

### Phase 2 — run the scoped health check

Run `sigma-toolkit health-check` with one `--page-id` flag per selected tab:

```bash
sigma-toolkit health-check \
  --workbook "<url-or-id>" \
  --page-id <page_id_1> \
  --page-id <page_id_2> \
  --output-json exports/healthcheck.json \
  --output-markdown exports/healthcheck.md
```

When `--page-id` is provided, it overrides the default skip-hidden behaviour for the selected pages (an explicit user pick wins).

Common adjustments to offer if the user asks for more depth:
- `--sample-rows 5000` — bigger sample, higher-confidence null detection (slower)
- `--high-null-threshold 0.8` — only flag columns above 80% null (default 50%)
- `--request-timeout-seconds 240` — for slow elements

Exit codes:
- `0` — overall status `ok` or `warn`
- `2` — overall status `fail` (at least one element failed to export)
- `1` — auth/config error

## What the per-element check actually does

For each exportable element (`table`, `pivot`, `pivotTable`, `inputTable`) on the selected pages:

1. Issues a small CSV export (default 1000 rows) with **no `parameters` passed**, so Sigma applies the dashboard's saved default filter state.
2. Parses the CSV and computes per-column null counts.
3. Captures a **sample preview** — the first 5 rows verbatim (configurable via `--preview-rows`).
4. Scans the sample for **flagged rows** — rows that match deterministic "looks bad" heuristics:
   - Any cell contains a spreadsheet error token (`#ERROR`, `#DIV/0!`, `#REF!`, `#N/A`, `#NAME?`, `#NULL!`, `#NUM!`, `#VALUE!`, `undefined`, `Infinity`, `-Infinity`)
   - Row is ≥80% null (configurable via `--row-mostly-null-threshold`)

   Up to 10 flagged rows are captured per element by default (`--max-flagged-rows`).
5. Classifies the element as:
   - `[OK]` — rows present, no fully-null columns, no high-null columns, no flagged rows
   - `[WARN]` — 0 rows under defaults, OR any 100%-null column, OR columns at/above the high-null threshold, OR any flagged rows
   - `[FAIL]` — the export itself errored (4xx/5xx, timeout)

## Important constraints to communicate to the user

- **Sigma's REST API does not expose the currently-selected value of a filter.** It only exposes the control's name and value type. The health check runs the dashboard at whatever filter defaults were saved with the workbook. If the user is debugging "why does my view look different from someone else's," the answer is in the UI, not the API. See `AGENTS.md` → "Filtered-export playbook" for the canonical framing.
- **Hidden tabs:** Sigma's `/pages` payload doesn't always include a `hidden`/`visibility` flag. When absent, the skill tells the user that up front.
- **Sample size defaults to 1000 rows per element.** Small samples can miss intermittent nulls.

## Reading the report and reporting back to the user

The markdown digest is the primary artifact. After running the check, do BOTH:

### A. Deterministic findings (from the report itself)

1. Lead with the overall status (OK / WARN / FAIL) and the summary counts.
2. Call out `[FAIL]` first — those are export failures, usually permissions / connectivity / a broken data source binding. Likely needs immediate attention.
3. Then call out `[WARN]`:
   - **0 rows** under default filters — note that this *may* be a legitimately empty filter state (e.g. dashboard ships pointing at a future quarter). Ask the user if that's expected before flagging it as broken.
   - **100% null columns** — strong signal something is broken upstream (column dropped from the source, join failing).
   - **High-null columns (≥50%)** — softer signal; could be normal for sparse fields like `closed_lost_reason`.
   - **Error tokens / mostly-null rows** — Sigma surfaces these when the underlying query or model errored on specific rows. Treat as fail-adjacent.

### B. Semantic review (your job, not Python's)

For every element in the report, read the **Sample (first N rows)** table and any **Flagged rows** table. Apply judgment to spot things the deterministic pass would miss:

- **Junk values that aren't null** — e.g. a `region` column containing `"XYZ"`, `"123"`, or numeric IDs where region names are expected
- **Type smells** — `revenue` showing dates, `email` containing phone numbers, `account_id` mixing UUIDs and integers
- **Magnitude / range smells** — negative values where they shouldn't exist (e.g. negative ARR), dates wildly outside the expected range (e.g. `2099-01-01`), absurdly large numbers
- **Cardinality smells** — every row has the same value in a column that should vary
- **Encoding / truncation smells** — visible `\u00xx` escapes, mojibake, suspicious trailing periods
- **Cross-column consistency smells** — `close_date < created_date`, `amount > 0` but `stage = 'Closed Lost'`

Be concrete: quote the offending cell value(s) and the row index from the report so the user can find them in the dashboard. Don't speculate; if a value *could* be legitimate (e.g. test account, edge case), say so and ask.

If the sampled values all look reasonable for the column names, **explicitly say so** — silence is ambiguous. A one-line "Sample values look semantically aligned with column names across all checked tabs" is the right confirmation when nothing is off.

### C. Close out

End with the filter-surface line: "this run used the workbook's saved defaults; Sigma's API doesn't expose what those values currently are."

If overall status is `ok` AND the semantic review found nothing, a short summary per tab is enough. Don't bury the lead.

## What this skill does NOT do (v1)

- Compare Sigma values against Snowflake/Redshift SOT — that's the future extension that links into the `metric-validation` repo direction.
- Read the dashboard's currently-applied filter values.
- Detect data quality issues that don't manifest as nulls, empties, or export failures (e.g. silently wrong numbers).
- Render charts or non-table elements — only exportable types are checked.

## Environment

Sigma credentials come from `.env` in the repo root (`SIGMA_API_URL`, `SIGMA_CLIENT_ID`, `SIGMA_CLIENT_SECRET`). If the user hasn't validated auth this session, run `sigma-toolkit test-auth` first.
