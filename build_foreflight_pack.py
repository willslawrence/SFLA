#!/usr/bin/env python3
"""Build the ForeFlight content pack 'THC-Part-135.zip' from the SFLA master KMZ + THC waypoints.

ForeFlight content-pack layout (per foreflight.com/support/content-packs):
  THC SFLA/
    manifest.json
    layers/   THC SFLA Areas.kml   <- vector areas (KML; incl. the Ritz restricted area)
    navdata/  THC Waypoints.kml, NAJD VRPs.kml   <- user waypoints (Point placemarks)

Hosted on GitHub Pages; pilots import via:
  https://foreflight.com/content?downloadURL=https://willslawrence.github.io/SFLA/THC-Part-135.zip

Re-run whenever the SFLA master KMZ (or waypoint sources) change, then commit + push.
The repo's THC_SFLA_master.kmz is the source of the area layer (kept current by the
vault splice pipeline). Waypoint sources live in ./sources/ (copied from the vault).
"""
import os, time, json, zipfile, shutil, tempfile, re

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER_KMZ = os.path.join(HERE, "THC_SFLA_master.kmz")
SOURCES = os.path.join(HERE, "sources")          # waypoint KMZ sources (committed)
OUT_ZIP = os.path.join(HERE, "THC-Part-135.zip")
PACK_NAME = "THC Part 135"

# UAM routes layer — built from the SAME source the Riyadh UAM Route Map uses
# (vault scripts/data/uam-route-features.json), so a route-map update + a pack rebuild
# stay in sync.  Falls back to the committed repo copy when the vault isn't mounted.
_VAULT = "/Users/willlawrence/Library/CloudStorage/OneDrive-TheHelicopterCompany/THC Vault/THC"
ROUTES_JSON = (os.environ.get("THC_ROUTES_JSON")
               or os.path.join(_VAULT, "scripts", "data", "uam-route-features.json"))
ROUTES_JSON_FALLBACK = os.path.join(SOURCES, "uam-route-features.json")
# Majmaah Corridor overlay (centreline + gates + OER39 restricted) — built in the vault by
# scripts/majmaah-corridor-kmz.py; rides in the pack as its own toggleable map layer.
MAJMAAH_KMZ = (os.environ.get("THC_MAJMAAH_KMZ")
               or os.path.join(_VAULT, "reference", "uam-kmz", "Majmaah Corridor.kmz"))
MAJMAAH_KMZ_FALLBACK = os.path.join(SOURCES, "Majmaah Corridor.kmz")
ROUTE_CATS = {                                   # cat -> (KML aabbggrr colour, width)
    "appr": ("ff0ec40e", 4),                     # approved  -> green
    "na":   ("ff1111cc", 3),                     # not approved (asked, refused) -> red
    # Published AIP routing that THC is NOT yet approved to fly (asked-nobody-yet, as
    # distinct from "na" = asked and refused). Azure. NOTE: KML LineStyle supports only
    # <color> and <width> — there is no dash/stipple property, so this renders SOLID in
    # ForeFlight. The dashed treatment exists only on the HTML map. (2026-08-10)
    "pub":  ("ffeda600", 3),                     # published, not approved -> azure
}


def kml_from_kmz(path):
    with zipfile.ZipFile(path) as z:
        name = "doc.kml" if "doc.kml" in z.namelist() else next(
            n for n in z.namelist() if n.endswith(".kml"))
        return z.read(name).decode("utf-8")


# Per-category marker styles: a distinct icon + colour per waypoint category, each with
# a white LabelStyle so ForeFlight draws the name on the map. Icons are the google KML
# icon set (mapfiles/kml/...) which ForeFlight renders; colour is KML aabbggrr and tints
# the glyph. Category comes from the "Area - Category" description. Tune freely.
_ICON_BASE = "http://maps.google.com/mapfiles/kml/"
_ICON_SCALE = "0.6"                                    # ~half the old pushpin; adjust to taste
CAT_STYLES = {                                         # category -> (icon href, colour aabbggrr)
    "VRP":           ("shapes/triangle.png",        "ffff0000"),  # blue triangle
    "Hospitals":     ("shapes/hospitals.png",       "ff0000ff"),  # red
    "Heli/Airports": ("shapes/heliport.png",        "ffffffff"),  # heliport glyph
    "Ferry":         ("shapes/ferry.png",           "ffffffff"),  # ferry
    "Info":          ("shapes/placemark_circle.png","ff00ffff"),  # yellow dot
    "Fun":           ("shapes/star.png",            "ff0080ff"),  # orange star
    "Rally":         ("shapes/flag.png",            "ff0080ff"),  # orange flag
    "Alula":         ("shapes/star.png",            "ff00ffff"),  # landmark
    "Neom":          ("shapes/star.png",            "ff00ffff"),  # landmark
}
_DEFAULT_STYLE = ("shapes/placemark_circle.png",    "ffff0000")   # blue dot — any other category

