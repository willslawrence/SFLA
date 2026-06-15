#!/bin/bash
# Usage: ./report.sh <uam|malham|city-tour|najd|all> [year] [month]
cd "$(dirname "$0")"
.venv/bin/python generate_report.py "$@"
