/**
 * THC SFLA — read/write proxy (Cloudflare Worker)
 * Holds the Airtable token server-side. Pilots' Suitable/Unsuitable taps POST here;
 * the public map + the monthly report read via GET (no token in any client).
 * Gated by a shared PIN for writes only — reads are open (statuses aren't sensitive).
 *
 * Set as Worker secrets/vars (Settings → Variables):
 *   AIRTABLE_TOKEN  = pat...        (Airtable PAT, data.records:read + write on the base)
 *   WRITE_PIN       = <shared PIN>  (give this to pilots)
 *   BASE_ID         = appBJW3FvPw5c659F
 *   TABLE           = SFLA Sites v2
 *
 * Endpoints:
 *   GET  /                       -> { ok, sites: { <name>: {status,lastChecked,checkCount,notes,areas} } }
 *   GET  /?log=1&from=ISO&to=ISO -> { ok, changeLog: [ {name,timestamp,prev,new,notes} ] }
 *   POST /  {name,status,notes,pin}                       -> toggle a pad Suitable/Unsuitable
 *   POST /  {action:"create", pin, records:[{name,areas,status,notes,extraFields}]}
 *                                -> bulk-create new SFLA rows (idempotent: skips names already present)
 *   POST /  {action:"setAreas", pin, records:[{name,areas:[...]}]}
 *   POST /  {action:"setNotes", pin, records:[{name,notes}]}   -> Notes only,
 *          no LastChecked/CheckCount side effects (see the block for why)
 *                                -> bulk-replace the Areas multi-select on existing pads
 */
// Airtable free tier = 1,000 API calls/month for the whole workspace, and one GET here
// costs FIVE of them (489 pads / pageSize 100). index.html and map.html both fetch live
// status on every page load, so ~200 page loads a month exhausts the quota — which is
// exactly what happened by 2026-08-31. 15 min of edge cache turns a day of pilot traffic
// into a handful of calls; writes purge it, so a pilot still sees their own tap land.
const CACHE_TTL = 900;  // seconds; purged on every successful write

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

// fetch every record from an Airtable table (handles pagination), optional query string
class UpstreamError extends Error {
  constructor(msg, status) { super(msg); this.status = status; }
}

async function airtableAll(BASE, table, H, query = "") {
  const out = [];
  let url = `https://api.airtable.com/v0/${BASE}/${encodeURIComponent(table)}?pageSize=100${query}`;
  while (url) {
    // Fail loud. This used to swallow a 401/404 and return {} — the monthly report
    // then rendered an empty month instead of erroring. (2026-08-31)
    const res = await fetch(url, { headers: H });
    if (!res.ok) {
      const body = (await res.text()).slice(0, 300);
      throw new UpstreamError(`Airtable ${res.status} on "${table}": ${body}`, res.status);
    }
    const d = await res.json();
    out.push(...(d.records || []));
    url = d.offset
      ? `https://api.airtable.com/v0/${BASE}/${encodeURIComponent(table)}?pageSize=100${query}&offset=${d.offset}`
      : null;
  }
  return out;
}


// Drop the cached read set after a write so pilots see their own tap immediately.
async function purgeReadCache(req) {
  const cache = caches.default;
  const base = new URL(req.url);
  base.search = "";
  await cache.delete(new Request(base.toString(), { method: "GET" }));
}

async function handleGet(req, BASE, TABLE, H) {
  const url = new URL(req.url);

    // change-log endpoint for the monthly report: ?log=1&from=ISO&to=ISO
    if (url.searchParams.get("log")) {
      const from = url.searchParams.get("from"), to = url.searchParams.get("to");
      let q = "";
      if (from && to) {
        const f = `AND(IS_AFTER(Timestamp,'${from}'),IS_BEFORE(Timestamp,'${to}'))`;
        q = `&filterByFormula=${encodeURIComponent(f)}`;
      }
      // "Change Log" is required. "All Change Log" is a legacy archive table from the v1
      // merge and no longer exists on the base (403 INVALID_PERMISSIONS_OR_MODEL_NOT_FOUND,
      // confirmed 2026-09-01) — a missing OPTIONAL table must not fail the whole report, but
      // a missing required one must. Before the fail-loud patch both were swallowed, so this
      // is the behaviour every previous monthly report already ran on.
      const recs = [...(await airtableAll(BASE, "Change Log", H, q))];
      for (const optional of ["All Change Log"]) {
        try {
          recs.push(...(await airtableAll(BASE, optional, H, q)));
        } catch (e) {
          if (!(e instanceof UpstreamError) || ![403, 404].includes(e.status)) throw e;
          console.log(`optional log table "${optional}" absent — skipped`);
        }
      }
      const changeLog = recs.map((r) => {
        const f = r.fields || {};
        return {
          name: f.Name || "",
          timestamp: f.Timestamp || "",
          prev: f.PreviousStatus || "",
          new: f.NewStatus || "",
          notes: f.Notes || "",
        };
      });
      return json({ ok: true, changeLog });
    }

    // default: current status for every SFLA (map + report)
    const recs = await airtableAll(BASE, TABLE, H);
    const sites = {};
    for (const r of recs) {
      const f = r.fields || {};
      const n = f["SFLA Name"];
      if (!n) continue;
      sites[n] = {
        status: f.Status || "New SFLA",
        lastChecked: f.LastChecked || null,
        checkCount: f.CheckCount || 0,
        notes: f.Notes || "",
        areas: f.Areas || [],
      };
    }
    return json({ ok: true, sites });
}

