# AGENTS.md — Sigma API Toolkit

Instructions in this file apply to all work in this repository. They are intentionally agent-agnostic: `CLAUDE.md` is a symlink to this file so Claude Code, Codex, and any other coding agent read the same source of truth.

## Purpose
- This repo exists so a teammate or another coding-agent session can go from a Sigma workbook URL or workbook ID to a repeatable local export.
- Prefer reusing the package CLI and checked-in example scripts over writing one-off export code from scratch.

## First Reads
- Read `README.md` first for setup, CLI usage, and the expected repo workflow.
- For known repeatable pulls, check `examples/*.md` before inventing new logic.
- Current canonical export references:
  - `examples/account_scoring_mid_market_export.py`
  - `examples/account_scoring_mid_market_export.md`
  - `examples/deal_performance_and_daily_exports.md`
  - `examples/export_sigma_tab_url.py`

## Working Rules
- `exports/` is local-only output and must not be committed.
- `.env` is local-only and must not be committed.
- Use `sigma-toolkit inspect-workbook` or `examples/export_sigma_tab_url.py` to resolve workbook URLs before adding custom code.
- If a known export already has a dedicated example script, update that script and its companion `.md` instead of creating a second overlapping variant.
- If an export is interrupted, prefer resume-capable flows instead of restarting from zero when practical.

## Choosing a delivery mode
- For pulls expected to stay under ~1M rows, direct `/export` via `sigma-toolkit export-data` or an example script is fine.
- For pulls that exceed 1M rows OR that must be exact, prefer `sigma-toolkit send-export` with a cloud-storage target (e.g., S3). Direct `/export` pagination can overlap across requests when the underlying query lacks a deterministic `ORDER BY`, and the toolkit's client-side overlap validation will fail loudly rather than silently writing a mismatched CSV. `send-export` avoids that entire class of issue by letting Sigma write one stable export to a destination.
- If you must stay on direct `/export` for a large pull, keep `--chunk-overlap-rows` non-zero so the toolkit can byte-match each chunk boundary.

## Resume and row-count safety
- The example script `examples/account_scoring_mid_market_export.py` supports `--resume-offset <sigma_row>`. On resume, the script reads the tail of the existing CSV, backs up the Sigma offset by `--chunk-overlap-rows`, and has `iter_export_chunks` byte-validate that the resumed rows connect cleanly to what's already on disk. If the validation fails, the export stops instead of corrupting the file.
- Both the account-scoring script and any new pulls should prefer passing `--expected-rows N` when the source row count is known. The script counts data records in the completed CSV and fails on mismatch. Use the same pattern in new scripts by importing `count_csv_data_records` from `sigma_api_toolkit.client`.

## Dashboard health-check playbook

When a user gives you a Sigma URL and asks whether the dashboard is online / working / pulling correctly, do not try to manually inspect every element, and do not blindly scan every page — most workbooks have many hidden / scratch tabs the user does not want spot-checked.

The full skill recipe lives at `.claude/skills/dashboard-healthcheck/SKILL.md`. Read it before running anything. The two-phase shape:

```
# Phase 1: preflight + tab list (cheap, confirms access)
sigma-toolkit list-pages --workbook "<url>" --json

# Phase 2: scoped health check, only the tabs the user picked
sigma-toolkit health-check --workbook "<url>" \
  --page-id <page_id_1> --page-id <page_id_2> \
  --output-json exports/healthcheck.json \
  --output-markdown exports/healthcheck.md
```

