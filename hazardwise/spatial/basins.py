"""Basin-membership gauge selection (roadmap M1).

Uses ECCC's National Hydrometric Network Basin Polygons: the actual drainage
area of each WSC station. If the property point lies INSIDE a station's basin
polygon, the property's local streams drain toward that gauge — a hydrological
relationship, not a distance guess.

Selection rule: among containing basins with usable records, take the
SMALLEST drainage area first (the most local gauged watercourse — closest in
scale to the stream that would actually flood the property) and the next
containing basin second (the larger river system). Proximity remains only as
an explicit, caveated fallback for coastal strips and small independent
creeks with no gauged catchment.

The shapefiles are downloaded once by setup_data and cached to GeoParquet for
fast startup (~8,000 polygons).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..data.hydat import DEFAULT_DATA_DIR

BASINS_DIR_NAME = "basins"
CACHE_NAME = "wsc_basins.parquet"


@dataclass(frozen=True)
class BasinMatch:
    station_number: str
    basin_area_km2: float
    method: str      # "containment" | "proximity-fallback"


class BasinIndex:
    def __init__(self, data_dir: Path | None = None):
        import geopandas as gpd
        base = (data_dir or DEFAULT_DATA_DIR) / BASINS_DIR_NAME
        cache = base / CACHE_NAME
        if cache.exists():
            self.gdf = gpd.read_parquet(cache)
        else:
            sources: list[tuple[str, str | None]] = []   # (path, layer)
            for pat in ("*.shp", "*.gpkg", "*.geojson", "*.json"):
                for f in sorted(base.rglob(pat)):
                    if f.suffix == ".gpkg":
                        try:
                            from pyogrio import list_layers
                            for lyr in list_layers(f)[:, 0]:
                                sources.append((str(f), str(lyr)))
                        except Exception:
                            sources.append((str(f), None))
                    else:
                        sources.append((str(f), None))
            for d in sorted(base.rglob("*.gdb")):
                if d.is_dir():
                    try:
                        from pyogrio import list_layers
                        for lyr in list_layers(d)[:, 0]:
                            sources.append((str(d), str(lyr)))
                    except Exception:
                        sources.append((str(d), None))
            if not sources:
                raise FileNotFoundError(
                    f"No basin polygons under {base}. "
                    "Run:  python -m hazardwise.setup_data --basins")
            import pandas as pd
            parts = []
            for path, layer in sources:
                try:
                    g = gpd.read_file(path, layer=layer) if layer else \
                        gpd.read_file(path)
                except Exception as e:
                    print(f"WARNING: could not read {path} (layer={layer}): {e}")
                    continue
                if g.empty or g.geometry.isna().all():
                    continue
                if g.crs is None:
                    print(f"WARNING: {Path(path).name} has no CRS — assuming "
                          "EPSG:4326; verify with the diagnostic CLI")
                    g = g.set_crs("EPSG:4326")
                try:
                    parts.append(self._normalise(g.to_crs("EPSG:4326")))
                except ValueError as e:
                    print(f"WARNING: skipping {Path(path).name} "
                          f"(layer={layer}): {e}")
            if not parts:
                raise ValueError(f"Vector files under {base} contained no "
                                 "readable polygon layers.")
            gdf = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True))
            gdf.to_parquet(cache)
            self.gdf = gdf
        self.sindex = self.gdf.sindex

    @staticmethod
    def _normalise(gdf):
        """ECCC column naming varies across releases; normalise to
        station / area_km2."""
        cols = {c.upper(): c for c in gdf.columns}
        stn = next((cols[k] for k in
                    ("STATIONNUM", "STATION_NU", "STATION_NUMBER", "STATION",
                     "STATIONID", "STATION_ID", "STNID", "STN_ID", "WSC_STN",
                     "ID") if k in cols), None)
        if stn is None:
            # regex fallback: WSC ids look like 05BL004 / 02HC022 / 08MH029
            import re as _re
            pat = _re.compile(r"^\d{2}[A-Z]{2}\d{3}[A-Z0-9]?$")
            best, best_frac = None, 0.0
            for col in gdf.columns:
                if col == gdf.geometry.name:
                    continue
                vals = gdf[col].astype(str).str.strip().head(200)
                frac = vals.apply(lambda v: bool(pat.match(v))).mean()
                if frac > best_frac:
                    best, best_frac = col, frac
            if best is not None and best_frac > 0.8:
                stn = best
            else:
                raise ValueError(
                    f"No station-number column among {list(gdf.columns)} "
                    "(regex fallback found none either)")
        gdf = gdf.rename(columns={stn: "station"})
        area = next((cols[k] for k in ("AREA_KM2", "AREA", "SHAPE_AREA",
                                       "DRAINAGE_A", "AREAKM2") if k in cols), None)
        if area:
            gdf = gdf.rename(columns={area: "area_km2"})
        else:
            eq = gdf.to_crs("EPSG:3979")
            gdf["area_km2"] = eq.geometry.area / 1e6
        return gdf[["station", "area_km2", "geometry"]]

    def containing(self, lat: float, lon: float) -> list[BasinMatch]:
        from shapely.geometry import Point
        pt = Point(lon, lat)
        # bbox candidates from the tree, then an EXPLICIT polygon.contains(pt)
        # check — v0.3 relied on a predicate whose direction silently matched
        # nothing in the field; never again.
        cand = list(self.sindex.query(pt))
        rows = self.gdf.iloc[cand]
        out = [BasinMatch(str(r.station).strip(), float(r.area_km2), "containment")
               for r in rows.itertuples() if r.geometry.contains(pt)]
        out.sort(key=lambda m: m.basin_area_km2)   # most local first
        return out


def main() -> int:
    """Diagnostic:  python -m hazardwise.spatial.basins LAT LON"""
    import sys
    if len(sys.argv) != 3:
        print("usage: python -m hazardwise.spatial.basins LAT LON"); return 2
    lat, lon = float(sys.argv[1]), float(sys.argv[2])
    try:
        bi = BasinIndex()
    except FileNotFoundError as e:
        print(f"NOT INSTALLED: {e}"); return 1
    print(f"Loaded {len(bi.gdf):,} basin polygons; CRS={bi.gdf.crs}")
    m = bi.containing(lat, lon)
    if not m:
        print("No gauged catchment contains this point.")
    for x in m[:10]:
        print(f"  {x.station_number}  {x.basin_area_km2:>12,.0f} km²")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
