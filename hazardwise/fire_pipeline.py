"""Wildfire risk pipeline, satellite/archive-only (v0.16):
    python -m hazardwise.fire_pipeline "ADDRESS"    (or --lat/--lon)
Mirrors sat_pipeline: address -> MRDEM terrain -> NBAC burn history (1972-)
+ fuel/land-cover -> annual burn probability with credible range, index
table, hatched warm-ramp map, and a line-by-line justification chain.
Grade capped C (archive-only); D = screening when no NBAC history is loaded.
LIVE NBAC/fuel connectors are M-FIRE-1: this version accepts injected layers
(tests) and documents the datamart source in every report."""
from __future__ import annotations
import argparse, base64, sys
from pathlib import Path
import numpy as np

from . import pipeline as _pl
from .satellite import params_sat as SP
from .satellite import wildfire as wf
from .satellite.aoi import AOI
from .satellite import wiring as satw
from .reporting.sat_report import render_sat_report, slugify

SOURCES = [
    ("NRCan/CWFIS — NBAC National Burned Area Composite (annual burn "
     "perimeters, 1972-present)", "https://cwfis.cfs.nrcan.gc.ca/datamart"),
    ("NRCan/CWFIS — FBP fuel type grids & Fire Weather Index climatology",
     "https://cwfis.cfs.nrcan.gc.ca/"),
    ("NASA FIRMS — VIIRS/MODIS hotspot archive (event timing/reach-back)",
     "https://firms.modaps.eosdis.nasa.gov/"),
    ("NRCan — MRDEM 30 m DTM (slope/aspect)",
     "https://open.canada.ca/data/en/dataset/18752265-bda3-498c-a4ba-9dfe68cb98da"),
    ("OpenStreetMap Nominatim (geocoding)",
     "https://nominatim.openstreetmap.org/"),
]

def _stamp():
    return (f"Model constants: fire defaults v0.22 (wui_decay={SP.FIRE_WUI_DECAY_M}"
            f" m, slope_boost={SP.FIRE_SLOPE_MAX_BOOST}, "
            f"c={SP.FIRE_PRIOR_C}, prior_w=8)")