_PM_RE = re.compile(r'<Placemark>(.*?)</Placemark>', re.S)
_NAME_RE = re.compile(r'<name>(.*?)</name>', re.S)
_DESC_RE = re.compile(r'<description>(.*?)</description>', re.S)


def _vrp_code(name, area):
    """Shorten a VRP name to its map code: drop the 'VRP' token, the trailing
    area token, and any leading index number.  'A VRP RUH'->'A', 'KAFD_RUH'->'KAFD'."""
    toks = [t for t in name.replace('_', ' ').split() if t.upper() != 'VRP']
    while toks and area and toks[-1].upper() == area.upper():
        toks.pop()
    while toks and toks[0].isdigit():
        toks.pop(0)
    return ' '.join(toks).strip() or name


def _style_id(cat):
    sid = re.sub(r'[^a-z0-9]+', '_', cat.lower()).strip('_')
    return "thc_" + (sid or "default")


def _style_block(sid, href, color):
    return (
        '<Style id="%s"><IconStyle><color>%s</color><scale>%s</scale>'
        '<Icon><href>%s%s</href></Icon>'
        '<hotSpot x="0.5" y="0.5" xunits="fraction" yunits="fraction"/></IconStyle>'
        '<LabelStyle><color>ffffffff</color><scale>1</scale></LabelStyle></Style>'
        % (sid, color, _ICON_SCALE, _ICON_BASE, href))


def style_waypoint_labels(kml):
    """Label EVERY waypoint and give it a per-category marker (VRP = blue triangle,
    Hospitals = red, Heli/Airports = heliport, etc.; unknown categories = blue dot).
    VRPs are additionally shortened to their on-map code (full name kept in the
    description; codes colliding across areas are de-duped by area).
    Returns (new_kml, [(full_name, code, area), ...]) for the shortened VRPs."""
    infos, used = [], {}     # infos aligned to _PM_RE order (None = no <name>); used = styles seen
    for m in _PM_RE.finditer(kml):
        body = m.group(1)
        dm, nm = _DESC_RE.search(body), _NAME_RE.search(body)
        if not nm:
            infos.append(None)
            continue
        desc = dm.group(1).strip() if dm else ''
        cat = desc.split(' - ')[-1].strip() if ' - ' in desc else ''
        href, color = CAT_STYLES.get(cat, _DEFAULT_STYLE)
        sid = _style_id(cat) if cat in CAT_STYLES else "thc_default"
        used[sid] = (href, color)
        info = {'sid': sid, 'vrp': cat == 'VRP'}
        if info['vrp']:
            area = desc[:-len('- VRP')].rstrip('- ').strip()
            name = nm.group(1).strip()
            info.update(area=area, name=name, code=_vrp_code(name, area))
        infos.append(info)

    counts = {}                                          # de-dup VRP codes across areas
    for i in infos:
        if i and i.get('vrp'):
            counts[i['code']] = counts.get(i['code'], 0) + 1
    for i in infos:
        if i and i.get('vrp') and counts[i['code']] > 1:
            i['code'] = (i['code'] + ' ' + i['area']).strip()

    styles = "".join(_style_block(sid, h, c) for sid, (h, c) in sorted(used.items()))
    kml = kml.replace('<Document>', '<Document>\n' + styles, 1)

    seq = iter(infos)

    def repl(m):
        info = next(seq)
        if not info:
            return m.group(0)
        body = m.group(1)
        if info.get('vrp'):                              # VRP: swap name -> code, stash full name
            body = _NAME_RE.sub('<name>%s</name>' % info['code'], body, count=1)
            body = _DESC_RE.sub('<description>%s (%s)</description>' % (info['name'], info['area']),
                                body, count=1)
        if '<styleUrl>' not in body:
            body = body.replace('<Point>', '<styleUrl>#%s</styleUrl><Point>' % info['sid'], 1)
        return '<Placemark>%s</Placemark>' % body

    kml = _PM_RE.sub(repl, kml)
    mapping = [(i['name'], i['code'], i['area']) for i in infos if i and i.get('vrp')]
    return kml, mapping


