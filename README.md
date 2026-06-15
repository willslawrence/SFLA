# THC SFLA Tracker v2 (multi-area)

One codebase, four areas (UAM · Malham · City Tour · NAJD), one Airtable table (`SFLA Sites v2`).
A pad can belong to multiple areas (multi-select `Areas`) — edited once, shows in every area's map + report.

- **Landing:** `index.html` (no params) → area picker + master KMZ download.
- **Area map:** `index.html?area=uam|malham|city-tour|najd` — same code, filtered + recoloured.
- **No token in the browser.** `build.py` bakes live status from Airtable into `data.geojson`.
  Rebuild: `AIRTABLE_KEY=pat... python3 build.py`
- **geometry.json** = committed shapes (source of truth for polygons).
- **THC_SFLA_master.kmz** = all areas, colour-coded (UAM blue / Malham green / City Tour orange / NAJD purple).

Replaces the legacy `sfla-tracker` + `sfla-malham-tracker` once proven. Those stay live as fallback.
