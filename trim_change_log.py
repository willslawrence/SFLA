#!/usr/bin/env python3
"""
Trim old Change Log entries from the SFLA Airtable base.

Deletes Change Log rows with Timestamp < CUTOFF. Rationale:
- Free-plan workspace hit its record cap.
- Sites table (~410 rows) is fine; Change Log is the dominant growth source.
- Pre-CUTOFF entries are all captured in monthly GACA SFLA reports already.

Usage:
    export AIRTABLE_TOKEN=pat...
    python3 trim_change_log.py --dry-run        # preview — counts, no deletes
    python3 trim_change_log.py                  # do it

The PAT needs data.records:read + data.records:write scope on base appBJW3FvPw5c659F.
Get / regen it at https://airtable.com/create/tokens.
"""
import argparse, os, sys, time, urllib.parse, urllib.request, urllib.error, json
from datetime import datetime, timezone

BASE_ID = "appBJW3FvPw5c659F"
TABLE = "Change Log"
CUTOFF = "2026-04-30"  # delete entries with Timestamp strictly older than this
API = "https://api.airtable.com/v0"


def req(method, url, token, body=None):
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    r = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        sys.exit(f"HTTP {e.code} {method} {url}\n{body}")


def list_all(token):
    table_enc = urllib.parse.quote(TABLE)
    records, offset = [], ""
    while True:
        url = f"{API}/{BASE_ID}/{table_enc}?pageSize=100"
        if offset:
            url += f"&offset={offset}"
        page = req("GET", url, token)
        records.extend(page.get("records", []))
        offset = page.get("offset", "")
        if not offset:
            break
    return records


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only, no deletes")
    ap.add_argument("--cutoff", default=CUTOFF, help=f"ISO date (default {CUTOFF})")
    args = ap.parse_args()

    token = os.environ.get("AIRTABLE_TOKEN")
    if not token:
        sys.exit("Set AIRTABLE_TOKEN env var (Airtable PAT, scope data.records:read+write on base appBJW3FvPw5c659F).")

    cutoff_dt = datetime.fromisoformat(args.cutoff).replace(tzinfo=timezone.utc)

    print(f"Reading Change Log from base {BASE_ID}…")
    records = list_all(token)
    print(f"Total Change Log records: {len(records)}")

    by_month = {}
    to_delete = []
    no_ts = []
    for r in records:
        ts = parse_ts((r.get("fields") or {}).get("Timestamp"))
        if ts is None:
            no_ts.append(r["id"])
            continue
        key = ts.strftime("%Y-%m")
        by_month[key] = by_month.get(key, 0) + 1
        if ts < cutoff_dt:
            to_delete.append(r["id"])

    print("\nRecords by month:")
    for k in sorted(by_month):
        marker = "  (delete)" if k < args.cutoff[:7] else ""
        print(f"  {k}: {by_month[k]}{marker}")
    if no_ts:
        print(f"  (no Timestamp): {len(no_ts)} — left alone")

    print(f"\nCutoff: Timestamp < {args.cutoff}")
    print(f"Records to delete: {len(to_delete)}")
    print(f"Records to keep:   {len(records) - len(to_delete)}")

    if args.dry_run:
        print("\n--dry-run — no deletes. Re-run without --dry-run to apply.")
        return

    if not to_delete:
        print("Nothing to delete.")
        return

    # Airtable DELETE: up to 10 records per call.
    table_enc = urllib.parse.quote(TABLE)
    deleted = 0
    BATCH = 10
    for i in range(0, len(to_delete), BATCH):
        batch = to_delete[i : i + BATCH]
        qs = "&".join(f"records[]={rid}" for rid in batch)
        url = f"{API}/{BASE_ID}/{table_enc}?{qs}"
        req("DELETE", url, token)
        deleted += len(batch)
        print(f"  deleted {deleted}/{len(to_delete)}", end="\r", flush=True)
        time.sleep(0.25)  # stay under 5 req/sec/base limit
    print()
    print(f"Done. Deleted {deleted} Change Log records older than {args.cutoff}.")


if __name__ == "__main__":
    main()
