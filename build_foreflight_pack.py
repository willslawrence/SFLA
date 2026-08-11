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
OUT_ZIP = os.path.join(HERE, "THC-Part-135.zip")     # stable alias — see release_zip() below
PACK_NAME = "THC Part 135"

# --- ForeFlight URL cache-busting -------------------------------------------------
# ForeFlight's foreflight.com/content?downloadURL=... service fetches through ITS OWN
# servers and caches BY URL.  Republishing new content at an unchanged filename keeps
# serving pilots the old pack — deleting it on the device and force-quitting does NOT
# clear it (proved 2026-08-03 and again 2026-08-11; see the vault's brain/Gotchas.md).
# The only lever that has actually worked is publishing at a URL ForeFlight has never
# seen, so every build also writes a version-stamped copy and prints its import link.
# A query string (?v=N) would be the tidier trick but is untested here and has to be
# percent-encoded inside downloadURL=; the filename bump is the one with evidence.
PAGES_BASE = "https://willslawrence.github.io/SFLA"
LINK_FILE = os.path.join(HERE, "foreflight-import-link.txt")
KEEP_RELEASES = 5                                    # older stamped zips are pruned (logged)

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
# Training / competency-check areas — shaded boxes, transit routes and the exercise points.
# Source of truth is the vault JSON; the repo copy is the offline fallback + provenance.
TRAINING_JSON = (os.environ.get("THC_TRAINING_JSON")
                 or os.path.join(_VAULT, "scripts", "data", "training-areas.json"))
TRAINING_JSON_FALLBACK = os.path.join(SOURCES, "training-areas.json")
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

# Waypoints dropped from the pack at BUILD time, by exact <name>.
# Filtered here rather than deleted from sources/THC Waypoints.kmz, because that file is a
# copy of the vault's waypoint set — editing the copy means the next refresh from the vault
# silently reinstates whatever was removed. (The 19 NAJD fixes de-duped on 2026-08-03 WERE
# cut from the KMZ itself and carry exactly that risk; move them here if they ever return.)
DROP_WAYPOINTS = {
    # KAFD is in the set twice, ~12 m apart, so the two markers overlap on the map:
    # "KAFD RUH" (Heli/Airports -> white heliport glyph) and "KAFD_RUH" (VRP -> blue
    # triangle, drawn as "KAFD"). Will 2026-08-11: keep the blue VRP, drop the white one.
    "KAFD RUH",
}

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


def drop_waypoints(kml):
    """Remove every Placemark whose <name> is in DROP_WAYPOINTS. Returns (kml, [names])."""
    dropped = []

    def repl(m):
        nm = _NAME_RE.search(m.group(1))
        if nm and nm.group(1).strip() in DROP_WAYPOINTS:
            dropped.append(nm.group(1).strip())
            return ""
        return m.group(0)

    return _PM_RE.sub(repl, kml), dropped


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


# Training-area styling. KML colours are aabbggrr, NOT rrggbb.
TRAIN_FILL    = "33ffa03a"                       # blue #3aa0ff at 20% -> transparent shading
TRAIN_OUTLINE = "ff0095ff"                       # orange #ff9500, thin
TRAIN_RTE = {                                    # cat -> (colour, width)
    "out": ("ffffa03a", 2),                      # blue  #3aa0ff
    "in":  ("ffc85fff", 2),                      # pink  #ff5fc8
}


def training_kml(json_path):
    """Build the training-areas MAP LAYER (shaded area boxes + transit routes) and the
    matching NAVDATA file (the exercise points, so a pilot can enter them in a flight
    plan).  Points live only in navdata so they are not drawn twice.
    Returns (layer_kml, navdata_kml, [area names], n_routes, n_points)."""
    data = json.load(open(json_path, encoding="utf-8"))

    styles = ('<Style id="trn_area"><LineStyle><color>%s</color><width>2</width></LineStyle>'
              '<PolyStyle><color>%s</color><fill>1</fill><outline>1</outline></PolyStyle></Style>'
              % (TRAIN_OUTLINE, TRAIN_FILL))
    styles += "".join(
        '<Style id="trn_%s"><LineStyle><color>%s</color><width>%d</width></LineStyle>'
        '<PolyStyle><fill>0</fill></PolyStyle></Style>' % (cat, col, w)
        for cat, (col, w) in TRAIN_RTE.items())

    pms, names = [], []
    for a in data.get("areas", []):
        ring = list(a["coords"]) + [a["coords"][0]]          # KML rings must close
        coords = " ".join("%s,%s,0" % (p[1], p[0]) for p in ring)
        pms.append('<Placemark><name>%s</name><description>%s</description>'
                   '<styleUrl>#trn_area</styleUrl><Polygon><tessellate>1</tessellate>'
                   '<outerBoundaryIs><LinearRing><coordinates>%s</coordinates>'
                   '</LinearRing></outerBoundaryIs></Polygon></Placemark>'
                   % (_xml_escape(a["name"]), _xml_escape(a.get("note", "")), coords))
        names.append(a["name"])

    routes = data.get("routes", [])
    for r in routes:
        cat = r.get("cat", "out")
        if cat not in TRAIN_RTE:
            cat = "out"
        coords = " ".join("%s,%s,0" % (p[1], p[0]) for p in r["c"])
        pms.append('<Placemark><name>%s</name><styleUrl>#trn_%s</styleUrl>'
                   '<LineString><tessellate>1</tessellate><coordinates>%s</coordinates>'
                   '</LineString></Placemark>' % (_xml_escape(r["name"]), cat, coords))

    layer = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
             '<name>THC Training Areas</name>\n' + styles + "\n" + "\n".join(pms)
             + "\n</Document></kml>\n")

    pts = data.get("points", [])
    wp_style = _style_block("thc_training", "shapes/placemark_circle.png", "ff3ad4a0")
    wp = "\n".join(
        '<Placemark><name>%s</name><description>%s</description>'
        '<styleUrl>#thc_training</styleUrl><Point><coordinates>%s,%s,0</coordinates></Point>'
        '</Placemark>' % (_xml_escape(p["n"]), _xml_escape(p.get("d", "")), p["lon"], p["lat"])
        for p in pts)
    navdata = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
               '<name>THC Training Points</name>\n' + wp_style + "\n" + wp
               + "\n</Document></kml>\n")

    return layer, navdata, names, len(routes), len(pts)


