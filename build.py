#!/usr/bin/env python3
"""Regenerate data.geojson from the Cloudflare Worker (live status) + geometry.json (committed shapes).
Status is BAKED IN at build time so the public site needs NO Airtable token.
Reads come from the Worker GET (token held server-side); no Airtable key needed here.
Run:  python3 build.py
"""
import json, os, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(HERE, "config.json")))
WORKER = cfg.get("workerUrl", "https://sfla-write.thehelicopter.workers.dev")

# live status for every SFLA, straight from the Worker (no token in this client).
# Cloudflare 403s the default Python-urllib UA, so present a normal one.
req = urllib.request.Request(WORKER, headers={"User-Agent": "thc-sfla-report/1.0"})
resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
status_by = resp.get("sites", {})

geom = json.load(open(os.path.join(HERE, "geometry.json")))
feats = []
for name, g in geom.items():
    st = status_by.get(name, {})
    ring = g["ring"][:]
    if ring[0] != ring[-1]: ring.append(ring[0])
    feats.append({
        "type": "Feature",
        "properties": {
            "name": name,
            "areas": st.get("areas") or g["areas"],
            "status": st.get("status", "New SFLA"),
            "lastChecked": st.get("lastChecked"),
            "checkCount": st.get("checkCount", 0),
            "notes": st.get("notes", ""),
        },
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    })
out = {"type": "FeatureCollection", "generated": True, "features": feats}
json.dump(out, open(os.path.join(HERE, "data.geojson"), "w"))
print(f"data.geojson written: {len(feats)} features ({len(status_by)} had live status)")
