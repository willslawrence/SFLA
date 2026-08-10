#!/usr/bin/env python3
"""
Import surveyed SFLA polygons from a KMZ/KML into the tracker.

Closes the gap that let Lindsay Pentz's 21 RYA-5 pads sit in a handed-over KMZ
from 2026-07-06 until 2026-08-05: there was no path from a survey deliverable
into geometry.json, so the map, the monthly UAM->GACA report and the ForeFlight
pack all under-reported.

What it does:
  1. Reads every Polygon placemark in the KMZ/KML.
  2. Ignores names already in geometry.json (survey drops re-export the whole
     area, so most placemarks are existing pads).
  3. Assigns area tags to each new pad by majority vote of its 3 nearest
     existing pads -- a tag is applied when >=2 of the 3 carry it.
  4. Writes the new entries into geometry.json and runs build.py, which
     regenerates data.geojson AND shapes.js (what map.html actually draws).

Airtable rows are NOT created by default -- geometry.json carries the area tags
and build.py falls back to them, so the pads render correctly either way. The
rows are what give a pad live status when a pilot taps Suitable.

Usage:
    python3 import_kmz.py "path/to/survey.kmz" --dry-run     # preview, writes nothing
    python3 import_kmz.py "path/to/survey.kmz"               # geometry.json + rebuild
    python3 import_kmz.py "path/to/survey.kmz" --note "Surveyed by X 2026-07-06"

    # also create the Airtable rows (status "New SFLA"):
    export AIRTABLE_TOKEN=pat...        # data.records:read + write on appBJW3FvPw5c659F
    python3 import_kmz.py "path/to/survey.kmz" --airtable

    # override the computed tags for every new pad:
    python3 import_kmz.py "path/to/survey.kmz" --areas Malham "City Tour"

Without --airtable it prints the rows as CSV so they can be pasted into Airtable
by hand.

Always --dry-run first and read the list. A survey KMZ can carry polygons that
are not pads -- route corridors, survey boxes, area outlines -- and nothing here
can tell those apart from an SFLA by shape alone. The dry run is the review step.
"""
import argparse, csv, io, json, math, os, re, subprocess, sys, zipfile
import urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
GEOMETRY = os.path.join(HERE, "geometry.json")
BASE_ID = "appBJW3FvPw5c659F"
TABLE = "SFLA Sites v2"
API = "https://api.airtable.com/v0"
NEIGHBOURS = 3          # pads consulted when inferring area tags
MAJORITY = 2            # tag applied when this many of them carry it


def read_kml(path):
    """Return the KML text from a .kmz (zip) or a plain .kml."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".kml")]
            if not names:
                sys.exit(f"{path}: no .kml inside the archive")
            return z.read(names[0]).decode("utf-8")
    return open(path, encoding="utf-8").read()


def parse_polygons(kml):
    """[(name, ring)] for every Polygon placemark. Ring is GeoJSON order (lon,lat)."""
    out = []
    for block in re.findall(r"<Placemark>(.*?)</Placemark>", kml, re.S):
        if "<Polygon" not in block:
            continue
        m = re.search(r"<name>(.*?)</name>", block, re.S)
        coords = re.search(r"<coordinates>(.*?)</coordinates>", block, re.S)
        if not m or not coords:
            continue
        ring = []
        for tok in coords.group(1).split():
            parts = tok.split(",")
            if len(parts) >= 2:
                ring.append([float(parts[0]), float(parts[1])])   # lon, lat
        if len(ring) >= 3:
            out.append((m.group(1).strip(), ring))
    return out


def centroid(ring):
    """Mean of the ring vertices, ignoring a repeated closing point."""
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    return sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts)


def haversine(a_lat, a_lon, b_lat, b_lon):
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def infer_areas(lat, lon, existing):
    """Majority vote over the NEIGHBOURS nearest existing pads."""
    near = sorted(existing, key=lambda e: haversine(lat, lon, e["lat"], e["lon"]))[:NEIGHBOURS]
    tally = {}
    for e in near:
        for tag in e["areas"]:
            tally[tag] = tally.get(tag, 0) + 1
    areas = sorted(t for t, n in tally.items() if n >= MAJORITY)
    # A pad wedged between three differently-tagged pads gets no majority; fall
    # back to the single nearest pad rather than leaving it untagged (untagged
    # renders on no area map at all, which is how a pad goes missing).
    return areas or sorted(near[0]["areas"])


def airtable(method, url, token, body=None):
    req = urllib.request.Request(url, method=method,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json",
                                          "User-Agent": "thc-sfla-import/1.0"},
                                 data=json.dumps(body).encode() if body else None)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except urllib.error.HTTPError as e:
        sys.exit(f"Airtable {method} failed ({e.code}): {e.read().decode()[:400]}")


def area_field_name(token):
    """Find the field holding area tags, so a rename doesn't silently drop them."""
    page = airtable("GET", f"{API}/{BASE_ID}/{urllib.parse.quote(TABLE)}?maxRecords=5", token)
    fields = set()
    for rec in page.get("records", []):
        fields.update(rec.get("fields", {}).keys())
    for candidate in ("Areas", "Area", "Area Tags"):
        if candidate in fields:
            return candidate
    sys.exit(f"No area field found on '{TABLE}'. Fields seen: {sorted(fields)}")


