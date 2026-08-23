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
# Waypoints — GENERATED in the vault from Files/Waypoints/Waypoints Master.csv by
# scripts/build-foreflight-csv.py (the CSV is the only editing surface). Falls back to the
# committed copy when the vault isn't mounted, like the other vault-sourced layers.
WAYPOINTS_KMZ = (os.environ.get("THC_WAYPOINTS_KMZ")
                 or os.path.join(_VAULT, "Files", "Waypoints", "THC Waypoints.kmz"))
WAYPOINTS_KMZ_FALLBACK = os.path.join(SOURCES, "THC Waypoints.kmz")
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
# Icon artwork. Google's KML shape PNGs were referenced by absolute http:// URL until
# 2026-08-23, when Will reported hospitals drawing as a plain RED TRIANGLE instead of the
# H — i.e. the tint was applied but the artwork never loaded. The KML was correct; the
# remote fetch is what ForeFlight does not do. (Banked as an untested risk on 2026-08-13 —
# "the icons are fetched from maps.google.com, not packaged in the zip"; this is that bill.)
# The PNGs now ship INSIDE the pack under icons/ and are referenced relative to the KML,
# which lives one level down in layers/ or navdata/.
# Flip ICONS_PACKAGED back to False to restore the old remote hrefs.
ICONS_PACKAGED = True
_ICON_DIR = os.path.join(HERE, "icons")
_ICON_BASE = "../icons/" if ICONS_PACKAGED else "http://maps.google.com/mapfiles/kml/"
_ICON_SCALE = "0.6"                                    # ~half the old pushpin; adjust to taste
# The 12-type scheme adopted by Will 2026-08-13. Types 1-9 are Part 121's published
# vocabulary, lifted verbatim from FOB 0326_01 ("THC New User Waypoints Database",
# issued 09 Jul 2025, rev 24 Mar 2026) so the two AOCs' waypoint databases can merge
# later without a renumber; 10-12 are P135-only additions. The type NUMBER is the
# contract with P121 — keep the numbering even where we have no points, and never
# reuse 6 for something else. Rationale + the full table: the vault note
# "ForeFlight Waypoint Icon Scheme" (work/active/).
#
# Category strings here must match Files/Waypoints/Waypoints Master.csv exactly.
CAT_STYLES = {                                          # category -> (icon href, colour aabbggrr)
    # 1  Base — company base / primary helipad
    "Base":             ("shapes/heliport.png",     "ffffffff"),  # untinted heliport
    # 2  VRP — ATC visual reporting points (absorbed the old "Fix")
    "VRP":              ("shapes/triangle.png",     "ffff0000"),  # blue triangle
    # 3  Helipad — paved/concrete, suitable for normal helicopter ops
    "Helipad":          ("shapes/heliport.png",     "ff0ec40e"),  # green heliport
    # 4  Landing Zone — prepared site that is NOT a helipad
    "Landing Zone":     ("shapes/donut.png",        "ff0ec40e"),  # green donut
    # 5  Hospital LZ
    "Hospital LZ":      ("shapes/hospitals.png",    "ff0000ff"),  # red
    # 6  Rig — offshore. Reserved, unused on P135; kept so our IDs match P121's.
    "Rig":              ("shapes/placemark_circle.png", "ffe08000"),  # steel blue dot
    # 7  Landmark — reference points that are not NAV/ATC. Absorbed Fun/Alula/Neom/Ferry/Info.
    "Landmark":         ("shapes/star.png",         "ff00ffff"),  # yellow star
    # 8  VFR Approach — company VFR approach points (UVR / NAJD structure)
    "VFR Approach":     ("shapes/triangle.png",     "ffdd00aa"),  # purple triangle
    # 9  Mobile or Temporary — moving or short-lived points
    "Mobile or Temporary": ("shapes/polygon.png",   "ff101010"),  # black diamond
    # 10 Training area — competency-check + H125/H145 training points  [P135 only]
    "Training area":    ("shapes/target.png",       "ff0080ff"),  # orange target
    # 11 Rally / event — struck after the event                        [P135 only]
    "Rally":            ("shapes/flag.png",         "ff0080ff"),  # orange flag
    # 12 Proposed — NOT certified, not usable for commercial ops        [P135 only]
    "Proposed":         ("shapes/open-diamond.png", "ffff00ff"),  # magenta open diamond
}
_DEFAULT_STYLE = ("shapes/placemark_circle.png",    "ffff0000")   # blue dot — any other category

