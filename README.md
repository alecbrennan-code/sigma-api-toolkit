# Sigma API Toolkit

Repeatable Sigma API workflow for teammates who need to go from a shared workbook ID or Sigma URL to a local export.

This repo is intentionally separate from any one-off script. It gives you a small package and CLI with three jobs:

1. Load Sigma API credentials from local environment variables.
2. Inspect a workbook and list its elements from just a workbook ID or Sigma URL.
3. Export one element, one page, or all exportable elements in a repeatable way.
4. Trigger Sigma's `/send` export flow when you need Sigma to deliver a stable export to a destination without client-side chunk stitching.

## What problem this solves

When someone sends you a Sigma workbook link or workbook ID, you should be able to:

1. Confirm you can authenticate.
2. Inspect the workbook metadata and exportable elements.
3. Export the relevant data without rewriting API code.

The most reliable pattern is:

1. Start with the workbook ID or Sigma workbook URL.
2. Call the workbook metadata endpoint.
3. Call the workbook elements endpoint.
4. Then either:
   - export a specific element to CSV/JSON, or
   - export all exportable elements into separate files.
5. For large exports that exceed Sigma's direct-download limits, use `send-export` so Sigma writes one stable export to a destination such as cloud storage.

## Repo structure

```text
sigma-api-toolkit/
├── .env.example
├── README.md
├── docs/
│   └── architecture.md
├── examples/
│   ├── account_scoring_mid_market_export.md
│   └── account_scoring_mid_market_export.py
│   ├── deal_performance_and_daily_exports.md
│   └── export_sigma_tab_url.py
├── pyproject.toml
├── src/
│   └── sigma_api_toolkit/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       ├── client.py
│       ├── config.py
│       ├── service.py
│       └── utils.py
└── tests/
    ├── test_service.py
    └── test_utils.py
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Then fill in:

```bash
SIGMA_API_URL=...
SIGMA_CLIENT_ID=...
SIGMA_CLIENT_SECRET=...
```

`SIGMA_API_URL` must match your Sigma cloud-specific API base URL. For the current local example, the working value is `https://aws-api.sigmacomputing.com`.

## CLI usage

The CLI accepts either:

- a raw Sigma API workbook reference like `6rXhGgU6qBXYotvQfKtIl1`
- a Sigma workbook URL like `https://app.sigmacomputing.com/flock-safety/workbook/6rXhGgU6qBXYotvQfKtIl1`
- a title-prefixed Sigma URL like `https://app.sigmacomputing.com/flock-safety/workbook/Account-Scoring-Query-5J9dDvF9eJ2BVBFkWxBI5f?:nodeId=BHnQm4BePW`

If the URL includes `nodeId=...`, the toolkit resolves that as either a page/tab ID or a direct element ID.

### 1. Test auth

```bash
sigma-toolkit test-auth
```

### 1a. List workbook controls (filters)

```bash
sigma-toolkit list-controls \
  --workbook "https://app.sigmacomputing.com/flock-safety/workbook/WIP-L1-Cockpit-1TWwiPtRVv4EkG7aBjXFJS"
```

Prints every control with its name and `valueType`. Sigma does not expose the currently-selected value — this command only lists the control surface so you know which names to pass into `--control`.

### 2. Inspect a workbook

```bash
sigma-toolkit inspect-workbook \
  --workbook "https://app.sigmacomputing.com/flock-safety/workbook/6rXhGgU6qBXYotvQfKtIl1"
```

This prints:

- workbook name
- workbook path
- workbook URL
- exportable element IDs and names

If the input URL includes a `nodeId`, the output is scoped to that page/tab.

If you want column metadata too:

```bash
sigma-toolkit inspect-workbook \
  --workbook 6rXhGgU6qBXYotvQfKtIl1 \
  --include-columns
```

### 3. Export a specific element

```bash
sigma-toolkit export-data \
  --workbook 6rXhGgU6qBXYotvQfKtIl1 \
  --element-id CA0FSO8BQ0 \
  --output-file exports/flock_academy_users.csv \
  --overwrite
```

Or by element name:

```bash
sigma-toolkit export-data \
  --workbook 6rXhGgU6qBXYotvQfKtIl1 \
  --element-name "Course Properties Parsed" \
  --overwrite
```

### 3a. Export with a filter applied (workbook controls)