def publish_release(version):
    """Copy the freshly built pack to a version-stamped filename and return
    (release_zip_path, import_link).

    The stamped name is what busts ForeFlight's server-side URL cache — see the
    comment on PAGES_BASE.  THC-Part-135.zip is kept as a stable alias so older
    links and docs still resolve (they will just serve whatever ForeFlight already
    cached for that URL, which is exactly the failure mode this works around).
    Older stamped releases beyond KEEP_RELEASES are pruned so the Pages site does
    not grow without bound; anything dropped is logged, never dropped silently.
    """
    name = "THC-Part-135-%s.zip" % version
    release = os.path.join(HERE, name)
    shutil.copyfile(OUT_ZIP, release)

    link = "https://foreflight.com/content?downloadURL=%s/%s" % (PAGES_BASE, name)
    with open(LINK_FILE, "w") as f:
        f.write("%s\n\nRelease %s, built %s.\n"
                "Give pilots THIS link — a new one is minted every release because\n"
                "ForeFlight caches content packs server-side by URL.\n"
                % (link, version, time.strftime("%Y-%m-%d %H:%M")))

    # Point the SFLA landing page's "Import into ForeFlight" button at THIS release.
    # index.html is what pilots actually reach (the Fleet Map's 📲 ForeFlight Pack
    # button links here), so a hardcoded link there goes stale the moment we rebuild.
    page = os.path.join(HERE, "index.html")
    if os.path.exists(page):
        html = open(page, encoding="utf-8").read()
        new_html, n = re.subn(
            r'href="https://foreflight\.com/content\?downloadURL='
            r'https://willslawrence\.github\.io/SFLA/THC-Part-135[^"]*"',
            'href="%s"' % link, html)
        if n:
            open(page, "w", encoding="utf-8").write(new_html)
            print(f"  index.html import button -> {name} ({n} link updated)")
        else:
            print("  WARN: no import link found in index.html — update it by hand")
    else:
        print("  WARN: index.html missing — landing-page link NOT updated")

    stamped = sorted(n_ for n_ in os.listdir(HERE)
                     if re.fullmatch(r"THC-Part-135-\d+\.zip", n_))
    for old in stamped[:-KEEP_RELEASES]:
        os.remove(os.path.join(HERE, old))
        print(f"  pruned old release {old} (keeping last {KEEP_RELEASES})")

    return release, link


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

    # Training / competency-check areas -> layer KML (boxes + transit routes) and the
    # exercise points -> navdata KML (enterable in a ForeFlight flight plan).
    trn_src = TRAINING_JSON if os.path.exists(TRAINING_JSON) else TRAINING_JSON_FALLBACK
    trn_points = 0
    if os.path.exists(trn_src):
        if os.path.abspath(trn_src) != os.path.abspath(TRAINING_JSON_FALLBACK):
            shutil.copyfile(trn_src, TRAINING_JSON_FALLBACK)   # committed provenance copy
        trn_layer, trn_nav, trn_names, n_rte, trn_points = training_kml(trn_src)
        with open(os.path.join(root, "layers", "THC Training Areas.kml"), "w") as f:
            f.write(trn_layer)
        with open(os.path.join(root, "navdata", "THC Training Points.kml"), "w") as f:
            f.write(trn_nav)
        print(f"  training layer: {len(trn_names)} areas ({', '.join(trn_names)}) "
              f"+ {n_rte} routes; {trn_points} points -> navdata")
    else:
        print(f"  WARN: training source not found ({trn_src}) — training layer skipped")

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
            kml, dropped = drop_waypoints(kml)
            for d in dropped:
                print(f"  dropped duplicate waypoint: {d}")
            missing = DROP_WAYPOINTS - set(dropped)
            if missing:
                print(f"  WARN: DROP_WAYPOINTS entries not found (renamed upstream?): "
                      f"{', '.join(sorted(missing))}")
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

    release_zip, link = publish_release(version)

    areas = kml_from_kmz(MASTER_KMZ).count("<Placemark>")
    print(f"Built {OUT_ZIP}")
    print(f"  version {version} · {areas} area polygons · {wp_count + trn_points} waypoints")
    print(f"  release copy: {os.path.basename(release_zip)}")
    print("\n  GIVE PILOTS THIS LINK (it changes every release — the old one keeps\n"
          "  serving the old pack from ForeFlight's cache):\n")
    print(f"    {link}\n")
    print(f"  also written to {os.path.basename(LINK_FILE)}")


if __name__ == "__main__":
    build()
