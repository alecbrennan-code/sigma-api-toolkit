# Deal Performance Reference Exports

This file records the exact Sigma sources and commands used to export the two `Deal Performance and Deal Performance Daily` tables.

## Sources

- Larger export URL:
  - `https://app.sigmacomputing.com/flock-safety/workbook/Deal-Performance-and-Deal-Performance-Daily-7ODEcsJDKGGW7oORB9hlH?:nodeId=7KjzlJ2rlA`
  - Resolved workbook reference: `7ODEcsJDKGGW7oORB9hlH`
  - Resolved export element ID: `7KjzlJ2rlA`
  - Table name: `deal performance daily`

- Smaller export URL:
  - `https://app.sigmacomputing.com/flock-safety/workbook/Deal-Performance-and-Deal-Performance-Daily-7ODEcsJDKGGW7oORB9hlH?:nodeId=M9hTbKFO_E`
  - Resolved workbook reference: `7ODEcsJDKGGW7oORB9hlH`
  - Resolved export element ID: `M9hTbKFO_E`
  - Table name: `deal performance`

## Reference commands

```bash
python3 examples/export_sigma_tab_url.py \
  --env-file .env \
  --workbook-url "https://app.sigmacomputing.com/flock-safety/workbook/Deal-Performance-and-Deal-Performance-Daily-7ODEcsJDKGGW7oORB9hlH?:nodeId=7KjzlJ2rlA" \
  --output-file exports/deal-performance-and-deal-performance-daily__deal-performance-daily.csv \
  --overwrite
```

```bash
python3 examples/export_sigma_tab_url.py \
  --env-file .env \
  --workbook-url "https://app.sigmacomputing.com/flock-safety/workbook/Deal-Performance-and-Deal-Performance-Daily-7ODEcsJDKGGW7oORB9hlH?:nodeId=M9hTbKFO_E" \
  --output-file exports/deal-performance-and-deal-performance-daily__deal-performance.csv \
  --overwrite
```

## Output behavior

Each run writes:

- the CSV export
- a `.log.txt` file with chunk timing
- a `.summary.json` file with total runtime, chunk counts, and byte totals

All of those outputs are local only and remain excluded from git by the repo `.gitignore`.