Always do Phase 1 first and have the user pick which tabs to spot-check (by index in a numbered list — the harness's multi-select picker maxes out at 4 options, which is too few for most workbooks). An explicit `--page-id` selection overrides the default skip-hidden behaviour.

Hard limits to communicate honestly when reporting back:
- The health check cannot see currently-applied filter values — see the next section.
- The health check does not compare values against a source-of-truth (Snowflake / Redshift). That's a future extension. Don't claim correctness, only connectivity and shape.

## Filtered-export playbook

Follow this flow whenever a user gives you a Sigma workbook URL and asks for data filtered by a control value. It works identically under Claude Code, Codex, or any other agent because the only primitives are the `sigma-toolkit` CLI and the user's `.env`.

**Important limitation up front:** Sigma's REST API does NOT expose the currently-selected value of a control. It exposes the control's name and `valueType`, and it accepts a value on export, but you cannot read what's displayed in someone's browser. Do not pretend otherwise. If a user asks "what is this dashboard filtered to right now," the honest answer is to either open the UI or have them tell you.

### Step 1 — confirm auth
```
sigma-toolkit test-auth
```
If this fails, stop and ask the user to fix `.env`. Do not try to recover by guessing credentials.

### Step 2 — inspect the workbook
```
sigma-toolkit inspect-workbook --workbook "<url>"
```
Capture:
- Workbook ID
- Page ID (if the URL had `nodeId=...` pointing at a page)
- Exportable element IDs, names, and types

Report these back to the user before moving on. If the `nodeId` resolved to a non-exportable node (e.g., a control element), say so and ask which element to pull.

### Step 3 — list controls
```
sigma-toolkit list-controls --workbook "<url>"
```
Capture control name and valueType. Report them to the user and state plainly: "I can't see what any of these are currently set to. Tell me which one(s) to apply and what value."

### Step 4 — align on the plan with the user
State the plan in one message and ask for confirmation before acting. Include:
- Which element you'll export (by ID and name).
- Which controls you'll set, and to what values.
- The export format and expected output path.
- Whether you'll use direct `/export` or `send-export` (prefer `send-export` for expected row counts >1M).

Wait for the user to confirm. Do not skip this step.

### Step 5 — dry-run the request
```
sigma-toolkit export-data --workbook "<url>" --element-id <id> \
  --control 'Sales-Team=["Major Markets 1"]' \
  --print-request
```
`--print-request` resolves `--control` / `--controls-file` into the final parameters map and prints it without firing the export. Show the output to the user. This catches typos in control names (they are case-sensitive) and wrong value types before you spend a Sigma query.

### Step 6 — execute
Drop `--print-request` and add `--output-file` + `--overwrite`. For large pulls, use `send-export` with a cloud-storage target instead.

### Step 7 — verify
Log the output row count. For known expected sizes, pass `--expected-rows N` (supported on the account-scoring example script; add similar logic to new scripts using `count_csv_data_records`). If the filtered row count looks wildly off from what the user expects, surface that before moving on.

### Control value format cheatsheet
- `text` control: `--control Name='value'`
- `text-list` control: `--control Name='["value1","value2"]'` (JSON array — single values also work as a string but arrays are safer)
- `number` / `number-range`: `--control Name=50000` or `--control Name='[100,500]'`
- `boolean`: `--control Name=true`
- `date` / `date-range`: use `--controls-file controls.json` with explicit JSON; the date shape varies by control
- For anything complex or when you have many controls, use `--controls-file path.json` with a JSON object and keep the file gitignored if it holds real values.

## Known Account Scoring Pull
- The current `Account Scoring Query -> Mid Market` reference pull is the direct element URL:
  - `https://app.sigmacomputing.com/flock-safety/workbook/Account-Scoring-Query-5J9dDvF9eJ2BVBFkWxBI5f?:nodeId=BHnQm4BePW`
- Use `examples/account_scoring_mid_market_export.py` for that export.
- Use `--chunk-size 500000` for this pull by default.
- If the network drops mid-run, resume with `--resume-offset <next_sigma_row_offset>`.

## Validation
- For code changes, run `python3 -m unittest discover -s tests`.
- For doc-only updates, at least sanity-check the referenced commands and paths.

## Sharing
- Before pushing, make sure README examples and `examples/*.md` agree on the current source URL, recommended flags, and output path.
- If the repo is meant to be handed to another person or Codex session, keep the “known export” references current so they can reproduce past pulls without reading terminal history.
