# Cloudflare Worker setup (live pilot writes)

The public site has **no Airtable token**. Pilot Suitable/Unsuitable taps go through this Worker,
which holds the token server-side and is gated by a shared PIN.

## One-time setup (~10 min)
1. Create a free **Cloudflare** account → cloudflare.com (no card needed).
2. Dashboard → **Workers & Pages → Create → Create Worker**. Name it e.g. `sfla-write`. Deploy the starter.
3. **Edit code** → paste the contents of [`worker.js`](worker.js) → **Deploy**.
4. **Settings → Variables and Secrets** → add:
   - `AIRTABLE_TOKEN` = a Airtable PAT with **data.records:read+write** on the SFLA base *(make a NEW one — and **rotate/delete the old public token** `patLbOrww…` that's exposed in the legacy `sfla-tracker` repo)*
   - `WRITE_PIN` = a shared PIN you give pilots (e.g. a 4–6 digit code)
   - `BASE_ID` = `appBJW3FvPw5c659F`
   - `TABLE` = `SFLA Sites v2`
5. Copy the Worker URL (e.g. `https://sfla-write.<you>.workers.dev`).
6. Send me the URL → I set `WORKER_URL` in `map.html` and push. Live writes on.

## Security model
- Token never reaches the browser. Worker only allows setting **Status (Suitable/Unsuitable) + Notes**
  on an existing SFLA — can't touch schema or other tables (except a Change-Log append).
- PIN gate keeps it to THC pilots. Rotate the PIN any time (change the Worker var).
