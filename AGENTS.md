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