def run_fire_report(address, *, coords=None, out_root=Path("reports_fire"),
                    dem=None, fuel=None, nbac_layers=None,
                    n_record_years=0, geocoder=None,
                    render_kwargs=None, region_km=None, debug=None):
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
    from .terrain.dem import CogDem
    dem_src = dem or CogDem()
    arr, cell = dem_src.window(geo.latitude, geo.longitude, half_km=6.0)
    aoi = AOI(geo.latitude, geo.longitude)
    dem_g = satw.to_aoi_grid(arr, cell, aoi.n)
    fuel_g = (fuel if fuel is not None
              else np.ones((aoi.n, aoi.n), bool))     # conservative default
    nbac_note = ""
    if nbac_layers is None and n_record_years == 0:
        try:
            from .satellite.nbac import load_layers
            nbac_layers, n_record_years = load_layers(
                geo.latitude, geo.longitude, aoi.half_km, aoi.n,
                SP.SAT_GRID_RES_M)
            nbac_note = (f"NBAC loaded: {len(nbac_layers)} perimeter-years "
                         f"in window, {n_record_years}-yr record.")
        except Exception as e:
            nbac_note = f"NBAC unavailable: {e}"
    layers = [(satw.to_aoi_grid(m.astype(float), SP.SAT_GRID_RES_M, aoi.n)
               >= 0.5, y) if m.shape != (aoi.n, aoi.n) else (m, y)
              for m, y in (nbac_layers or [])]
    from .satellite.history import fire_history, summarize, history_indices
    try:
        fire_ev = fire_history(geo.latitude, geo.longitude, wide_km=region_km)
    except Exception as _e:
        fire_ev = []
        dbg.log("history_error", error=str(_e))
    dbg.log("regional_history", region_km=region_km,
            n_events=len(fire_ev),
            years=sorted({e.year for e in fire_ev}, reverse=True)[:10])
    res = wf.fire_probability(dem=dem_g, cell_m=SP.SAT_GRID_RES_M,
                              fuel=fuel_g, nbac_layers=layers,
                              n_record_years=n_record_years,
                              regional_events=fire_ev, region_km=region_km)
    if res.get("mode") == "probability":
        dbg.log("rate", r_local=res["r_local"], r_hist=res["r_hist"],
                r_used=res["r"][0], rate_floored=res["rate_floored"])
        dbg.array_stats("prob", res["prob"])
    rc = aoi.address_rc
    d_wui = float(res["d_wui"][rc]); sl = float(res["slope"][rc])
    nb_d, nb_y = wf.nearest_burn(layers, rc, aoi.res_m)
    chain, caveats, indices = [], [], {}
    if res["mode"] == "probability":
        from scipy.ndimage import maximum_filter
        rad_px = int(SP.FIRE_REACH_RADIUS_M / aoi.res_m)
        reach = maximum_filter(res["prob"], size=2 * rad_px + 1)
        reach_lo = maximum_filter(res["lo"], size=2 * rad_px + 1)
        reach_hi = maximum_filter(res["hi"], size=2 * rad_px + 1)
        p = float(reach[rc]); lo = float(reach_lo[rc]); hi = float(reach_hi[rc])
        p_at = float(res["prob"][rc])
        r, r_lo, r_hi = res["r"]
        risk = wf.classify_fire(p, d_wui, nb_d)
        grade = SP.FIRE_GRADE
        chain += [
            "Wildfire, satellite/archive-only mode: no in-situ fire-weather "
            "station or provincial assessment was used.",
            f"Burn history: {len(layers)} NBAC perimeter-years over a "
            f"{n_record_years}-year record; the mean annual fraction of "
            f"burnable land burned in this window is {r:.4%} "
            f"(CI {r_lo:.4%}-{r_hi:.4%}) — the regional base rate.",
            f"Local exposure: this address is {d_wui:,.0f} m from continuous "
            f"wildland fuel (exposure e-fold {SP.FIRE_WUI_DECAY_M:.0f} m) on "
            f"{sl:.0f} deg terrain (slope multiplier up to "
            f"1+{SP.FIRE_SLOPE_MAX_BOOST}).",
            "Annual burn probability = 1 - exp(-rate x exposure x slope), "
            "updated per-pixel by observed burned-year counts (Beta-Binomial;"
            " pixel trials deflated 5x for correlation with the regional "
            "rate, which the same years estimated).",
            f"The graded number is REACH probability: the maximum within "
            f"{SP.FIRE_REACH_RADIUS_M:.0f} m of the address "
            f"({p:.2%}, vs {p_at:.2%} at the exact pixel) — a fire at the "
            "fence line is the risk to the structure.",
            (f"Reach-back: the {nb_y} fire burned to within {nb_d:,.0f} m of "
             f"this address.") if nb_y is not None else
            "Reach-back: no mapped historical burn intersects this window.",
            f"Result: {p:.2%} central (CI {lo:.2%}-{hi:.2%}) -> {risk}.",
            _stamp()]
        if res.get("rate_floored"):
            chain.insert(-1,
                f"Regional-rate floor applied: the {region_km:.0f} km burn "
                f"history implies a base rate of {res['r_hist']:.4%}, above "
                f"the {res['r'][0] if False else res['r_local']:.4%} measured "
                "inside the 3 km map window; the higher, region-supported "
                "rate is used so this number cannot contradict the history "
                "below (B2).")
        if nbac_note:
            chain.insert(1, nbac_note)
        caveats += [
            "Archive-only: grade capped at C. NBAC maps burns >~ 200 ha "
            "reliably; small/urban-interface fires are under-represented, so "
            "the central value is a lower bound of this evidence channel.",
            "No fire-weather forecast is included: this is climatological "
            "frequency, not current-season danger (CWFIS FWI is the "
            "M-FIRE-2 addition).",
            "The exposure/slope form is an interim disclosed model; it is "
            "replaced, not tuned (Lever 3)."]
        aep_out = (p, lo, hi)
    else:
        risk, grade = "Undetermined", SP.FIRE_GRADE_NO_DATA
        chain += ["No NBAC burn history loaded: terrain/fuel screening only.",
                  f"Distance to wildland fuel {d_wui:,.0f} m; slope "
                  f"{sl:.0f} deg.", _stamp()]
        caveats += ["No probability is claimed. Grade D: screening, not "
                    "assessment. Load NBAC (see sources) for a graded "
                    "report."]
        if nbac_note:
            caveats.append(nbac_note)
        aep_out = (0.0, 0.0, 0.0)
    # v0.21/0.22 regional event history (already computed above at region_km)
    try:
        hist_lines = summarize(fire_ev, "mapped fires (NBAC >=200 ha)")
        chain.append(f"Regional fire history ({region_km:.0f} km window): "
                     + " ".join(hist_lines))
    except Exception as e:
        fire_ev = []
        chain.append(f"Regional fire history unavailable ({e}).")
    indices.update({
        "Distance to continuous wildland fuel": f"{d_wui:,.0f} m",
        "Terrain slope at address": f"{sl:.0f} deg",
        "Fuel fraction within 500 m":
            f"{float(res['expo'][rc] and fuel_g[max(rc[0]-50,0):rc[0]+50, max(rc[1]-50,0):rc[1]+50].mean()):.0%}",
        "NBAC record length": f"{n_record_years} years" if n_record_years
            else "not loaded",
        "Historical burns in window": str(len(layers)),
        "Nearest historical burn":
            (f"{nb_d:,.0f} m ({nb_y})" if nb_y is not None else "none mapped"),
        **history_indices(fire_ev),
        "Annual burn probability (central)":
            (f"{aep_out[0]:.2%}" if aep_out[0] else "not estimated"),
        "Credible range": (f"{aep_out[1]:.2%} - {aep_out[2]:.2%}"
                           if aep_out[0] else "n/a")})
    out_dir = out_root / slugify(address)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = wf.render_fire_map(aoi, res, fuel_g, layers,
                              str(out_dir / "fire_map.png"), _stamp(),
                              date_range=f"{n_record_years} yr record",
                              grade=risk if risk in ("High", "Medium", "Low")
                              else None,
                              graded_p=(aep_out[0] if aep_out[0] else None),
                              bumped=(nb_y is not None and
                                      nb_d <= SP.FIRE_NEAR_BURN_BUMP_M),
                              **_rk(render_kwargs, geo, aoi))
    b64 = base64.b64encode((out_dir / "fire_map.png").read_bytes()).decode()
    map_html = (f'<h2>Wildfire risk map</h2><img style="max-width:100%" '
                f'src="data:image/png;base64,{b64}" alt="wildfire risk map">')
    try:
        from .satellite.aux_maps import render_fire_context, fetch_wind
        from .satellite.nbac import load_layers as _ll
        try:
            wide_layers, _ = _ll(geo.latitude, geo.longitude, 6.0,
                                 arr.shape[0], cell)
        except Exception:
            wide_layers = [(satw.to_aoi_grid(m.astype(float),
                            SP.SAT_GRID_RES_M, arr.shape[0]) >= 0.5, y)
                           for m, y in layers]
        wind = None
        try:
            wind = fetch_wind(geo.latitude, geo.longitude)
        except Exception:
            pass
        render_fire_context(arr, cell, wide_layers,
                            str(out_dir / "fire_context.png"), 6.0,
                            wind=wind)
        b2 = base64.b64encode((out_dir / "fire_context.png")
                              .read_bytes()).decode()
        map_html += (f'<h3>Regional context</h3><img style="max-width:100%" '
                     f'src="data:image/png;base64,{b2}">')
    except Exception as e:
        print(f"  [aux map] skipped ({e})")
    path = render_sat_report(
        address=address, geocode_note=f"({geo.latitude:.5f}, "
        f"{geo.longitude:.5f}) — {geo.method}", risk_class=risk, grade=grade,
        aep=aep_out[0], aep_lo=aep_out[1], aep_hi=aep_out[2], chain=chain,
        caveats=caveats, indices=indices, sources=SOURCES, map_html=map_html,
        map_meta=meta, out_dir=out_dir, hazard="Wildfire",
        latlon=(geo.latitude, geo.longitude))
    dbg.log("result", risk=risk, aep=aep_out[0], grade=grade)
    dbg.flush(address)
    return path, risk, aep_out[0]

