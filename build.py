#!/usr/bin/env python3
"""Regenerate data.geojson from Airtable (live status) + geometry.json (committed shapes).
Status is BAKED IN at build time so the public site needs NO Airtable token.
Run:  AIRTABLE_KEY=pat... python3 build.py
"""
import json, os, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(HERE, "config.json")))
BASE = cfg["airtable"]["baseId"]
TABLE = cfg["airtable"]["tableName"]
KEY = os.environ.get("AIRTABLE_KEY")
if not KEY:
    raise SystemExit("Set AIRTABLE_KEY env var (a read token for the base).")

def get_all(table):
    recs, off = [], None
    while True:
        url = f"https://api.airtable.com/v0/{BASE}/{urllib.parse.quote(table)}?pageSize=100" + (f"&offset={off}" if off else "")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {KEY}"})
        d = json.load(urllib.request.urlopen(req))
        recs += d["records"]; off = d.get("offset")
        if not off: break
    return recs

status_by = {}
for r in get_all(TABLE):
    f = r["fields"]
    n = f.get("SFLA Name")
    if not n: continue
    status_by[n] = {
        "status": f.get("Status", "New SFLA"),
        "lastChecked": f.get("LastChecked"),
        "checkCount": f.get("CheckCount", 0),
        "notes": f.get("Notes", ""),
        "areas": f.get("Areas", []),
    }

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
