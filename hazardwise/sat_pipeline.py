"""Satellite-only pipeline (v0.14):
  python -m hazardwise.sat_pipeline "ADDRESS"        (or --lat/--lon)
Address -> geocode -> MRDEM HAND -> JRC water-occurrence history ->
occurrence-vs-HAND frequency curve -> fusion with observed flood extents ->
risk class + credible interval + index table + map. NO gauge, NO HYDAT:
the evidence grade is capped at C by construction (D without occurrence)."""
from __future__ import annotations
import argparse, sys
from pathlib import Path
import numpy as np

from . import params as P
from . import pipeline as _pl                      # classify_risk, geocoding
from .satellite import params_sat as SP
from .satellite import wiring as satw
from .satellite import occurrence as socc
from .satellite.aoi import AOI
from .satellite.extent_stack import ExtentStack
from .satellite.surface import fuse
from .satellite.render import render_map
from .satellite.integrity import run_all
from .reporting.sat_report import render_sat_report, slugify

SOURCES = [
    ("EC JRC / Google — Global Surface Water v1.4 (occurrence 1984-2021)",
     "https://global-surface-water.appspot.com/"),
    ("NRCan — Emergency Geomatics Service flood extents (SAR-derived)",
     "https://open.canada.ca/data/en/dataset/74144824-206e-4cea-9fb9-72925a128189"),
    ("NRCan — MRDEM 30 m Digital Terrain Model (CanElevation)",
     "https://open.canada.ca/data/en/dataset/18752265-bda3-498c-a4ba-9dfe68cb98da"),
    ("OpenStreetMap Nominatim (geocoding)",
     "https://nominatim.openstreetmap.org/"),
]

def _stamp() -> str:
    return (f"Model constants: satellite-only defaults (occ_to_annual="
            f"{SP.SAT_OCC_TO_ANNUAL}, px_decorr={SP.SAT_PX_DECORR}, "
            f"prior_w=8, channel_km2={SP.SAT_ONLY_CHANNEL_KM2})")

def stack_layers_years(layers):
    """Adapt observed-extent layers to (mask, year) tuples for the history
    summarizer; returns [] when none are present."""
    out = []
    for l in (layers or []):
        yr = getattr(l, "date", None)
        out.append((l.mask, yr.year if yr is not None else 0))
    return out

