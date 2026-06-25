#!/bin/bash
# Usage: ./report.sh <uam|malham|city-tour|najd|all> [year] [month]
# Refreshes data.geojson (for the Site Map Overview) then generates the report(s).
cd "$(dirname "$0")"
# Reads come from the Cloudflare Worker (token server-side) — no Airtable key needed.
.venv/bin/python build.py >/dev/null 2>&1
.venv/bin/python generate_report.py "$@"