export default {
  async fetch(req, env, ctx) {
    if (req.method === "OPTIONS") return new Response(null, { headers: CORS });

    const BASE = env.BASE_ID, TABLE = env.TABLE, KEY = env.AIRTABLE_TOKEN;
    const H = { Authorization: `Bearer ${KEY}`, "Content-Type": "application/json" };

    // ---- READ (public) ----
    // Edge-cached for CACHE_TTL. Airtable's free plan caps API calls per month and the
    // quota was exhausted on 2026-08-31, which took out the monthly report AND the map.
    // Every uncached map load used to cost one Airtable call per pad page; now a burst of
    // pilots costs one. Writes purge the cache below, so a status tap still shows up at once.
    if (req.method === "GET") {
      const cache = caches.default;
      const cacheKey = new Request(new URL(req.url).toString(), { method: "GET" });
      const hit = await cache.match(cacheKey);
      if (hit) return hit;
      try {
        const res = await handleGet(req, BASE, TABLE, H);
        const cached = new Response(res.body, res);
        cached.headers.set("Cache-Control", `public, s-maxage=${CACHE_TTL}`);
        ctx.waitUntil(cache.put(cacheKey, cached.clone()));
        return cached;
      } catch (e) {
        // never cache a failure
        const st = e instanceof UpstreamError ? 502 : 500;
        return json({ ok: false, error: String(e && e.message || e) }, st);
      }
    }

    // ---- WRITE (PIN-gated) ----
    if (req.method !== "POST") return json({ error: "GET or POST only" }, 405);

    let body;
    try { body = await req.json(); } catch { return json({ error: "bad json" }, 400); }
    if (!body || !body.pin || body.pin !== env.WRITE_PIN) return json({ error: "bad pin" }, 401);

    const TABLE_ENC = encodeURIComponent(TABLE);

    // ---- CREATE new SFLA rows (bulk, idempotent by SFLA Name) ----
    if (body.action === "create") {
      const records = Array.isArray(body.records) ? body.records : [];
      if (!records.length) return json({ error: "no records" }, 400);

      // names already in the table — never duplicate an existing pad
      const existing = new Set(
        (await airtableAll(BASE, TABLE, H)).map((r) => (r.fields || {})["SFLA Name"]).filter(Boolean)
      );

      const created = [], skipped = [], failed = [], toCreate = [];
      for (const rec of records) {
        const name = rec && rec.name;
        if (!name) { failed.push({ name: null, error: "missing name" }); continue; }
        if (existing.has(name)) { skipped.push(name); continue; }
        const fields = { "SFLA Name": name, Status: rec.status || "New SFLA", CheckCount: 0 };
        if (Array.isArray(rec.areas) && rec.areas.length) fields.Areas = rec.areas;
        if (rec.notes) fields.Notes = rec.notes;
        if (rec.extraFields && typeof rec.extraFields === "object") Object.assign(fields, rec.extraFields);
        toCreate.push({ fields });
        existing.add(name); // guard against dupes inside this same batch
      }

      // Airtable caps creates at 10 records/request
      for (let i = 0; i < toCreate.length; i += 10) {
        const chunk = toCreate.slice(i, i + 10);
        const resp = await fetch(`https://api.airtable.com/v0/${BASE}/${TABLE_ENC}`, {
          method: "POST", headers: H,
          body: JSON.stringify({ records: chunk, typecast: true }),
        });
        if (!resp.ok) { failed.push({ batch: i / 10, error: await resp.text() }); continue; }
        const d = await resp.json();
        for (const r of (d.records || [])) created.push((r.fields || {})["SFLA Name"]);
      }
      ctx.waitUntil(purgeReadCache(req));
      return json({ ok: failed.length === 0, created, skipped, failed });
    }

    // ---- SET AREAS on existing pads (bulk multi-select replace) ----
    // ---- SET NOTES only (no check-history side effects) ----
    //
    // The plain status POST also stamps LastChecked=today and increments CheckCount,
    // which is right for a pilot's tap but wrong for recording WHY a pad was already
    // rejected: it would make a 24 Jul mark look like a fresh check. This patches the
    // Notes field alone, so an after-the-fact reason can be added without falsifying
    // the pad's check history. Same shape as setAreas.
    if (body.action === "setNotes") {
      const records = Array.isArray(body.records) ? body.records : [];
      if (!records.length) return json({ error: "no records" }, 400);

      const byName = {};
      for (const r of await airtableAll(BASE, TABLE, H)) {
        const n = (r.fields || {})["SFLA Name"];
        if (n) byName[n] = r.id;
      }
      const updated = [], notfound = [], failed = [];
      for (const rec of records) {
        const name = rec && rec.name;
        const notes = rec && typeof rec.notes === "string" ? rec.notes : null;
        if (!name || notes === null) { failed.push({ name: name || null, error: "bad record" }); continue; }
        const id = byName[name];
        if (!id) { notfound.push(name); continue; }
        const resp = await fetch(`https://api.airtable.com/v0/${BASE}/${TABLE_ENC}/${id}`, {
          method: "PATCH", headers: H,
          body: JSON.stringify({ fields: { Notes: notes } }),
        });
        if (!resp.ok) { failed.push({ name, error: await resp.text() }); continue; }
        updated.push(name);
      }
      ctx.waitUntil(purgeReadCache(req));
      return json({ ok: failed.length === 0, updated, notfound, failed });
    }

    if (body.action === "setAreas") {
      const records = Array.isArray(body.records) ? body.records : [];
      if (!records.length) return json({ error: "no records" }, 400);

      const byName = {};
      for (const r of await airtableAll(BASE, TABLE, H)) {
        const n = (r.fields || {})["SFLA Name"];
        if (n) byName[n] = r.id;
      }
      const updated = [], notfound = [], failed = [];
      for (const rec of records) {
        const name = rec && rec.name, areas = rec && rec.areas;
        if (!name || !Array.isArray(areas)) { failed.push({ name: name || null, error: "bad record" }); continue; }
        const id = byName[name];
        if (!id) { notfound.push(name); continue; }
        const resp = await fetch(`https://api.airtable.com/v0/${BASE}/${TABLE_ENC}/${id}`, {
          method: "PATCH", headers: H,
          body: JSON.stringify({ fields: { Areas: areas }, typecast: true }),
        });
        if (!resp.ok) { failed.push({ name, error: await resp.text() }); continue; }
        updated.push(name);
      }
      ctx.waitUntil(purgeReadCache(req));
      return json({ ok: failed.length === 0, updated, notfound, failed });
    }

    // ---- STATUS toggle (pilot tap: Suitable / Unsuitable) ----
    const { name, status, notes } = body || {};
    if (!name || !["Suitable", "Unsuitable"].includes(status)) return json({ error: "bad input" }, 400);

    const today = new Date().toISOString().split("T")[0];

    // find the record by SFLA Name
    const q = encodeURIComponent(`{SFLA Name}="${name.replace(/"/g, '\\"')}"`);
    const found = await (await fetch(
      `https://api.airtable.com/v0/${BASE}/${TABLE_ENC}?filterByFormula=${q}&maxRecords=1`, { headers: H }
    )).json();
    const rec = (found.records || [])[0];
    if (!rec) return json({ error: "not found" }, 404);

    const prev = rec.fields.Status || "";
    const cc = (rec.fields.CheckCount || 0) + 1;

    // update the SFLA record
    const upd = await fetch(`https://api.airtable.com/v0/${BASE}/${TABLE_ENC}/${rec.id}`, {
      method: "PATCH", headers: H,
      body: JSON.stringify({ fields: { Status: status, Notes: notes || "", LastChecked: today, CheckCount: cc } }),
    });
    if (!upd.ok) return json({ error: "update failed", detail: await upd.text() }, 502);

    // Append to Change Log ONLY when the status actually changed (not a routine re-check).
    // Off the response path via ctx.waitUntil: the pad's own record is already PATCHed by
    // here, so the pilot's answer does not need to wait on a second Airtable round-trip.
    // waitUntil keeps the Worker alive until it finishes, so nothing is dropped.
    if (prev !== status) {
      const logWrite = fetch(`https://api.airtable.com/v0/${BASE}/${encodeURIComponent("Change Log")}`, {
        method: "POST", headers: H,
        body: JSON.stringify({ fields: { Name: name, Timestamp: new Date().toISOString(),
          PreviousStatus: prev || "Pending", NewStatus: status, Notes: notes || "" } }),
      }).catch(() => {});
      if (ctx && typeof ctx.waitUntil === "function") ctx.waitUntil(logWrite); else await logWrite;
    }

    ctx.waitUntil(purgeReadCache(req));
    return json({ ok: true, name, status, lastChecked: today, checkCount: cc });
  },
};

function json(obj, code = 200) {
  return new Response(JSON.stringify(obj), { status: code, headers: { ...CORS, "Content-Type": "application/json" } });
}
