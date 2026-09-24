"""M-FIRE-1: NBAC burn-perimeter connector.

Diagnostic:  python -m hazardwise.satellite.nbac LAT LON
 Reads any locally cached NBAC
vector file (gpkg/shp/gdb/zip) from %USERPROFILE%\\.hazardwise\\nbac (or
$HW_NBAC_DIR), bbox-filters to the AOI, and rasterizes per-YEAR masks onto
the analysis grid. Download once from the CWFIS datamart; the loader tells
you exactly what to do if the file is missing. Fail-open: the pipeline
degrades to grade-D screening with the loader's message as a caveat."""
from __future__ import annotations
import os
from pathlib import Path
import numpy as np

DATAMART = "https://cwfis.cfs.nrcan.gc.ca/datamart (NBAC, any vector format)"

class NbacUnavailable(RuntimeError):
    pass

def nbac_dir() -> Path:
    return Path(os.environ.get("HW_NBAC_DIR",
                               Path.home() / ".hazardwise" / "nbac"))

SINGLE_YEAR = __import__("re").compile(r"NBAC_(\d{4})_\d{8}", __import__("re").I)
MERGED = __import__("re").compile(r"NBAC_(\d{4})to(\d{4})", __import__("re").I)

def _find_vector():
    """Prefer the MERGED multi-year composite; a single-year file silently
    zeroes the record and grades everything Low, so it is rejected loudly."""
    d = nbac_dir()
    if not d.exists():
        return None
    hits = []
    for pat in ("*.gpkg", "*.shp", "*.gdb", "*.zip"):
        hits += sorted(d.glob(pat))
    if not hits:
        return None
    merged = [h for h in hits if MERGED.search(h.name)]
    if merged:
        return merged[0]
    only_single = [h for h in hits if SINGLE_YEAR.search(h.name)]
    if only_single and len(only_single) == len(hits):
        raise NbacUnavailable(
            f"{only_single[0].name} is a SINGLE-YEAR NBAC file — one year "
            "of burns cannot ground a frequency estimate and would grade "
            "everything Low. Download the MERGED composite "
            "(NBAC_1972toYYYY_*_shp.zip) from "
            "https://cwfis.cfs.nrcan.gc.ca/downloads/nbac/ and remove the "
            "single-year file.")
    return hits[0]

def _record_years(max_year):
    from . import params_sat as P
    end = max(int(max_year or 0), P.FIRE_NBAC_END_YEAR_MIN)
    return end - P.FIRE_NBAC_START_YEAR + 1

def load_layers(lat, lon, half_km, aoi_n, res_m):
    """-> (layers [(bool mask, year), ...] on the AOI grid, n_record_years)"""
    src = _find_vector()
    if src is None:
        raise NbacUnavailable(
            f"No NBAC file found in {nbac_dir()}. Download the National "
            f"Burned Area Composite from {DATAMART} and place it there "
            f"(or set HW_NBAC_DIR).")
    import geopandas as gpd
    import pyproj
    from rasterio.features import rasterize
    from rasterio.transform import from_bounds
    from .aoi import AOI
    a = AOI(lat, lon, half_km=half_km)
    w, s, e, n = a.bounds_lonlat()

    def _detect_crs(path):
        try:
            import pyogrio
            info = pyogrio.read_info(str(path))
            if info.get("crs"):
                return info["crs"]
        except Exception:
            pass
        try:
            return gpd.read_file(path, rows=1).crs
        except Exception:
            return None

    paths = [src] + ([f"zip://{src}"] if str(src).endswith(".zip") else [])
    # NBAC national products ship in EPSG:3978 (Canada Atlas Lambert, metres).
    # A bbox in the wrong CRS silently selects NOTHING, so we try the detected
    # CRS first, then 3978, then 4326, and record every attempt for diagnosis.
    gdf, attempts = None, []
    for path in paths:
        crs0 = _detect_crs(path)
        for crs in dict.fromkeys([str(crs0) if crs0 else None,
                                  "EPSG:3978", "EPSG:4326"]):
            if crs is None:
                continue
            try:
                if crs != "EPSG:4326":
                    tr = pyproj.Transformer.from_crs("EPSG:4326", crs,
                                                     always_xy=True)
                    xs, ys = tr.transform([w, e], [s, n])
                    bbox = (min(xs), min(ys), max(xs), max(ys))
                else:
                    bbox = (w, s, e, n)
                cand = gpd.read_file(path, bbox=bbox)
                attempts.append(f"{Path(str(path)).name} @ {crs}: "
                                f"{len(cand)} features")
                if len(cand):
                    gdf = cand
                    break
            except Exception as ex:
                attempts.append(f"{Path(str(path)).name} @ {crs}: "
                                f"{type(ex).__name__}")
        if gdf is not None:
            break
    if gdf is None or gdf.empty:
        # A readable file with zero features here is a VALID result (urban
        # cores really have no burns within 1.5 km) — but only if at least
        # one attempt actually read the file. Otherwise, fail loudly.
        if not any(": 0 features" in t or " features" in t for t in attempts):
            raise NbacUnavailable(
                f"Could not read {src.name}; attempts: {attempts}. If this "
                "is a zip containing a .gdb, extract it into the folder.")
        return [], _record_years(None)
    gdf = gdf.to_crs("EPSG:4326")
    yf = next((c for c in gdf.columns
               if c.upper() in ("YEAR", "FIRE_YEAR", "FIREYEAR", "YEAR_")),
              None)
    if yf is None:
        raise NbacUnavailable(f"No YEAR field in {src.name}; columns: "
                              f"{list(gdf.columns)[:8]}")
    transform = from_bounds(w, s, e, n, aoi_n, aoi_n)
    layers = []
    for yr, grp in gdf.groupby(gdf[yf].astype(int)):
        mask = rasterize(((g, 1) for g in grp.geometry if g is not None),
                         out_shape=(aoi_n, aoi_n), transform=transform,
                         fill=0, dtype="uint8").astype(bool)
        if mask.any():
            layers.append((mask, int(yr)))
    m = MERGED.search(Path(str(src)).name)
    if m:   # trust the merged filename for the record span (window subset
            # cannot see the archive's true coverage)
        n_rec = int(m.group(2)) - int(m.group(1)) + 1
    else:
        n_rec = _record_years(int(gdf[yf].astype(int).max()))
    return layers, n_rec


if __name__ == "__main__":
    import sys
    lat, lon = float(sys.argv[1]), float(sys.argv[2])
    print("NBAC cache dir:", nbac_dir())
    print("File found:    ", _find_vector())
    try:
        layers, n_rec = load_layers(lat, lon, 1.5, 300, 10.0)
        print(f"Record length:  {n_rec} years")
        print(f"Perimeter-years in the 3 km window at ({lat}, {lon}): "
              f"{len(layers)}")
        for m, y in layers:
            print(f"  {y}: {int(m.sum())} burned pixels (10 m grid)")
        if not layers:
            print("  (zero burns here is a valid graded-Low input, not an "
                  "error — try Jasper 52.8734 -118.0806 to confirm the "
                  "loader itself works)")
    except Exception as e:
        print("LOADER FAILED:", e)
