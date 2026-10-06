#!/bin/bash
# Publish this site to Cloudflare Pages (project: thc-sfla).
# Stages ONLY the files the live page needs, taken from the committed HEAD,
# so scripts, sources and uncommitted edits never ship.
set -euo pipefail
cd "$(cd "$(dirname "$0")" && pwd)"
# launchd runs with a bare PATH — make sure node/npx resolve on both Macs.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
PROJECT="thc-sfla"
FILES=(index.html map.html mapshot.html shapes.js thc_logo.png config.json data.geojson THC_SFLA_master.kmz 'THC-Part-135*.zip')
# --if-changed: skip when this commit is already live (used by the scheduled jobs).
SHA="$(git rev-parse HEAD)"; MARK=".git/cf-deployed-sha"
if [ "${1:-}" = "--if-changed" ] && [ "$(cat "$MARK" 2>/dev/null)" = "$SHA" ]; then
    echo "⏭️  Cloudflare already has $SHA"; exit 0
fi
STAGE="$(mktemp -d)"
git archive HEAD "${FILES[@]}" | tar -x -C "$STAGE"
npx --yes wrangler pages deploy "$STAGE" --project-name "$PROJECT" --branch main --commit-dirty=true
echo "$SHA" > "$MARK"
echo "✅ Live at: https://$PROJECT.pages.dev"