# Waypoints kept OUT of the THC Waypoints layer, by exact <name>.
#
# This is a standing guarantee, not a one-off edit: whatever is listed here never reaches
# the pack, whether or not it is currently in the source KMZ. Excluding by name rather than
# by deleting from sources/THC Waypoints.kmz is the point — a deletion is invisible, and a
# later refresh of that file silently undoes it.
#
# Since 2026-08-12 the waypoint KMZ is GENERATED from the vault's single editing surface
# (Files/Waypoints/Waypoints Master.csv -> scripts/build-foreflight-csv.py), which ended
# the era of two hand-maintained KMZ drifting in both directions. sources/THC Waypoints.kmz
# is the auto-refreshed offline fallback, same as every other vault-sourced layer here.
DROP_WAYPOINTS = {
    # KAFD was in the set twice, ~12 m apart, so the two markers overlapped: "KAFD RUH"
    # (Heli/Airports -> white heliport glyph) and "KAFD_RUH" (VRP -> blue triangle, drawn
    # as "KAFD"). Will 2026-08-11: keep the blue VRP. The duplicate row was deleted from
    # the master CSV on 2026-08-12, so this entry is now an inert guard like the rest.
    "KAFD RUH",
    # Same trap, same place: "SRSC RUH" and "XRSC RUH" were the identical coordinate under two
    # names. Will 2026-08-10: "SRSC / SRC / XRSC are the same place" = the Riyadh base. Kept
    # XRSC (the location indicator the rest of the vault and Flights Schedule use); the SRSC row
    # was deleted from the master CSV on 2026-08-13, so this entry is an inert guard like the rest.
    "SRSC RUH",
    # The 17 DR fixes belong in NAJD VRPs.kml, not here — they sit close together and
    # clutter the main layer, so they get their own toggleable layer (Will, 2026-08-17).
    # Cut straight out of the KMZ on 2026-08-03, so these entries are inert guards: they
    # exist so a future refresh of the KMZ cannot bring the duplicates back.
    "DR-1", "DR-2", "DR-3", "DR-4", "DR-5", "DR-6", "DR-7 - Ritz Hotel", "DR-8", "DR-9",
    "DR-10", "DR-11", "DR-12", "DR-13", "DR-14", "DR-15", "DR-16", "DR-17",
    # TURAYF and HANIFAH were dropped here too until 2026-08-17, when Will called them
    # "valuable" standalone points rather than part of the DR cluster. They now live in the
    # master CSV and ship in THC Waypoints.kml, and were removed from sources/NAJD VRPs.kmz
    # in the same pass so they are not duplicated. Do NOT re-add them to this list without
    # putting them back in the NAJD layer — they would otherwise vanish from the fleet.
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
        % (sid, color, _ICON_SCALE, _ICON_BASE,
           os.path.basename(href) if ICONS_PACKAGED else href))


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


# Hospitals ride in BOTH navdata and layers, on purpose (2026-08-23, Will).
#   navdata/ -> user waypoints: searchable, enterable in a flight plan / Direct-To. This is
#               what matters on an EMS tasking and must never be lost.
#   layers/  -> a map layer gets its OWN toggle in ForeFlight's layer list, which is the only
#               way to hide hospitals. navdata is governed by ONE "User Waypoints" switch that
#               would take all 281 points with it.
# ⚠️ UNVERIFIED ON DEVICE: shipping a point in both may draw TWO pins when the layer is on.
# If it does, set HOSPITALS_IN_NAVDATA = False (toggle survives, search does not) rather than
# dropping the layer. Prove it on an iPad before this reaches the fleet.
HOSPITALS_IN_NAVDATA = True
_HOSP_RE = re.compile(r"<Placemark>(?:(?!</Placemark>).)*?- Hospital LZ</description>"
                      r"(?:(?!</Placemark>).)*?</Placemark>", re.S)