def main():
    ap = argparse.ArgumentParser(description="Import surveyed SFLA polygons from a KMZ/KML.")
    ap.add_argument("kmz", help="path to the .kmz or .kml survey deliverable")
    ap.add_argument("--dry-run", action="store_true", help="preview only, write nothing")
    ap.add_argument("--areas", nargs="+", metavar="AREA",
                    help="force these area tags on every new pad instead of inferring")
    ap.add_argument("--note", default="", help="Notes text stamped on each new pad")
    ap.add_argument("--airtable", action="store_true",
                    help="also create the Airtable rows (needs AIRTABLE_TOKEN)")
    args = ap.parse_args()

    geom = json.load(open(GEOMETRY))
    existing = [{"name": n, "lat": g["lat"], "lon": g["lon"], "areas": g["areas"]}
                for n, g in geom.items() if not g.get("retired")]

    polys = parse_polygons(read_kml(args.kmz))
    if not polys:
        sys.exit(f"{args.kmz}: no Polygon placemarks found")
    new = [(n, r) for n, r in polys if n not in geom]
    print(f"{os.path.basename(args.kmz)}: {len(polys)} polygons, "
          f"{len(polys) - len(new)} already known, {len(new)} new")
    if not new:
        print("Nothing to import.")
        return

    rows = []
    for name, ring in new:
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        lat, lon = centroid(ring)
        areas = args.areas if args.areas else infer_areas(lat, lon, existing)
        rows.append({"name": name, "ring": ring, "lat": lat, "lon": lon, "areas": areas})
        print(f"  {name:8} {lat:.5f},{lon:.5f}  ->  {', '.join(areas)}")

    tally = {}
    for r in rows:
        for a in r["areas"]:
            tally[a] = tally.get(a, 0) + 1
    print("Area tags: " + ", ".join(f"{a} x{n}" for a, n in sorted(tally.items())))

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    for r in rows:
        geom[r["name"]] = {"ring": r["ring"], "lat": r["lat"], "lon": r["lon"], "areas": r["areas"]}
    json.dump(geom, open(GEOMETRY, "w"))
    print(f"\ngeometry.json: +{len(rows)} entries, {len(geom)} total")

    if args.airtable:
        token = os.environ.get("AIRTABLE_TOKEN")
        if not token:
            sys.exit("Set AIRTABLE_TOKEN (Airtable PAT, data.records:read+write on "
                     f"{BASE_ID}) or drop --airtable and paste the CSV below by hand.")
        field = area_field_name(token)
        url = f"{API}/{BASE_ID}/{urllib.parse.quote(TABLE)}"
        for i in range(0, len(rows), 10):                     # Airtable caps at 10/request
            batch = [{"fields": {"SFLA Name": r["name"], "Status": "New SFLA",
                                 "CheckCount": 0, "Notes": args.note, field: r["areas"]}}
                     for r in rows[i:i + 10]]
            airtable("POST", url, token, {"records": batch, "typecast": True})
            print(f"  Airtable: created {min(i + 10, len(rows))}/{len(rows)}")
    else:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["SFLA Name", "Status", "CheckCount", "Notes", "Areas"])
        for r in rows:
            w.writerow([r["name"], "New SFLA", 0, args.note, ", ".join(r["areas"])])
        print("\nAirtable rows not created (no --airtable). Paste these in:\n")
        print(buf.getvalue().rstrip())

    print("\nRebuilding data.geojson + shapes.js ...")
    subprocess.run([sys.executable, os.path.join(HERE, "build.py")], check=True)
    print("\nDone. Commit geometry.json, data.geojson and shapes.js.")
    print("If the ForeFlight pack matters for this drop, also run "
          "sync_master_kmz.py and build_foreflight_pack.py.")


if __name__ == "__main__":
    main()
