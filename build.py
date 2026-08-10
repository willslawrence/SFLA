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
shapes = []
for name, g in geom.items():
    # Retired-Unsuitable pads STAY on the map (grey). They used to be filtered out
    # here, which meant an accidental Unsuitable tap made a pad silently disappear
    # with no way to spot or undo it. Will reviews them by hand every few months;
    # the flag still drives the master KMZ's "do not re-survey" folder.
    st = status_by.get(name, {})
    ring = g["ring"][:]
    if ring[0] != ring[-1]: ring.append(ring[0])
    # map.html draws from SHAPES in shapes.js (Leaflet order: lat,lon).
    # geometry.json rings are GeoJSON order (lon,lat) — flip them.
    shapes.append({
        "name": name,
        "coords": [[pt[1], pt[0]] for pt in ring],
        "center": [g["lat"], g["lon"]],
        "areas": g["areas"],
    })
    feats.append({
        "type": "Feature",
        "properties": {
            "name": name,
            # geometry.json is authoritative for area tags — survey metadata, version
            # controlled, reviewable in a diff. Airtable used to win here, which made
            # any tag edit in the repo a silent no-op.
            # The Worker's PIN-gated setAreas action can still write tags to Airtable
            # (it re-tagged 21 pads on 2026-07-06), so the two CAN diverge. That is now
            # reported loudly below rather than silently resolved either way.
            "areas": g["areas"],
            "status": st.get("status", "New SFLA"),
            "lastChecked": st.get("lastChecked"),
            "checkCount": st.get("checkCount", 0),
            "notes": st.get("notes", ""),
        },
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    })
# Area tags live in geometry.json, but the Worker's setAreas action writes them to
# Airtable too. Say so when they disagree — whichever side is stale, staying quiet
# about it is how the map drifts from the database.
drift = [(n, sorted(g["areas"]), sorted(status_by[n]["areas"]))
         for n, g in geom.items()
         if status_by.get(n, {}).get("areas")
         and sorted(status_by[n]["areas"]) != sorted(g["areas"])]
if drift:
    print(f"WARNING: area tags differ from Airtable on {len(drift)} pad(s) — "
          f"geometry.json wins; reconcile or re-run setAreas:")
    for name, mine, theirs in drift:
        print(f"  {name}: geometry.json {mine}  vs  Airtable {theirs}")

out = {"type": "FeatureCollection", "generated": True, "features": feats}
json.dump(out, open(os.path.join(HERE, "data.geojson"), "w"))
print(f"data.geojson written: {len(feats)} features ({len(status_by)} had live status)")

# shapes.js — line 1 is SHAPES (regenerated here); ROUTES and GPS_POINTS on the
# following lines are hand-maintained and passed through untouched.
SHAPES_PATH = os.path.join(HERE, "shapes.js")
lines = open(SHAPES_PATH).read().split("\n")
if not lines[0].startswith("const SHAPES"):
    raise SystemExit("shapes.js: line 1 is not 'const SHAPES' — refusing to rewrite it")
lines[0] = "const SHAPES = " + json.dumps(shapes) + ";"
open(SHAPES_PATH, "w").write("\n".join(lines))
print(f"shapes.js written: {len(shapes)} SHAPES (ROUTES/GPS_POINTS preserved)")