Sigma's `/export` and `/send` endpoints accept control values server-side, so you can pull a pre-filtered slice without touching the workbook UI:

```bash
sigma-toolkit export-data \
  --workbook "<url>" \
  --element-id 3ouiWRJsEn \
  --control 'Sales-Team=["Major Markets 1"]' \
  --control 'Include-Closed=false' \
  --output-file exports/team-pipe-gen__major-markets-1.csv \
  --overwrite
```

- `--control NAME=VALUE` is repeatable. Values that look like JSON (`[`, `{`, `true`, `false`, numbers) are parsed as JSON so array-valued controls like `text-list` work. Plain strings pass through as-is.
- `--controls-file path.json` takes a JSON object mapping control name to value. Useful for complex values (date ranges) or when you have many controls. `--control` flags win on overlap.
- `--print-request` resolves the control map and prints it without firing the export. Use this as a dry-run to catch typos in control names before spending a Sigma query.

Run `sigma-toolkit list-controls --workbook "<url>"` first to discover valid control names and their value types.

### 4. Export every exportable element from a workbook

This is the most useful “workbook ID only” team workflow:

```bash
sigma-toolkit export-data \
  --workbook 6rXhGgU6qBXYotvQfKtIl1 \
  --all-elements \
  --output-dir exports \
  --overwrite
```

That will create one file per exportable element.

### 5. Export directly from a shared workbook tab URL

If a teammate sends the exact Sigma URL for a workbook tab, you can pass it directly:

```bash
sigma-toolkit export-data \
  --workbook "https://app.sigmacomputing.com/flock-safety/workbook/Account-Scoring-Query-5J9dDvF9eJ2BVBFkWxBI5f?:nodeId=BHnQm4BePW" \
  --output-file exports/account-scoring-query__mid-market.csv \
  --chunk-size 500000 \
  --chunk-overlap-rows 1000 \
  --overwrite
```

For CSV and JSON exports, the toolkit resolves the page/tab to its exportable element(s) and exports those data objects rather than trying to export the page itself as a document.

For chunked CSV exports, the toolkit now re-requests an overlapping boundary between chunks and validates that the rows match exactly before appending. If Sigma returns a different boundary, the export fails loudly instead of silently writing a mismatched CSV.

### 6. Use Sigma's send flow for stable large exports

Sigma's direct `/export` endpoint tops out at 1 million rows per request and Sigma documents that chunked requests can overlap because the row order is evaluated when each request is made. For large exports that need to be exact, the toolkit now exposes a `send-export` command that builds a `/v2/workbooks/{workbookId}/send` request and lets Sigma deliver one export to a destination.

Start from a target spec JSON file. A warehouse-backed cloud storage target is the most useful option for multi-GB CSV exports:

```json
{
  "targets": [
    {
      "type": "workbook-cloud-export",
      "workbookCloudExport": {
        "connectionType": "snowflake",
        "authorization": "YOUR_STORAGE_AUTHORIZATION",
        "uri": "s3://YOUR_BUCKET/sigma/account-scoring.csv",
        "exportFormat": "csv",
        "timestampedUri": null
      }
    }
  ]
}
```

Save that JSON locally and run:

```bash
sigma-toolkit send-export \
  --workbook "https://app.sigmacomputing.com/flock-safety/workbook/Account-Scoring-Query-5J9dDvF9eJ2BVBFkWxBI5f?:nodeId=BHnQm4BePW" \
  --request-file examples/send_export_workbook_cloud_storage.json.example \
  --dry-run
```

The dry run prints the exact `/send` payload with the correct Sigma `elementId` or `pageId` injected into `attachments`. Remove `--dry-run` once the target config matches your org's storage setup.

### 7. Use the checked-in Account Scoring reference export

The repo includes a concrete reference example for the exact `Account Scoring Query -> Mid Market` export validated in this project:

```bash
python3 examples/account_scoring_mid_market_export.py \
  --env-file .env \
  --output-file exports/account-scoring-query__mid-market.csv \
  --chunk-size 500000 \
  --chunk-overlap-rows 1000 \
  --request-timeout-seconds 600 \
  --overwrite
```

Supporting reference notes live in `examples/account_scoring_mid_market_export.md`.

If you know the expected row count from the Sigma source, pass `--expected-rows N` so the script counts data rows in the completed CSV and fails loudly on mismatch:

