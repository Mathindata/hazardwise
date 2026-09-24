"""M5 calibration ground truth: NRCan EGS flood extents -> labeled points.

Data source: "Floods in Canada - Archive" (open.canada.ca dataset
74144824-206e-4cea-9fb9-72925a128189) — satellite-derived flood extent
polygons for major events since 2011, distributed as zips containing
shapefiles. Download the events you want (2013 Alberta, 2017/2019 Ottawa,
2019 Muskoka, 2021 BC) into a folder; this module reads every vector file
under it (shp/gpkg/geojson/gdb — same formats as the basins loader).

Sampling design:
- WET points: uniform inside flood polygons -> label "flooded".
- DRY points: in a ring OUTSIDE the polygons (buffer..ring km from the
  boundary) -> label "dry". Near-miss dry points are the discriminative ones:
  a model that can't separate a flooded street from the dry street two blocks
  uphill has no skill worth publishing.
Both carry the event name (from the file name) so skill can be reported
per event as well as pooled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class LabeledPoint:
    lat: float
    lon: float
    flooded: bool
    event: str


def _event_name(path: Path) -> str:
    stem = re.sub(r"[_\-]+", " ", path.stem)
    return stem[:60]


def _layers_of(src):
    try:
        from pyogrio import list_layers
        return [str(l) for l in list_layers(src)[:, 0]]
    except Exception:
        return [None]


def _event_from_attrs(g, layer, src):
    """Event label from attributes. Robust to unknown schemas: any column
    whose name CONTAINS name-ish/date-ish tokens; if no date column, pull a
    4-digit year (19xx/20xx) out of any string column or the layer name."""
    import pandas as pd
    lc = {c.lower(): c for c in g.columns if c != g.geometry.name}
    def find(tokens):
        for k, orig in lc.items():
            if any(t in k for t in tokens):
                return orig
        return None
    def _stringy(col):
        v = g[col].dropna().astype(str).head(20)
        return (~v.str.fullmatch(r"[\d.\-eE+]+")).mean() > 0.5
    name_col = find(("event", "location", "site", "region", "name",
                     "descr", "comment"))
    if name_col and not _stringy(name_col):
        name_col = None
    date_col = find(("date", "year", "utc", "time"))
    yr_re = re.compile(r"(19|20)\d{2}")
    def year_of(row):
        if date_col:
            d = pd.to_datetime(row[date_col], errors="coerce")
            if pd.notna(d):
                return str(d.year)
            m = yr_re.search(str(row[date_col]))
            if m:
                return m.group(0)
        for c in lc.values():
            m = yr_re.search(str(row[c]))
            if m:
                return m.group(0)
        m = yr_re.search(str(layer or "") + str(src))
        return m.group(0) if m else ""
    def label(row):
        nm = str(row[name_col])[:30] if name_col else (layer or _event_name(Path(src)))
        return f"{nm} {year_of(row)}".strip()
    return g.apply(label, axis=1)


def load_extents(extents_dir: Path, event_filter: list[str] | None = None):
    """All flood polygons under extents_dir as GeoDataFrame(event, geometry).
    Reads every layer of every vector source (incl. multi-layer FileGDBs like
    EGS_Flood_Product_Archive.gdb). event_filter: keep events whose label
    contains ANY of these substrings (e.g. ["2013", "2017", "2021"])."""
    import geopandas as gpd
    import pandas as pd
    parts = []
    patterns = ["*.shp", "*.gpkg", "*.geojson", "*.json"]
    files = [f for pat in patterns for f in sorted(Path(extents_dir).rglob(pat))]
    gdbs = [d for d in sorted(Path(extents_dir).rglob("*.gdb")) if d.is_dir()]
    if Path(extents_dir).suffix.lower() == ".gdb":
        gdbs.append(Path(extents_dir))
    for src in files + gdbs:
        layers = _layers_of(src) if str(src).lower().endswith((".gdb", ".gpkg")) \
            else [None]
        for layer in layers:
            try:
                g = gpd.read_file(src, layer=layer) if layer else gpd.read_file(src)
            except Exception as e:
                print(f"WARNING: skipping {Path(src).name}:{layer}: {e}")
                continue
            if g.empty:
                continue
            if g.crs is None:
                g = g.set_crs("EPSG:4326")
            g = g.to_crs("EPSG:4326")
            g = g[g.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
            if g.empty:
                continue
            ev = _event_from_attrs(g, layer, src)
            g = g[["geometry"]].copy()
            g["event"] = ev.values
            parts.append(g)
    if not parts:
        raise FileNotFoundError(
            f"No polygon vector data under {extents_dir}.")
    out = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True),
                           crs="EPSG:4326")
    labels = sorted(out["event"].astype(str).unique())
    print(f"Events available ({len(labels)}): {labels[:25]}"
          f"{' ...' if len(labels) > 25 else ''}")
    if event_filter:
        keep = out["event"].str.contains("|".join(event_filter), case=False,
                                         na=False)
        out = out[keep]
        if out.empty:
            raise ValueError(
                f"No events matched filter {event_filter}. Labels look like: "
                f"{labels[:10]} — adjust --events to substrings of these, or "
                "omit --events and rely on --max-events.")
    return out


def sample_points(
    extents,                      # GeoDataFrame from load_extents
    n_wet_per_event: int = 40,
    n_dry_per_event: int = 40,
    dry_buffer_km: float = 0.3,   # dry points start this far outside the flood
    dry_ring_km: float = 3.0,     # ...and end this far
    seed: int = 20260713,
) -> list[LabeledPoint]:
    from shapely.geometry import Point
    from shapely.ops import unary_union
    rng = np.random.default_rng(seed)
    out: list[LabeledPoint] = []
    deg = 1 / 111.0               # ~km -> degrees (fine at sampling precision)
    for event, grp in extents.groupby("event"):
        poly = unary_union(list(grp.geometry))
        wet_zone = poly
        dry_zone = poly.buffer(dry_ring_km * deg).difference(
            poly.buffer(dry_buffer_km * deg))
        for zone, flooded, target in ((wet_zone, True, n_wet_per_event),
                                      (dry_zone, False, n_dry_per_event)):
            minx, miny, maxx, maxy = zone.bounds
            got, tries = 0, 0
            while got < target and tries < target * 400:
                tries += 1
                p = Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy))
                if zone.contains(p):
                    out.append(LabeledPoint(p.y, p.x, flooded, event))
                    got += 1
            if got < target:
                print(f"WARNING: {event}: sampled only {got}/{target} "
                      f"{'wet' if flooded else 'dry'} points")
    return out