def _xml_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def routes_layer_kml(json_path):
    """Build a ForeFlight map-layer KML of the approved (green) + not-approved (red)
    UAM routes from uam-route-features.json.  Coords in the JSON are [lat, lon] under
    'c'; KML wants lon,lat,alt.  Returns (kml_text, [(cat, name), ...])."""
    data = json.load(open(json_path, encoding="utf-8"))
    styles = "".join(
        '<Style id="rte_%s"><LineStyle><color>%s</color><width>%d</width></LineStyle>'
        '<PolyStyle><fill>0</fill></PolyStyle></Style>' % (cat, col, w)
        for cat, (col, w) in ROUTE_CATS.items()
    )
    placemarks, listing = [], []
    for L in data.get("lines", []):
        cat = L.get("cat")
        if cat not in ROUTE_CATS:
            continue
        coords = " ".join("%s,%s,0" % (pt[1], pt[0]) for pt in L.get("c", []))
        if not coords:
            continue
        placemarks.append(
            '<Placemark><name>%s</name><styleUrl>#rte_%s</styleUrl>'
            '<LineString><tessellate>1</tessellate><coordinates>%s</coordinates></LineString></Placemark>'
            % (_xml_escape(str(L.get("n", ""))), cat, coords))
        listing.append((cat, L.get("n", "")))
    kml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>THC Routes</name>\n'
           + styles + "\n" + "\n".join(placemarks) + "\n</Document></kml>\n")
    return kml, listing


def build():
    version = int(time.strftime("%Y%m%d%H%M"))     # higher = newer; ForeFlight detects updates
    manifest = {
        "name": PACK_NAME,
        "abbreviation": "THCP135",
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

    # area layer (SFLA polygons incl. Ritz restricted) -> KML in layers/
    with open(os.path.join(root, "layers", "THC SFLA Areas.kml"), "w") as f:
        f.write(kml_from_kmz(MASTER_KMZ))

    # UAM routes layer -> KML in layers/  (approved green + not-approved red)
    routes_src = ROUTES_JSON if os.path.exists(ROUTES_JSON) else ROUTES_JSON_FALLBACK
    if os.path.exists(routes_src):
        shutil.copyfile(routes_src, ROUTES_JSON_FALLBACK)   # keep a committed provenance copy
        routes_kml, routes_listing = routes_layer_kml(routes_src)
        with open(os.path.join(root, "layers", "THC Routes.kml"), "w") as f:
            f.write(routes_kml)
        appr = sum(1 for c, _ in routes_listing if c == "appr")
        na = sum(1 for c, _ in routes_listing if c == "na")
        print(f"  routes layer: {appr} approved (green) + {na} not-approved (red) "
              f"[source: {os.path.basename(routes_src)}]")
    else:
        print(f"  WARN: routes source not found ({routes_src}) — routes layer skipped")

    # Majmaah Corridor overlay -> KML in layers/  (its own toggleable layer; keeps its own
    # styling — shaded corridor, gates with contact freq, OER39 restricted boundary)
    maj_src = MAJMAAH_KMZ if os.path.exists(MAJMAAH_KMZ) else MAJMAAH_KMZ_FALLBACK
    if os.path.exists(maj_src):
        if os.path.abspath(maj_src) != os.path.abspath(MAJMAAH_KMZ_FALLBACK):
            shutil.copyfile(maj_src, MAJMAAH_KMZ_FALLBACK)   # committed provenance copy
        with open(os.path.join(root, "layers", "Majmaah Corridor.kml"), "w") as f:
            f.write(kml_from_kmz(maj_src))
        print(f"  Majmaah Corridor layer added [source: {os.path.basename(maj_src)}]")
    else:
        print(f"  WARN: Majmaah Corridor source not found ({maj_src}) — layer skipped")

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
        if out_name == "THC Waypoints.kml":
            kml, mapping = style_waypoint_labels(kml)
            total = kml.count('styleUrl>#thc_')
            print(f"  labeled {total} waypoints ({len(mapping)} VRPs shortened to codes):")
            for name, code, area in mapping:
                flag = "  <-- disambiguated" if code.endswith(area) and code != name else ""
                print(f"    {name:<32} -> {code}{flag}")
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