```bash
python3 examples/account_scoring_mid_market_export.py \
  --env-file .env \
  --expected-rows 1450000 \
  --overwrite
```

On resume, the script now validates continuity between the existing CSV and the new chunk by byte-matching the last `--chunk-overlap-rows` rows of the file against the head of the resumed Sigma response. A mismatch stops the export instead of appending drift.

### 8. Use the generic Sigma tab URL export helper

If you want a saved script for a specific Sigma workbook URL, use:

```bash
python3 examples/export_sigma_tab_url.py \
  --env-file .env \
  --workbook-url "https://app.sigmacomputing.com/flock-safety/workbook/...?:nodeId=..." \
  --output-file exports/example.csv \
  --overwrite
```

This script is intentionally generic and handles both cases we saw in practice:

- `nodeId` is a page/tab ID
- `nodeId` is a direct element/table ID

The exact `deal performance daily` and `deal performance` reference pulls are documented in `examples/deal_performance_and_daily_exports.md`.

## Operational pattern for teammates

1. One person shares a Sigma workbook link or workbook ID.
2. Another person runs `inspect-workbook` to see element IDs and names.
3. They export either:
   - a specific element, or
   - `--all-elements` if the goal is “pull the workbook’s data locally”.

This avoids embedding workbook-specific logic in code.

## Known exports

If the request is “pull the same thing we exported before,” start with the checked-in references instead of reconstructing the workbook:

- `examples/account_scoring_mid_market_export.py` and `examples/account_scoring_mid_market_export.md`
- `examples/deal_performance_and_daily_exports.md`

For long-running CSV pulls that fail mid-download, prefer resuming from the next Sigma row offset instead of restarting from zero.

If a chunked CSV export fails with a boundary-validation error, treat that as a data-safety stop rather than a transient transport failure. The usual fixes are:

1. Add a deterministic `order by` to the underlying Sigma SQL or data model element.
2. Export smaller filtered slices so the result fits in a single Sigma export request.
3. Switch to `send-export`, ideally with a warehouse-backed cloud storage target, so Sigma delivers one stable export without offset pagination.
4. Keep chunk overlap enabled so the toolkit can verify chunk continuity when you must stay on direct `/export`.

## Safety and repeatability

- Secrets are never committed. `.env` is gitignored.
- Exports fail if the output file already exists, unless `--overwrite` is set.
- Large CSV/JSON/XLSX exports can be chunked with `rowLimit` and `offset`.
- Sigma documents that chunked exports can overlap between requests, so the toolkit uses overlap-aware CSV chunk validation by default in the CLI and example scripts.
- Sigma also supports a `/send` workflow that can deliver an export directly to a destination. This is the preferred path for large exact exports because it avoids client-side stitching entirely.
- Workbook URLs are accepted directly, so teammates do not need to manually extract IDs.

## Useful commands

```bash
sigma-toolkit inspect-workbook --workbook <workbook-id-or-url>
sigma-toolkit export-data --workbook <workbook-id-or-url> --all-elements --overwrite
sigma-toolkit export-data --workbook <workbook-id-or-url> --element-name "<element name>" --overwrite
sigma-toolkit send-export --workbook <workbook-id-or-url> --request-file <targets.json> --dry-run
python3 examples/account_scoring_mid_market_export.py --env-file .env --overwrite
python3 examples/export_sigma_tab_url.py --env-file .env --workbook-url "<sigma-url>" --output-file exports/example.csv --overwrite
python3 -m unittest discover -s tests
```

## Architecture and references

Architecture notes live in [docs/architecture.md](docs/architecture.md).

Official Sigma docs used for this toolkit:

- [Get access token](https://help.sigmacomputing.com/reference/posttoken)
- [Get a workbook](https://help.sigmacomputing.com/reference/getworkbook)
- [Export data from a workbook](https://help.sigmacomputing.com/reference/exportworkbook)
- [List elements in a workbook](https://help.sigmacomputing.com/reference/listworkbookelements)
- [Export data from a workbook](https://help.sigmacomputing.com/reference/exportworkbook)
- [Export a workbook](https://help.sigmacomputing.com/reference/sendworkbook)
- [Export to cloud storage](https://help.sigmacomputing.com/docs/export-to-cloud-storage)
- [Download an exported file](https://help.sigmacomputing.com/reference/downloadquery)
