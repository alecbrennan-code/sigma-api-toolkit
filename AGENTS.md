# AGENTS.md — Sigma API Toolkit

Instructions in this file apply to all work in this repository.

## Purpose
- This repo exists so a teammate or another Codex session can go from a Sigma workbook URL or workbook ID to a repeatable local export.
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
