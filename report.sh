#!/bin/bash
# Usage: ./report.sh <uam|malham|city-tour|najd|all> [year] [month]
# Refreshes data.geojson (for the Site Map Overview) then generates the report(s).
cd "$(dirname "$0")"
KEY=$(.venv/bin/python -c "import json;print(json.load(open('config.local.json'))['apiKey'])" 2>/dev/null)
AIRTABLE_KEY="$KEY" .venv/bin/python build.py >/dev/null 2>&1
.venv/bin/python generate_report.py "$@"
