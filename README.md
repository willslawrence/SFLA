# THC SFLA Tracker v2 (multi-area)

One codebase, four areas (UAM · Malham · City Tour · NAJD), one Airtable table (`SFLA Sites v2`).
A pad can belong to multiple areas (multi-select `Areas`) — edited once, shows in every area's map + report.

- **Landing:** `index.html` (no params) → area picker + master KMZ download.
- **Area map:** `index.html?area=uam|malham|city-tour|najd` — same code, filtered + recoloured.
- **No token in the browser.** `build.py` bakes live status from Airtable into `data.geojson`
  **and regenerates `shapes.js`**, which is what `map.html` actually draws. Rebuild: `python3 build.py`
- **geometry.json** = committed shapes (source of truth for polygons **and area tags**).
- **worker/** = the Cloudflare Worker that fronts Airtable (`worker/README.md`). Folded in from
  its own repo 2026-08-10 — the split had left a stale copy here that was missing half the API.
- **import_kmz.py** = bring a survey KMZ into the tracker. `--dry-run` first, always.
- **Training areas** (comp check + H125 training) come from the vault JSON
  `scripts/data/training-areas.json` → `layers/THC Training Areas.kml` (shaded boxes +
  transit routes) and `navdata/THC Training Points.kml` (the exercise points, so they're
  enterable in a flight plan). Edit the vault JSON, then re-run `build_foreflight_pack.py`;
  `sources/training-areas.json` is the auto-refreshed offline fallback.

### Rebuild chain after any pad or tag change

```bash
python3 build.py && python3 sync_master_kmz.py && python3 build_foreflight_pack.py
```

`build.py` alone updates the web map but not the master KMZ or the ForeFlight pack.
Pilots do **not** get a pack update automatically — they must delete and re-import it.
- **THC_SFLA_master.kmz** = all areas, colour-coded (UAM blue / Malham green / City Tour orange / NAJD purple).

Replaces the legacy `sfla-tracker` + `sfla-malham-tracker` once proven. Those stay live as fallback.