_STYLE_RE = re.compile(r"<Style id=.*?</Style>", re.S)


def split_hospitals(kml):
    """Return (kml_for_navdata, hospitals_layer_kml, count).

    Builds the layer document from scratch — header + the <Style> blocks it needs +
    the hospital placemarks + its own <name>.

    ⚠️ Do NOT build it by slicing the source up to the first <Placemark>. That was the
    2026-08-23 bug: the source groups placemarks into per-Area <Folder> elements, so the
    slice captured an opening "<Folder><name>Abha</name>" that nothing ever closed. The
    KML was malformed, and ForeFlight does not report a bad layer — it just renders
    NOTHING, which reads exactly like "the feature didn't work".
    """
    hosp = _HOSP_RE.findall(kml)
    if not hosp:
        return kml, None, 0
    styles = "\n".join(_STYLE_RE.findall(kml))
    layer = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
             '<name>THC Hospitals</name>\n'
             + styles + "\n" + "\n".join(hosp) + "\n</Document></kml>\n")
    rest = kml if HOSPITALS_IN_NAVDATA else _HOSP_RE.sub("", kml)
    return rest, layer, len(hosp)


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
    if ICONS_PACKAGED:
        if not os.path.isdir(_ICON_DIR):
            print(f"  WARN: {_ICON_DIR} missing — icons will not render")
        else:
            os.makedirs(os.path.join(root, "icons"))
            n_ic = 0
            for ic in sorted(os.listdir(_ICON_DIR)):
                if ic.lower().endswith(".png"):
                    shutil.copyfile(os.path.join(_ICON_DIR, ic),
                                    os.path.join(root, "icons", ic))
                    n_ic += 1
            print(f"  packaged {n_ic} icons -> icons/ (hrefs are {_ICON_BASE}*)")

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
        "THC Waypoints.kml": (WAYPOINTS_KMZ if os.path.exists(WAYPOINTS_KMZ)
                              else WAYPOINTS_KMZ_FALLBACK),
        "NAJD VRPs.kml": os.path.join(SOURCES, "NAJD VRPs.kmz"),
    }
    wp_count = 0
    for out_name, src in wp_sources.items():
        if not os.path.exists(src):
            print(f"  WARN: missing waypoint source {src} — skipped")
            continue
        if (out_name == "THC Waypoints.kml"
                and os.path.abspath(src) != os.path.abspath(WAYPOINTS_KMZ_FALLBACK)):
            shutil.copyfile(src, WAYPOINTS_KMZ_FALLBACK)   # committed provenance copy
            print(f"  waypoints from vault-generated KMZ [source: {os.path.basename(src)}]")
        kml = kml_from_kmz(src)
        if out_name == "THC Waypoints.kml":
            kml, dropped = drop_waypoints(kml)
            # An entry that matches nothing is not an error — the exclusion list is a
            # standing guarantee, and most of it is already absent from the source.
            for d in sorted(dropped):
                print(f"  dropped duplicate waypoint: {d}")
            inert = DROP_WAYPOINTS - set(dropped)
            print(f"  DROP_WAYPOINTS: {len(dropped)} removed, "
                  f"{len(inert)} already absent from the source (guarding against re-add)")
            kml, mapping = style_waypoint_labels(kml)
            kml, hosp_kml, n_hosp = split_hospitals(kml)
            if hosp_kml:
                with open(os.path.join(root, "layers", "THC Hospitals.kml"), "w") as hf:
                    hf.write(hosp_kml)
                print(f"  hospitals -> layers/THC Hospitals.kml ({n_hosp} points, "
                      f"{'also kept' if HOSPITALS_IN_NAVDATA else 'REMOVED from'} navdata)")
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
