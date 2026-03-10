# Sigma API Toolkit

Repeatable Sigma API workflow for teammates who need to go from a shared workbook ID or Sigma URL to a local export.

This repo is intentionally separate from any one-off script. It gives you a small package and CLI with three jobs:

1. Load Sigma API credentials from local environment variables.
2. Inspect a workbook and list its elements from just a workbook ID or Sigma URL.
3. Export one element, one page, or all exportable elements in a repeatable way.

## What problem this solves

When someone sends you a Sigma workbook link or workbook ID, you should be able to:

1. Confirm you can authenticate.
2. Inspect the workbook metadata and exportable elements.
3. Export the relevant data without rewriting API code.

The most reliable pattern is:

1. Start with the workbook ID or Sigma workbook URL.
2. Call the workbook metadata endpoint.
3. Call the workbook elements endpoint.
4. Either:
   - export a specific element to CSV/JSON, or
   - export all exportable elements into separate files.

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
- a title-prefixed Sigma URL like `https://app.sigmacomputing.com/flock-safety/workbook/Account-Scoring-Query-5J9dDvF9eJ2BVBFkWxBI5f?:nodeId=a78KJC6YSe`

If the URL includes `nodeId=...`, the toolkit treats that as the target page/tab ID.

### 1. Test auth

```bash
sigma-toolkit test-auth
```

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
  --workbook "https://app.sigmacomputing.com/flock-safety/workbook/Account-Scoring-Query-5J9dDvF9eJ2BVBFkWxBI5f?:nodeId=a78KJC6YSe" \
  --output-file exports/account-scoring-query__mid-market.csv \
  --overwrite
```

For CSV and JSON exports, the toolkit resolves the page/tab to its exportable element(s) and exports those data objects rather than trying to export the page itself as a document.

### 6. Use the checked-in Account Scoring reference export

The repo includes a concrete reference example for the exact `Account Scoring Query -> Mid Market` export validated in this project:

```bash
python3 examples/account_scoring_mid_market_export.py \
  --env-file .env \
  --output-file exports/account-scoring-query__mid-market.csv \
  --overwrite
```

Supporting reference notes live in `examples/account_scoring_mid_market_export.md`.

## Operational pattern for teammates

1. One person shares a Sigma workbook link or workbook ID.
2. Another person runs `inspect-workbook` to see element IDs and names.
3. They export either:
   - a specific element, or
   - `--all-elements` if the goal is “pull the workbook’s data locally”.

This avoids embedding workbook-specific logic in code.

## Safety and repeatability

- Secrets are never committed. `.env` is gitignored.
- Exports fail if the output file already exists, unless `--overwrite` is set.
- Large CSV/JSON/XLSX exports can be chunked with `rowLimit` and `offset`.
- Workbook URLs are accepted directly, so teammates do not need to manually extract IDs.

## Useful commands

```bash
sigma-toolkit inspect-workbook --workbook <workbook-id-or-url>
sigma-toolkit export-data --workbook <workbook-id-or-url> --all-elements --overwrite
sigma-toolkit export-data --workbook <workbook-id-or-url> --element-name "<element name>" --overwrite
python3 examples/account_scoring_mid_market_export.py --env-file .env --overwrite
python3 -m unittest discover -s tests
```

## Architecture and references

Architecture notes live in [docs/architecture.md](docs/architecture.md).

Official Sigma docs used for this toolkit:

- [Get access token](https://help.sigmacomputing.com/reference/posttoken)
- [Get a workbook](https://help.sigmacomputing.com/reference/getworkbook)
- [List elements in a workbook](https://help.sigmacomputing.com/reference/listworkbookelements)
- [Export data from a workbook](https://help.sigmacomputing.com/reference/exportworkbook)
- [Download an exported file](https://help.sigmacomputing.com/reference/downloadquery)
