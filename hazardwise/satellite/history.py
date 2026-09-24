"""Regional event history (v0.21): recent floods and fires near an address,
bucketed into 10 / 20 / 50-year horizons, for a plain-language report block.

Fire: NBAC perimeters whose YEAR falls in each horizon and that intersect a
WIDE regional window (default 25 km — the same ring B2 will use for the
rate), with distance-to-address. Flood: observed extents (EGS) where present;
until that connector lands, JRC occurrence years are not per-event, so flood
history is reported honestly as 'record-based, not event-resolved' with the
occurrence signal summarized. All array/vector inputs injectable for tests."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import numpy as np

HORIZONS = (10, 20, 50)

@dataclass
class RegionalEvent:
    year: int
    kind: str            # "fire" | "flood"
    distance_km: float
    detail: str

def _current_year():
    import datetime
    return datetime.date.today().year

def fire_history(lat, lon, wide_km=25.0, aoi_n=500, res_m=50.0,
                 nbac_layers=None):
    """Return sorted RegionalEvents from NBAC over the wide window. If
    nbac_layers is None, load live. Distance is address-to-nearest-burn-pixel."""
    if nbac_layers is None:
        try:
            from .nbac import load_layers
            nbac_layers, _ = load_layers(lat, lon, wide_km, aoi_n, res_m)
        except Exception:
            nbac_layers = []
    from scipy import ndimage
    cx = cy = aoi_n // 2
    out = []
    for mask, year in nbac_layers:
        if not mask.any():
            continue
        d = ndimage.distance_transform_edt(~mask)[cy, cx] * res_m / 1000.0
        out.append(RegionalEvent(int(year), "fire", float(d),
                                 f"burn perimeter {int(year)}"))
    return sorted(out, key=lambda e: -e.year)

def bucket(events, now=None):
    now = now or _current_year()
    return {h: [e for e in events if 0 <= now - e.year <= h] for h in HORIZONS}

def summarize(events, kind_label, now=None):
    """Plain-language lines per horizon; honest about empties."""
    now = now or _current_year()
    b = bucket(events, now)
    lines = []
    for h in HORIZONS:
        evs = b[h]
        if not evs:
            lines.append(f"Past {h} years: no {kind_label} recorded within "
                         f"the regional window.")
            continue
        nearest = min(evs, key=lambda e: e.distance_km)
        uniq_years = sorted({e.year for e in evs}, reverse=True)
        yrs = ", ".join(str(y) for y in uniq_years[:6])
        more = "" if len({e.year for e in evs}) <= 6 else ", ..."
        lines.append(
            f"Past {h} years: {len({e.year for e in evs})} {kind_label} "
            f"(years: {yrs}{more}); nearest {nearest.distance_km:.1f} km "
            f"({nearest.year}).")
    return lines

def history_indices(fire_events, flood_events=None, now=None):
    now = now or _current_year()
    fb = bucket(fire_events, now)
    idx = {}
    for h in HORIZONS:
        n = len({e.year for e in fb[h]})
        nearest = (f"{min(fb[h], key=lambda e: e.distance_km).distance_km:.1f} km"
                   if fb[h] else "none")
        idx[f"Fires within {h} yr (regional)"] = \
            f"{n}" + (f", nearest {nearest}" if n else "")
    return idx
