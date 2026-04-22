# Architecture

## Goal

Provide a stable team workflow for Sigma exports where the only required business input is a workbook ID or Sigma workbook URL.

## Request flow

```text
Workbook ID or URL
  -> normalize workbook reference
  -> authenticate with Sigma API
  -> GET /v2/workbooks/{workbookId}
  -> GET /v2/workbooks/{workbookId}/elements
  -> choose export target
     -> one element
     -> one page
     -> all exportable elements
  -> choose delivery mode
     -> direct export
        -> POST /v2/workbooks/{workbookId}/export
        -> poll GET /v2/query/{queryId}/download
        -> write files locally
     -> send export
        -> POST /v2/workbooks/{workbookId}/send
        -> let Sigma deliver one export to the requested destination
```

## Module responsibilities

- `config.py`
  - loads `SIGMA_API_URL`, `SIGMA_CLIENT_ID`, and `SIGMA_CLIENT_SECRET`
  - optionally loads `.env`
- `client.py`
  - owns HTTP behavior, authentication, direct export pagination, polling, file export, and `/send` delivery
- `service.py`
  - owns workbook inspection, element-selection rules, and `/send` attachment shaping
- `utils.py`
  - owns workbook URL parsing, filename sanitization, and display helpers
- `cli.py`
  - exposes the shared team workflow as a command line interface

## Selection rules

If the caller only provides a workbook ID:

1. Inspect the workbook.
2. Identify exportable elements.
3. If `--all-elements` is set, export each exportable element.
4. If only one exportable element exists, export it automatically.
5. If multiple exportable elements exist and no selection is provided, fail with a clear message that lists the valid choices.

That keeps the workflow deterministic without silently choosing the wrong table.

## Why this structure

- The client stays reusable for notebooks or future services.
- The service layer keeps Sigma-specific “selection” logic out of raw HTTP code.
- The CLI is thin and easy for teammates to use.
- The repo is safe to share because credentials stay local.

## Constraints from Sigma docs

- Access tokens are short-lived and should be refreshed automatically.
- Workbook exports return a `queryId`, not file bytes directly.
- Export downloads can require polling before the file is ready.
- CSV/XLSX/JSON exports are capped at 1 million rows per request, so chunking via `rowLimit` and `offset` is required for larger results.
- Sigma warns that the order is evaluated when each chunk request is made, so chunked CSV exports can overlap across requests. The toolkit mitigates that by re-requesting an overlap window and validating that the chunk boundaries match before appending rows locally.
- Sigma also exposes a `/send` workflow that can deliver one export to a destination such as cloud storage, Google Drive, Slack, or a webhook. For large exact exports, this is the preferred path because it avoids client-side chunk stitching.

## Expected team workflow

For ad hoc requests:

```bash
sigma-toolkit inspect-workbook --workbook <id-or-url>
sigma-toolkit export-data --workbook <id-or-url> --all-elements --overwrite
```

For targeted pulls:

```bash
sigma-toolkit export-data --workbook <id-or-url> --element-name "<table name>" --overwrite
```

For stable large exports:

```bash
sigma-toolkit send-export --workbook <id-or-url> --request-file <targets.json> --dry-run
```