def run_sat_report(address, *, coords=None, out_root=Path("reports_sat"),
                   dem=None, occ=None, occ_source=None, extent_layers=None,
                   geocoder=None,
                   render_kwargs=None,
                   region_km=None, debug=None):
    # 1 -- locate
    if coords:
        geo = _pl.GeocodeResult(coords[0], coords[1], address, "A", 0,
                                "coordinates supplied directly")
    else:
        if geocoder is None:
            from .geocoding.rural import RuralAwareGeocoder
            geocoder = RuralAwareGeocoder()
        geo = geocoder.geocode(address)
    from . import params as _P
    if region_km is None:
        region_km = _P.FIRE_REGIONAL_RATE_KM
    from .debuglog import DebugLog
    dbg = debug if isinstance(debug, DebugLog) else DebugLog(enabled=bool(debug))
    dbg.log("geocode", address=address, lat=geo.latitude, lon=geo.longitude,
            method=getattr(geo, "method", ""), region_km=region_km)
    gated = any(t in (geo.method or "").lower()
                for t in ("centroid", "locality", "town"))

    # 2 -- terrain (same solve as the gauged pipeline)
    from .terrain.dem import CogDem
    from .terrain.hand import TerrainModel
    dem_src = dem or CogDem()
    arr, cell = dem_src.window(geo.latitude, geo.longitude, half_km=6.0)
    terrain = TerrainModel(arr, cell)
    aoi = AOI(geo.latitude, geo.longitude,
              geocode_precision_m=250.0 if gated else 15.0)
    hraw, used_km2 = satw.hand_raster(terrain, SP.SAT_ONLY_CHANNEL_KM2)
    hand = satw.to_aoi_grid(hraw, terrain.cell, aoi.n)
    fill = satw.to_aoi_grid(terrain.fill_depth, terrain.cell, aoi.n)
    hc = float(hand[aoi.address_rc]); fill_c = float(fill[aoi.address_rc])

    # 3 -- flood history: JRC occurrence window (satellite optical, 1984-2021)
    if occ is None and occ_source is not None:
        o_arr, o_cell = occ_source.window(geo.latitude, geo.longitude,
                                          aoi.half_km)
        occ = satw.to_aoi_grid(o_arr, o_cell, aoi.n)
    curve = socc.occurrence_aep_curve(occ, hand) if occ is not None else None

    # 4 -- observed flood extents (EGS/EMS/S1; SAR-derived)
    stack = ExtentStack(aoi.n, hand)
    if occ is not None:
        stack.set_permanent_water_from_occurrence(occ)
    for layer in (extent_layers or []):
        stack.add(layer)
    n_events = len({l.event_id for l in stack.admitted_layers()})

    chain, caveats, indices = [], [], {}
    out_dir = out_root / slugify(address)

    if curve is not None:
        prior = socc.prior_raster(curve, hand)
        lam = max(float(np.nanmax(curve["p"])), 1e-3)   # events/yr proxy at channel
        fusion = fuse(prior, stack.wet_event_count(), float(n_events), lam,
                      gauge_case="SATELLITE_ONLY")
        aep = float(fusion.aep[aoi.address_rc])
        clo, chi = socc.curve_ci(curve, hc)
        aep_lo = min(clo, float(fusion.aep_lo[aoi.address_rc]))
        aep_hi = max(chi, float(fusion.aep_hi[aoi.address_rc]))
        aep = float(min(aep, P.AEP_DISPLAY_CAP))
        aep_hi = float(min(max(aep_hi, aep), P.AEP_DISPLAY_CAP))
        aep_lo = float(min(aep_lo, aep))
        risk = _pl.classify_risk(aep, hc, fill_c)
        grade = SP.SAT_ONLY_GRADE
        chain += [
            "Satellite-only mode: no hydrometric gauge or HYDAT record was "
            "used; every quantity below is observable from space + terrain.",
            f"Terrain: HAND computed on the 30 m national DEM (MRDEM), "
            f"channel scale {used_km2:.1f} km2; this address sits "
            f"{hc:.1f} m above the nearest mapped channel.",
            "Flood history: 38 years of optical water observations (JRC "
            "Global Surface Water, 1984-2021) were binned by HAND to give an "
            "empirical wet-probability curve for THIS reach; monotonicity in "
            "height is enforced (water cannot prefer higher ground).",
            f"The curve evaluated at the address's HAND gives the central "
            f"annual probability {aep:.1%}; the credible range "
            f"{aep_lo:.1%}-{aep_hi:.1%} combines within-band spread, "
            f"spatial-correlation-deflated sample size, and the Bayesian "
            f"posterior.",
            (f"Observed flood extents: {stack.provenance()} fused into the "
             f"surface (Beta-Binomial).") if n_events else
            "Observed flood extents: none available for this window yet — "
            "the estimate rests on the occurrence history alone.",
            _stamp(),
        ]
        caveats += [
            "No in-situ gauge: the evidence grade is capped at C by "
            "construction. A gauged HazardWise report supersedes this one "
            "wherever it exists.",
            "Optical occurrence under-counts short-duration and under-cloud "
            "floods, so the central probability is best read as a LOWER "
            "bound of that evidence channel; the upper credible bound "
            "carries that asymmetry.",
            "SAR/optical water mapping is unreliable under dense canopy and "
            "in built-up cores; occurrence there reads low regardless of "
            "true flooding.",
            "The occurrence-to-annual-probability mapping is an interim "
            "disclosed form (constant above); it is replaced, not tuned.",
        ]
    else:
        prior = np.zeros_like(hand)
        fusion = fuse(prior, np.zeros_like(hand, int), 0.0, 1e-3,
                      gauge_case="UNGAUGED")
        fusion.mode = "susceptibility"
        aep = aep_lo = aep_hi = 0.0
        risk = "Undetermined"
        grade = SP.SAT_ONLY_GRADE_NO_OCC
        chain += [
            "Satellite-only mode with NO usable water-occurrence history in "
            "this window: only terrain screening is possible.",
            f"HAND at the address: {hc:.1f} m (channel scale "
            f"{used_km2:.1f} km2).", _stamp()]
        caveats += ["No probability is estimated; the map shows relative "
                    "terrain susceptibility only. Grade D: screening, not "
                    "assessment."]

    if fill_c > P.DEPRESSION_BUMP_M:
        caveats.append("This property sits in a closed depression "
                       "(diked/pumped or ponding land): frequencies "
                       "understate consequence severity here.")
    if gated:
        caveats.append("Geocoding resolved only to a locality centroid; the "
                       "map shows an uncertainty circle, not a parcel.")

    # indices table
    dist_pw = float("nan")
    if stack.permanent_water.any():
        from scipy import ndimage as _ndi
        d = _ndi.distance_transform_edt(~stack.permanent_water) * aoi.res_m
        dist_pw = float(d[aoi.address_rc])
    occ_here = (float(occ[aoi.address_rc]) if occ is not None else float("nan"))
    # v0.21 regional flood history: EGS extents are per-event; until that
    # connector lands, occurrence is a 38-yr frequency, NOT dated events, so
    # we say so rather than inventing years.
    try:
        from .satellite.history import fire_history, summarize
        flood_ev = [e for e in fire_history(geo.latitude, geo.longitude,
                    wide_km=25.0, nbac_layers=[])]
    except Exception:
        flood_ev = []
    if flood_ev:
        chain.append("Regional flood history (25 km window): "
                     + " ".join(summarize(flood_ev, "observed flood extents")))
    else:
        chain.append(f"Regional flood history ({region_km:.0f} km window): the "
                     "38-year JRC occurrence "
                     "record is a frequency, not a dated-event list; "
                     "per-event flood history awaits the EGS extent "
                     "connector (M-SAT-1).")
    indices.update({
        "HAND — height above nearest channel": f"{hc:.1f} m",
        "Closed-depression fill depth": f"{fill_c:.2f} m",
        "Water occurrence at address (JRC 1984-2021)":
            ("n/a" if np.isnan(occ_here) else f"{occ_here:.0f}%"),
        "Distance to permanent water":
            ("n/a" if np.isnan(dist_pw) else f"{dist_pw:,.0f} m"),
        "Observed flood events in window": str(n_events),
        "Effective satellite record": "38 yr optical (JRC) "
            + (f"+ {n_events} SAR-mapped events" if n_events else ""),
        "Annual exceedance probability (central)":
            (f"{aep:.2%}" if aep else "not estimated"),
        "Credible range":
            (f"{aep_lo:.2%} - {aep_hi:.2%}" if aep else "n/a"),
    })

    # map (same renderer, same integrity discipline)
    out_dir.mkdir(parents=True, exist_ok=True)
    rk = dict(render_kwargs or {})
    if rk.pop("street_basemap", False):
        try:
            from .satellite.basemap import fetch_street_basemap
            rk["basemap_img"] = fetch_street_basemap(
                geo.latitude, geo.longitude, aoi.half_km, aoi.n,
                style=rk.pop("basemap_style", "street"))
        except Exception as e:
            print(f"  [basemap] unavailable ({e}); using hillshade")
    meta = render_map(aoi, fusion, hand, stack,
                      str(out_dir / "flood_map.png"),
                      constants_stamp=_stamp(),
                      date_range="JRC 1984-2021" + (
                          f"; extents: {stack.provenance()}" if n_events else ""),
                      grade=risk if risk in ("High", "Medium", "Low") else None,
                      **rk)
    meta["integrity"] = run_all(
        risk if risk in ("High", "Medium", "Low") else None,
        fusion, stack, meta,
        bumped=_aep_bumped(aep, risk))
    meta["gauge_case"] = "SATELLITE_ONLY"
    import base64
    b64 = base64.b64encode((out_dir / "flood_map.png").read_bytes()).decode()
    map_html = (f'<h2>Flood risk map</h2><img style="max-width:100%" '
                f'src="data:image/png;base64,{b64}" alt="flood risk map">')
    # v0.19 auxiliary context map: topography + permanent/seasonal waters,
    # over the FULL 12 km terrain window (fail-open)
    try:
        from .satellite.aux_maps import render_flood_context
        occ_wide = None
        if occ_source is not None:
            try:
                ow, oc = occ_source.window(geo.latitude, geo.longitude, 6.0)
                occ_wide = satw.to_aoi_grid(ow, oc, arr.shape[0]) \
                    if ow.shape != arr.shape else ow
            except Exception:
                pass
        render_flood_context(arr, cell, occ_wide,
                             str(out_dir / "flood_context.png"), 6.0)
        b2 = base64.b64encode((out_dir / "flood_context.png")
                              .read_bytes()).decode()
        map_html += (f'<h3>Regional context</h3><img style="max-width:100%" '
                     f'src="data:image/png;base64,{b2}">')
    except Exception as e:
        print(f"  [aux map] skipped ({e})")

    path = render_sat_report(
        address=address,
        geocode_note=f"Resolved to ({geo.latitude:.5f}, {geo.longitude:.5f}) "
                     f"— {geo.method}",
        risk_class=risk, grade=grade, aep=aep, aep_lo=aep_lo, aep_hi=aep_hi,
        chain=chain, caveats=caveats, indices=indices, sources=SOURCES,
        map_html=map_html, map_meta=meta, out_dir=out_dir,
        latlon=(geo.latitude, geo.longitude))
    dbg.flush(address)
    return path, risk, aep

