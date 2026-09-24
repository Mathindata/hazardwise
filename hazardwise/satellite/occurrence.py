"""JRC Global Surface Water occurrence (30 m, 1984-2021) as the satellite-only
frequency backbone: empirical wet-probability as a function of HAND, transferred
to the address along the terrain coordinate. Loaders accept injected arrays so
everything is offline-testable; the live reader streams COG-style windows over
HTTP range requests (no tile download), same philosophy as MRDEM."""
from __future__ import annotations
import numpy as np
from . import params_sat as P
from .frequency import wilson_interval

JRC_TILE_URL = ("https://storage.googleapis.com/global-surface-water/"
                "downloads2021/occurrence/occurrence_{lon}{ew}_{lat}{ns}"
                "v1_4_2021.tif")

def tile_url(lat: float, lon: float) -> str:
    lon0 = int(np.floor(lon / 10.0) * 10)
    lat0 = int(np.ceil(lat / 10.0) * 10)
    return JRC_TILE_URL.format(lon=abs(lon0), ew="W" if lon0 < 0 else "E",
                               lat=abs(lat0), ns="N" if lat0 >= 0 else "S")

class JrcOccurrence:
    """Live reader (user machine): rasterio range-request window, EPSG:4326."""
    def window(self, lat: float, lon: float, half_km: float):
        import rasterio
        from rasterio.windows import from_bounds
        dlat = half_km * 1000.0 / 111320.0
        dlon = dlat / max(np.cos(np.deg2rad(lat)), 1e-6)
        with rasterio.open("/vsicurl/" + tile_url(lat, lon)) as ds:
            w = from_bounds(lon - dlon, lat - dlat, lon + dlon, lat + dlat,
                            ds.transform)
            arr = ds.read(1, window=w).astype(float)
        cell_m = abs(ds.transform.a) * 111320.0 * np.cos(np.deg2rad(lat))
        arr[arr > 100] = np.nan            # 255 = nodata
        return arr, float(cell_m)

def occurrence_aep_curve(occ_pct: np.ndarray, hand_m: np.ndarray):
    """Per-HAND-band annual wet probability with honest intervals.
    Returns dict(centers, p, lo, hi, n_px) or None if unusable."""
    occ = np.asarray(occ_pct, float); hand = np.asarray(hand_m, float)
    valid = np.isfinite(occ) & (occ >= 0) & (occ < P.SAT_PERMWATER_PCT) \
        & np.isfinite(hand)
    edges = np.arange(0.0, P.SAT_CURVE_MAX_M + P.SAT_CURVE_BAND_M,
                      P.SAT_CURVE_BAND_M)
    centers = 0.5 * (edges[:-1] + edges[1:])
    p = np.full(len(centers), np.nan); lo = p.copy(); hi = p.copy()
    n_px = np.zeros(len(centers), int)
    for i, (a, b) in enumerate(zip(edges[:-1], edges[1:])):
        m = valid & (hand >= a) & (hand < b)
        n_px[i] = int(m.sum())
        if n_px[i] < P.SAT_CURVE_MIN_PX:
            continue
        v = np.clip(occ[m] / 100.0 * P.SAT_OCC_TO_ANNUAL, 0.0, 0.5)
        c = float(v.mean())
        n_eff = max(n_px[i] / P.SAT_PX_DECORR, 1.0)
        wl, wh = wilson_interval(c * n_eff, n_eff)
        s10, s90 = np.percentile(v, [10, 90])
        p[i] = c
        lo[i] = min(float(wl), float(s10)); hi[i] = max(float(wh), float(s90))
    if not np.isfinite(p).any():
        return None
    ok = np.isfinite(p)
    for arr in (p, lo, hi):                       # interpolate thin bands
        arr[~ok] = np.interp(centers[~ok], centers[ok], arr[ok])
    # physics: wet probability cannot INCREASE with height above the channel
    for arr in (p, lo, hi):
        arr[:] = np.maximum.accumulate(arr[::-1])[::-1]
    return {"centers": centers, "p": p, "lo": lo, "hi": hi, "n_px": n_px}

def eval_curve(curve, h):
    return float(np.interp(h, curve["centers"], curve["p"]))

def curve_ci(curve, h):
    return (float(np.interp(h, curve["centers"], curve["lo"])),
            float(np.interp(h, curve["centers"], curve["hi"])))

def prior_raster(curve, hand_m: np.ndarray) -> np.ndarray:
    return np.interp(hand_m, curve["centers"], curve["p"])
