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
  -> POST /v2/workbooks/{workbookId}/export
  -> poll GET /v2/query/{queryId}/download
  -> write files locally
```

## Module responsibilities

- `config.py`
  - loads `SIGMA_API_URL`, `SIGMA_CLIENT_ID`, and `SIGMA_CLIENT_SECRET`
  - optionally loads `.env`
- `client.py`
  - owns HTTP behavior, authentication, pagination, polling, and file export
- `service.py`
  - owns workbook inspection and element-selection rules
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