def main() -> int:
    ap = argparse.ArgumentParser(description="HazardWise satellite-only report")
    ap.add_argument("address", nargs="?")
    ap.add_argument("--lat", type=float); ap.add_argument("--lon", type=float)
    ap.add_argument("--radius", type=float, default=None,
                    help="regional radius km (default 25)")
    ap.add_argument("--debug", nargs="?", const=True, default=False)
    ap.add_argument("--out", default="reports_sat")
    a = ap.parse_args()
    if not a.address and not (a.lat and a.lon):
        ap.error("provide an address or --lat/--lon")
    label = a.address or f"site at {a.lat:.4f}, {a.lon:.4f}"
    coords = (a.lat, a.lon) if a.lat and a.lon else None
    kw = {}
    try:
        kw["occ_source"] = socc.JrcOccurrence()
    except Exception:
        pass
    path, risk, aep = run_sat_report(label, coords=coords,
                                     out_root=Path(a.out, region_km=a.radius,
        debug=('stdout' if a.debug=='stdout' else bool(a.debug))), **kw)
    print(f"Risk: {risk} | AEP {aep:.2%} | satellite-only (grade cap C)")
    print(f"Report: {path}")
    return 0

if __name__ == "__main__":
    sys.exit(main())


def _aep_bumped(aep, cls):
    """True when the risk class exceeds what AEP alone justifies (terrain
    bump); the map audit must know, exactly as the fire audit does."""
    from . import params as _P
    pure = ("High" if aep >= _P.RISK_HIGH_AEP else
            "Medium" if aep >= _P.RISK_MED_AEP else "Low")
    order = {"Low": 0, "Medium": 1, "High": 2}
    return order.get(cls, 0) > order[pure]
