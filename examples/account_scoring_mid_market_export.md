# Account Scoring Mid Market Export

This is a checked-in reference export for the exact Sigma source used during validation.

## Source

- Workbook URL: `https://app.sigmacomputing.com/flock-safety/workbook/Account-Scoring-Query-5J9dDvF9eJ2BVBFkWxBI5f?:nodeId=a78KJC6YSe`
- Workbook reference: `5J9dDvF9eJ2BVBFkWxBI5f`
- Page ID: `a78KJC6YSe`
- Resolved export element ID: `BHnQm4BePW`

## Why this file exists

If a teammate or another Codex session needs to reproduce the exact pull, use this as the canonical example instead of reconstructing the logic from scratch.

## Reference command

After installing the toolkit and setting local Sigma credentials:

```bash
python3 examples/account_scoring_mid_market_export.py \
  --env-file .env \
  --output-file exports/account-scoring-query__mid-market.csv \
  --overwrite
```

## Output artifacts

The script writes three local files:

- the CSV export
- a chunk-by-chunk log file
- a JSON summary with total runtime and per-chunk stats

These outputs are local only. `exports/` is gitignored and should not be committed.

## Notes

- This example intentionally uses `csv` only.
- The script resolves the page-specific element automatically from the shared Sigma URL.
- This is a reference export pattern, not a requirement that future workbook pulls use the same workbook shape.
