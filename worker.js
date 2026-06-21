/**
 * THC SFLA — write proxy (Cloudflare Worker)
 * Holds the Airtable token server-side. Pilots' Suitable/Unsuitable taps POST here.
 * Gated by a shared PIN so only THC pilots can write.
 *
 * Set as Worker secrets/vars (Settings → Variables):
 *   AIRTABLE_TOKEN  = pat...        (Airtable personal access token, data.records:write on the base)
 *   WRITE_PIN       = <shared PIN>  (give this to pilots)
 *   BASE_ID         = appBJW3FvPw5c659F
 *   TABLE           = SFLA Sites v2
 */
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

export default {
  async fetch(req, env) {
    if (req.method === "OPTIONS") return new Response(null, { headers: CORS });

    // Live read: return current status for every SFLA (no PIN — statuses aren't sensitive).
    if (req.method === "GET") {
      const BASE = env.BASE_ID, TABLE = encodeURIComponent(env.TABLE), KEY = env.AIRTABLE_TOKEN;
      const H = { Authorization: `Bearer ${KEY}` };
      const out = {};
      let offset = "";
      try {
        do {
          const url = `https://api.airtable.com/v0/${BASE}/${TABLE}?pageSize=100` + (offset ? `&offset=${offset}` : "");
          const page = await (await fetch(url, { headers: H })).json();
          (page.records || []).forEach(r => {
            const f = r.fields || {};
            const name = f["SFLA Name"];
            if (name) out[name] = { status: f.Status || null, lastChecked: f.LastChecked || null,
              checkCount: f.CheckCount || 0, notes: f.Notes || "" };
          });
          offset = page.offset || "";
        } while (offset);
      } catch (e) {
        return json({ error: "read failed", detail: String(e) }, 502);
      }
      return json({ ok: true, sites: out });
    }

    if (req.method !== "POST") return json({ error: "POST only" }, 405);

    let body;
    try { body = await req.json(); } catch { return json({ error: "bad json" }, 400); }
    const { name, status, notes, pin } = body || {};

    if (!pin || pin !== env.WRITE_PIN) return json({ error: "bad pin" }, 401);
    if (!name || !["Suitable", "Unsuitable"].includes(status)) return json({ error: "bad input" }, 400);

    const BASE = env.BASE_ID, TABLE = encodeURIComponent(env.TABLE), KEY = env.AIRTABLE_TOKEN;
    const H = { Authorization: `Bearer ${KEY}`, "Content-Type": "application/json" };
    const today = new Date().toISOString().split("T")[0];

    // find the record by SFLA Name
    const q = encodeURIComponent(`{SFLA Name}="${name.replace(/"/g, '\\"')}"`);
    const found = await (await fetch(
      `https://api.airtable.com/v0/${BASE}/${TABLE}?filterByFormula=${q}&maxRecords=1`, { headers: H }
    )).json();
    const rec = (found.records || [])[0];
    if (!rec) return json({ error: "not found" }, 404);

    const prev = rec.fields.Status || "";
    const cc = (rec.fields.CheckCount || 0) + 1;

    // update the SFLA record
    const upd = await fetch(`https://api.airtable.com/v0/${BASE}/${TABLE}/${rec.id}`, {
      method: "PATCH", headers: H,
      body: JSON.stringify({ fields: { Status: status, Notes: notes || "", LastChecked: today, CheckCount: cc } }),
    });
    if (!upd.ok) return json({ error: "update failed", detail: await upd.text() }, 502);

    // append to Change Log ONLY when the status actually changed (not a routine re-check)
    if (prev !== status) {
      try {
        await fetch(`https://api.airtable.com/v0/${BASE}/${encodeURIComponent("Change Log")}`, {
          method: "POST", headers: H,
          body: JSON.stringify({ fields: { Name: name, Timestamp: new Date().toISOString(),
            PreviousStatus: prev || "Pending", NewStatus: status, Notes: notes || "" } }),
        });
      } catch (_) {}
    }

    return json({ ok: true, name, status, lastChecked: today, checkCount: cc });
  },
};

function json(obj, code = 200) {
  return new Response(JSON.stringify(obj), { status: code, headers: { ...CORS, "Content-Type": "application/json" } });
}
