"""v0.13 wiring: pipeline -> satellite map section.
FAIL-OPEN: any exception in here must degrade to 'no map + note', never kill a
report. The extent stack ships EMPTY until the EGS rasterizer lands (M-SAT-1
exit); the map is then model-surface-only and its provenance says so."""
from __future__ import annotations
import base64
from pathlib import Path
import numpy as np
from scipy import ndimage

from . import params_sat as P
from .aoi import AOI
from .extent_stack import ExtentStack
from .surface import fuse
from .render import render_map
from .integrity import run_all


def hand_raster(terrain, min_area_km2: float):
    """Full-window HAND from an existing TerrainModel solve (Euclidean-nearest
    channel cell, same philosophy as hand_at_scale; same window cap so a big
    river clipped by the window can't fake a tiny accumulation)."""
    area = terrain.acc * terrain.cell_km2
    cap = 0.25 * terrain.max_stream_area_km2
    target = float(min(min_area_km2, max(cap, terrain.cell_km2 * 4)))
    chan = area >= target
    if not chan.any():
        chan = area >= np.quantile(area, 0.999)
    _, idx = ndimage.distance_transform_edt(~chan, return_indices=True)
    hand = terrain.filled - terrain.filled[idx[0], idx[1]]
    return np.clip(hand, 0.0, None), target


def to_aoi_grid(arr: np.ndarray, cell_m: float, aoi_n: int) -> np.ndarray:
    """Center 1.5 km crop of the 6 km/30 m window, upsampled to the 10 m grid."""
    half_px = max(int(round(aoi_n * P.SAT_GRID_RES_M / (2.0 * cell_m))), 1)
    r0, c0 = arr.shape[0] // 2, arr.shape[1] // 2
    crop = arr[max(r0 - half_px, 0):r0 + half_px,
               max(c0 - half_px, 0):c0 + half_px]
    f = max(int(round(cell_m / P.SAT_GRID_RES_M)), 1)
    up = np.kron(crop, np.ones((f, f)))
    if up.shape[0] > aoi_n:
        s = (up.shape[0] - aoi_n) // 2; up = up[s:s + aoi_n]
    if up.shape[1] > aoi_n:
        s = (up.shape[1] - aoi_n) // 2; up = up[:, s:s + aoi_n]
    pr, pc = aoi_n - up.shape[0], aoi_n - up.shape[1]
    if pr or pc:
        up = np.pad(up, ((0, max(pr, 0)), (0, max(pc, 0))), mode="edge")
    return up


def gauge_case(matched_fallback: bool, is_regulated: bool, n_years: int) -> str:
    if matched_fallback:
        return "GAUGE_FAR"
    if is_regulated:
        return "GAUGE_REGULATED"
    if n_years < 25:
        return "GAUGE_SHORT"
    return "GAUGE_OK"


def build_map_section_html(*, terrain, lat, lon, geocode_precision_m, est,
                           risk_class, case, ams, threshold, target_km2,
                           out_dir: Path, constants_stamp: str,
                           extent_layers=None, occurrence_pct=None):
    aoi = AOI(lat, lon, geocode_precision_m=geocode_precision_m)
    hraw, used_target = hand_raster(terrain, target_km2)
    hand = to_aoi_grid(hraw, terrain.cell, aoi.n)

    # prior: model AEP at the address, e-folded over HAND relative to the
    # address's own HAND (disclosed interim form; Lever 3 replaces it)
    hand_c = float(hand[aoi.address_rc])
    aep_c = float(np.clip(est.aep_central, 1e-4, 0.25))
    prior = np.clip(aep_c * np.exp(-(hand - hand_c) / P.SAT_PRIOR_HAND_EFOLD_M),
                    0.0, 0.25)

    # event rate from the gauge AMS vs the report's own flood threshold
    flows = ams.flows_array()
    lam = float((flows >= threshold).sum()) / max(len(flows), 1) \
        if threshold else 0.05

    stack = ExtentStack(aoi.n, hand)
    if occurrence_pct is not None:
        stack.set_permanent_water_from_occurrence(occurrence_pct)
    for layer in (extent_layers or []):
        stack.add(layer)
    n_eff = float(len({l.event_id for l in stack.admitted_layers()}))

    fusion = fuse(prior, stack.wet_event_count(), n_eff, max(lam, 1e-6),
                  gauge_case=case)
    png = out_dir / "flood_map.png"
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = render_map(aoi, fusion, hand, stack, str(png),
                      constants_stamp=constants_stamp,
                      date_range="model surface (extent stack: pending M-SAT-1)",
                      grade=risk_class)
    meta["integrity"] = run_all(risk_class, fusion, stack, meta,
        bumped=_aep_bumped(float(est.aep_central), risk_class))
    meta["gauge_case"] = case
    meta["prior"] = (f"anchored at address AEP {aep_c:.3f}, HAND e-fold "
                     f"{P.SAT_PRIOR_HAND_EFOLD_M} m, channel scale "
                     f"{used_target:.1f} km2")

    b64 = base64.b64encode(png.read_bytes()).decode()
    caption = (f"Probability surface: model prior ({meta['prior']}) fused with "
               f"{stack.provenance()}; gauge case {case}. Bins match the AEP "
               "language above; hatching marks wide credible intervals.")
    if meta["mode"] == "susceptibility":
        caption = ("RELATIVE SUSCEPTIBILITY (terrain only) — no probability is "
                   "implied; no representative gauge and no observed extents.")
    html = (f'<h2>Flood risk map</h2><img src="data:image/png;base64,{b64}" '
            f'alt="flood risk map" style="max-width:100%">'
            f'<p class="small">{caption}</p>')
    return html, meta


def _aep_bumped(aep, cls):
    """True when the risk class exceeds what AEP alone justifies (terrain
    bump); the map audit must know, exactly as the fire audit does."""
    from .. import params as _P
    pure = ("High" if aep >= _P.RISK_HIGH_AEP else
            "Medium" if aep >= _P.RISK_MED_AEP else "Low")
    order = {"Low": 0, "Medium": 1, "High": 2}
    return order.get(cls, 0) > order[pure]
