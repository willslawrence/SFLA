#!/usr/bin/env python3
"""Rebuild THC_SFLA_master.kmz from source-of-truth data so it can't silently drift
from the database, then rebuild the ForeFlight pack. Run AFTER build.py:

    python3 build.py && python3 sync_master_kmz.py

What comes from where:
  - Area folders (UAM / Malham / City Tour / NAJD)  <- data.geojson  (live area membership + shapes;
      a multi-area pad appears once per area folder, coloured by area)
  - "Retired — Unsuitable" folder                   <- geometry.json pads flagged retired:'Unsuitable'
      (kept as survey memory so they aren't re-created as new; never on the public map)
  - Restricted Areas folder + <Style> blocks        <- preserved VERBATIM from the existing KMZ
      (hand-authored, e.g. the Ritz royal-court no-fly polygons; NOT in data — do not drop them)

This is the single command that keeps the downloadable KMZ + ForeFlight pack in lock-step with
data.geojson. build.py bakes data.geojson from Airtable; this bakes the KMZ from data.geojson.
"""
import json, os, re, subprocess, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
KMZ  = os.path.join(HERE, "THC_SFLA_master.kmz")
AREA_FOLDER = {"UAM": "UAM SFLA", "Malham": "Malham SFLA", "City Tour": "City Tour SFLA", "NAJD": "NAJD SFLA"}
AREA_STYLE  = {"UAM": "s_UAM", "Malham": "s_Malham", "City Tour": "s_CityTour", "NAJD": "s_NAJD"}
AREA_ORDER  = ["UAM", "Malham", "City Tour", "NAJD"]
RETIRED_STYLE = ('<Style id="s_Retired"><LineStyle><color>ff988f8a</color><width>1.4</width></LineStyle>'
                 '<PolyStyle><color>80988f8a</color></PolyStyle></Style>')

def ring_coords(ring):
    r = ring[:]
    if r[0] != r[-1]: r = r + [r[0]]
    return " ".join(f"{lon},{lat},0" for lon, lat in r)   # data + geometry rings are [lon,lat]

def placemark(name, style, ring, desc=None):
    d = f"<description>{desc}</description>" if desc else ""
    return (f"<Placemark><name>{name}</name>{d}<styleUrl>#{style}</styleUrl>"
            f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{ring_coords(ring)}"
            f"</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>")

# --- preserve styles + Restricted Areas folder verbatim from the existing KMZ ---
z = zipfile.ZipFile(KMZ); old = z.read("doc.kml").decode("utf-8")
extra = [(n, z.read(n)) for n in z.namelist() if n != "doc.kml"]; z.close()
styles = "".join(re.findall(r"<Style[^>]*>.*?</Style>", old, re.S))
if "s_Retired" not in styles: styles += RETIRED_STYLE
mr = re.search(r"<Folder><name>Restricted Areas</name>.*?</Folder>", old, re.S)
restricted = mr.group(0) if mr else ""

# --- area folders from data.geojson (live area membership) ---
# Unsuitable pads are deliberately NOT in the area folders. data.geojson carries them
# so the web tracker can draw them grey (an accidental Unsuitable tap has to stay
# visible and revertible), but ForeFlight styles a polygon by its area folder, not by
# status — an unsuitable pad in an area folder reads to a pilot as a usable landing
# option. Retired ones keep their own hidden "do not re-survey" folder below.
data = json.load(open(os.path.join(HERE, "data.geojson")))
folders, area_counts, suppressed = [], {}, set()
for area in AREA_ORDER:
    pms = []
    for f in data["features"]:
        if f["properties"].get("status") == "Unsuitable":
            suppressed.add(f["properties"]["name"])
            continue
        if area in (f["properties"].get("areas") or []):
            ring = [(x, y) for x, y in f["geometry"]["coordinates"][0]]
            pms.append(placemark(f["properties"]["name"], AREA_STYLE[area], ring))
    area_counts[area] = len(pms)
    folders.append(f"<Folder><name>{AREA_FOLDER[area]}</name>" + "".join(pms) + "</Folder>")

# --- retired folder from geometry.json ---
geom = json.load(open(os.path.join(HERE, "geometry.json")))
ret = sorted((k, v) for k, v in geom.items() if v.get("retired"))
rpms = [placemark(k, "s_Retired", v["ring"],
                  "Retired — marked Unsuitable (kept as survey memory; do not re-create as new)")
        for k, v in ret]
retired_folder = (f"<Folder><name>Retired — Unsuitable (do not re-survey)</name><visibility>0</visibility>"
                  + "".join(rpms) + "</Folder>") if rpms else ""

doc = ('<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
       '<name>THC SFLA — All Areas</name>' + styles + "".join(folders) + restricted + retired_folder
       + "</Document></kml>")
with zipfile.ZipFile(KMZ, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("doc.kml", doc)
    for n, b in extra: zf.writestr(n, b)

print(f"master KMZ rebuilt: {doc.count('<Placemark>')} placemarks "
      f"(areas {area_counts}, restricted {restricted.count('<Placemark>')}, retired {len(rpms)})")
pending = sorted(suppressed - set(k for k, v in ret))
print(f"  {len(suppressed)} Unsuitable pads kept out of the area folders "
      f"({len(pending)} not yet flagged retired)")
if pending:
    # Marked Unsuitable by a pilot but not yet reviewed. Either confirm and set
    # retired: true in geometry.json, or tap it back to Suitable on the tracker
    # if it was a mis-tap. See work/active/SFLA Unsuitable Review.md in the vault.
    print("  awaiting Will's review: " + ", ".join(pending))
subprocess.run(["python3", os.path.join(HERE, "build_foreflight_pack.py")], check=True)
