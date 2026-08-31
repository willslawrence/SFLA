# SFLA write proxy (Cloudflare Worker)

Deployed as **`sfla-write`** → `https://sfla-write.willslawrence.workers.dev`

Holds the Airtable token server-side so nothing client-side needs one: the map, the
monthly GACA report and `build.py` all read through this.

Lived in its own repo (`willslawrence/sfla-write-worker`) until 2026-08-10. It was folded
in here because the split had already caused real drift — the tracker repo carried a stale
June copy of `worker.js` that would have dropped `create`, `setAreas`, the change-log
endpoint and the `areas` field on GET if anyone had deployed it. One Worker, one place.

## Deploy

```bash
cd worker && npx wrangler deploy
```

`wrangler.toml` carries the non-secret vars (`BASE_ID`, `TABLE`). The two secrets are set
on the Worker itself and are **not** in this repo:

```bash
npx wrangler secret put AIRTABLE_TOKEN    # Airtable PAT, data.records:read+write
npx wrangler secret put WRITE_PIN         # shared pilot PIN
```

## API

| Call | Shape |
|---|---|
| Read all | `GET /` → `{ok, sites:{name:{status,lastChecked,checkCount,notes,areas}}}` |
| Change log | `GET /?log=1&from=<iso>&to=<iso>` |
| Status tap | `POST {name, status:"Suitable"\|"Unsuitable", notes, pin}` |
| Bulk insert | `POST {action:"create", pin, records:[…]}` |
| Re-tag areas | `POST {action:"setAreas", pin, records:[{name, areas:[…]}]}` |

The PIN gates casual writes; it is not a security control.

## Note on area tags

The Worker can write area tags via `setAreas`, but **`geometry.json` in the repo root is
authoritative** — `build.py` and `generate_report.py` both read tags from there, and
`build.py` prints a warning naming any pad where Airtable disagrees. After a `setAreas`
call, make the matching edit in `geometry.json` (or the other way round) and rerun the
build.
