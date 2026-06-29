#!/usr/bin/env python3
"""Build the ForeFlight content pack 'THC-SFLA.zip' from the SFLA master KMZ + THC waypoints.

ForeFlight content-pack layout (per foreflight.com/support/content-packs):
  THC SFLA/
    manifest.json
    layers/   THC SFLA Areas.kml   <- vector areas (KML; incl. the Ritz restricted area)
    navdata/  THC Waypoints.kml, NAJD VRPs.kml   <- user waypoints (Point placemarks)

Hosted on GitHub Pages; pilots import via:
  https://foreflight.com/content?downloadURL=https://willslawrence.github.io/SFLA/THC-SFLA.zip

Re-run whenever the SFLA master KMZ (or waypoint sources) change, then commit + push.
The repo's THC_SFLA_master.kmz is the source of the area layer (kept current by the
vault splice pipeline). Waypoint sources live in ./sources/ (copied from the vault).
"""
import os, time, json, zipfile, shutil, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER_KMZ = os.path.join(HERE, "THC_SFLA_master.kmz")
SOURCES = os.path.join(HERE, "sources")          # waypoint KMZ sources (committed)
OUT_ZIP = os.path.join(HERE, "THC-SFLA.zip")
PACK_NAME = "THC SFLA"


def kml_from_kmz(path):
    with zipfile.ZipFile(path) as z:
        name = "doc.kml" if "doc.kml" in z.namelist() else next(
            n for n in z.namelist() if n.endswith(".kml"))
        return z.read(name).decode("utf-8")


def build():
    version = int(time.strftime("%Y%m%d%H%M"))     # higher = newer; ForeFlight detects updates
    manifest = {
        "name": PACK_NAME,
        "abbreviation": "THCSFLA",
        "version": version,
        "organizationName": "The Helicopter Company",
    }

    tmp = tempfile.mkdtemp()
    root = os.path.join(tmp, PACK_NAME)
    os.makedirs(os.path.join(root, "layers"))
    os.makedirs(os.path.join(root, "navdata"))

    # manifest
    with open(os.path.join(root, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    # area layer (519 polygons incl. Ritz restricted) -> KML in layers/
    with open(os.path.join(root, "layers", "THC SFLA Areas.kml"), "w") as f:
        f.write(kml_from_kmz(MASTER_KMZ))

    # waypoints -> KML in navdata/  (each source KMZ becomes one KML file)
    wp_sources = {
        "THC Waypoints.kml": os.path.join(SOURCES, "THC Waypoints.kmz"),
        "NAJD VRPs.kml": os.path.join(SOURCES, "NAJD VRPs.kmz"),
    }
    wp_count = 0
    for out_name, src in wp_sources.items():
        if not os.path.exists(src):
            print(f"  WARN: missing waypoint source {src} — skipped")
            continue
        kml = kml_from_kmz(src)
        wp_count += kml.count("<Point>")
        with open(os.path.join(root, "navdata", out_name), "w") as f:
            f.write(kml)

    # zip the parent folder (archive root contains 'THC SFLA/...')
    if os.path.exists(OUT_ZIP):
        os.remove(OUT_ZIP)
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, _, files in os.walk(root):
            for fn in files:
                full = os.path.join(dirpath, fn)
                arc = os.path.relpath(full, tmp)   # keep 'THC SFLA/' prefix
                z.write(full, arc)
    shutil.rmtree(tmp)

    areas = kml_from_kmz(MASTER_KMZ).count("<Placemark>")
    print(f"Built {OUT_ZIP}")
    print(f"  version {version} · {areas} area polygons · {wp_count} waypoints")


if __name__ == "__main__":
    build()