def _rk(render_kwargs, geo, aoi):
    rk = dict(render_kwargs or {})
    if rk.pop("street_basemap", False):
        try:
            from .satellite.basemap import fetch_street_basemap
            rk["basemap_img"] = fetch_street_basemap(
                geo.latitude, geo.longitude, aoi.half_km, aoi.n,
                style=rk.pop("basemap_style", "street"))
        except Exception as e:
            print(f"  [basemap] unavailable ({e}); using hillshade")
    return rk

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("address", nargs="?")
    ap.add_argument("--lat", type=float); ap.add_argument("--lon", type=float)
    ap.add_argument("--radius", type=float, default=None,
                    help="regional history/rate radius km (default 25)")
    ap.add_argument("--debug", nargs="?", const=True, default=False,
                    help="write reports_debug/ trace; 'stdout' also streams")
    a = ap.parse_args()
    label = a.address or f"site at {a.lat:.4f}, {a.lon:.4f}"
    coords = (a.lat, a.lon) if a.lat and a.lon else None
    dbg = "stdout" if a.debug == "stdout" else bool(a.debug)
    path, risk, p = run_fire_report(label, coords=coords,
                                    region_km=a.radius, debug=dbg)
    print(f"Wildfire risk: {risk} | {p:.2%} | {path}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
