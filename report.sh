#!/bin/bash
# Usage: ./report.sh <uam|malham|city-tour|najd|all> [year] [month]
# Refreshes data.geojson (for the Site Map Overview) then generates the report(s).
#
# FAILS LOUD (2026-08-31). It used to swallow every error: build.py was `>/dev/null 2>&1`
# and nothing checked whether a PDF actually appeared. On 28 Aug the Cloudflare Worker
# hostname was dead (workers.dev subdomain renamed) and Airtable's monthly API quota was
# spent, so the August run produced NOTHING — and the only trace was a stack trace in a log
# nobody reads. Now: non-zero exit, a size sanity-check on each PDF, and a phone ping.
cd "$(dirname "$0")"

NTFY_TOPIC="thc-bridge-will-c333ed3bee86b1cc"   # same topic as the Claude Bridge / capture pipelines
LOG="logs/launchd.log"
stamp() { date "+%Y-%m-%d %H:%M:%S %Z"; }

ping_fail() {
  /usr/bin/curl -s -m 10 \
    -H "Title: SFLA monthly report" \
    -H "Priority: high" \
    -H "Tags: warning" \
    -d "$1" "https://ntfy.sh/$NTFY_TOPIC" >/dev/null 2>&1 || true
}

fail() {
  echo "[$(stamp)] FAILED: $1"
  ping_fail "SFLA report FAILED ($*). Check $PWD/$LOG"
  exit 1
}

echo "[$(stamp)] report.sh $*"

# Reads come from the Cloudflare Worker (token server-side) — no Airtable key needed.
# Keep build.py's output: a dead Worker shows up here first.
if ! .venv/bin/python build.py >/tmp/sfla-build.out 2>&1; then
  cat /tmp/sfla-build.out
  fail "build.py — Worker read failed"
fi

out=$(.venv/bin/python generate_report.py "$@" 2>&1); rc=$?
echo "$out"
[ $rc -eq 0 ] || fail "generate_report.py exited $rc"

# Every run must have written at least one PDF, and a PDF without its map image lands
# at ~35KB instead of ~2.5MB — that is a failure, not a small file.
# /bin/bash on macOS is 3.2 — no mapfile.
saved=$(echo "$out" | sed -n 's/^Saved: //p')
[ -n "$saved" ] || fail "no PDF written"
echo "$saved" | while IFS= read -r f; do
  [ -f "$f" ] || fail "missing PDF: $f"
  sz=$(stat -f%z "$f")
  [ "$sz" -ge 1000000 ] || fail "PDF too small (${sz}B, map image likely missing): $(basename "$f")"
  echo "[$(stamp)] OK $(basename "$f") ${sz}B"
done || exit 1
