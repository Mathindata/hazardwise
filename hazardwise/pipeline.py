"""Pipeline v0.3: address in -> report out.

Changes from v0.2 (driven by the first validation run):
 M1  Gauge selection by BASIN MEMBERSHIP (WSC drainage polygons): the property
     must lie inside a gauge's catchment. Proximity survives only as an
     explicit, caveated fallback.
 M2  Terrain by HAND on the national 30 m DEM (MRDEM): height above the actual
     channel the property drains to, plus closed-depression detection (the
     Sumas Prairie failure mode). The 800 m relief ring survives only as a
     no-DEM fallback and caps the grade at C.
 E1  Beyond-record thresholds report as bounds, never central estimates
     (the Markham false-High fix) — handled inside models/estimate.py.

CLI unchanged:
  python -m hazardwise.pipeline "309B Macleod Trail SW, High River, AB"
  python -m hazardwise.pipeline --lat 50.58 --lon -113.87 "my cottage"
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np

from .data.hydat import DEFAULT_DATA_DIR, HydatDB
from .geocoding.geocoder import Geocoder, GeocodeResult, NominatimGeocoder
from .models.confidence import CAPolygonStatus
from .models.estimate import FloodRiskEstimate, estimate_property_flood_risk
from .reporting.report import render_report, slugify
from . import params as P

GEOCODE_GRADE_CAP = {"A": "A", "B": "B", "C": "C", "D": "D"}
# D = locality/county-centroid geocode: the parcel was never located,
# so no per-gauge grade can rescue the report (weakest-link rule).


def __getattr__(name):          # legacy constant access -> params.py
    _map = dict(RISK_HIGH_AEP="RISK_HIGH_AEP", RISK_MED_AEP="RISK_MED_AEP",
                HAND_BUMP_M="HAND_BUMP_M", DEPRESSION_BUMP_M="DEPRESSION_BUMP_M",
                MAINSTEM_MAX_KM="MAINSTEM_MAX_KM",
                GAUGE_SCALE_FRACTION="GAUGE_SCALE_FRACTION",
                GAUGE_SCALE_MIN_KM2="GAUGE_SCALE_MIN_KM2",
                GAUGE_SCALE_MAX_KM2="GAUGE_SCALE_MAX_KM2",
                STALE_RECORD_YEARS="STALE_RECORD_YEARS",
                MAX_GAUGES="MAX_GAUGES", FALLBACK_RADIUS_KM="FALLBACK_RADIUS_KM",
                AEP_DISPLAY_CAP="AEP_DISPLAY_CAP",
                AEP_DISPLAY_FLOOR="AEP_DISPLAY_FLOOR",
                REACHBACK_MEDIUM_FRACTION="REACHBACK_MEDIUM_FRACTION")
    if name in _map:
        return getattr(P, _map[name])
    raise AttributeError(name)


REGULATED_NAME_TOKENS = ("CANAL", "DIVERSION", "SPILLWAY", "FLOODWAY")


def _is_regulated_structure(name: str) -> bool:
    """True only when the WATER BODY ITSELF is a regulated structure.
    WSC names read "<WATER BODY> AT|NEAR|ABOVE|BELOW <PLACE>": 'LITTLE BOW
    CANAL AT HIGH RIVER' is a canal; 'HIGHWOOD RIVER BELOW LITTLE BOW CANAL'
    is a river whose name merely references one (v0.5 false positive)."""
    up = (name or "").upper()
    body = re.split(r"\s+(?:AT|NEAR|ABOVE|BELOW)\s+", up, maxsplit=1)[0]
    return any(t in body for t in REGULATED_NAME_TOKENS)


def _worst_grade(*grades: str) -> str:
    return max(grades, key="ABCD".index)


def classify_risk(aep: float, hand_m: float, fill_depth_m: float = 0.0) -> str:
    if aep >= P.RISK_HIGH_AEP:
        cls = "High"
    elif aep >= P.RISK_MED_AEP:
        cls = "Medium"
    else:
        cls = "Low"
    bump = (hand_m < P.HAND_BUMP_M) or (fill_depth_m > P.DEPRESSION_BUMP_M)
    if bump and cls != "High":
        cls = {"Low": "Medium", "Medium": "High"}[cls]
    return cls


def _bankfull_depth(da_km2: float | None) -> float:
    da = da_km2 if da_km2 and da_km2 > 0 else 500.0
    return max(1.0, P.K_BANKFULL * da ** P.BANKFULL_DA_EXP)


def _threshold(q2: float, hand_m: float, d_b: float) -> float:
    return q2 * ((d_b + hand_m) / d_b) ** (1.0 / P.STAGE_EXPONENT)


def run_report(
    address: str,
    *,
    geocoder: Geocoder | None = None,
    hydat: HydatDB | None = None,
    dem=None,                       # terrain.dem.CogDem-like; None -> default COG
    basins=None,                    # spatial.basins.BasinIndex-like; None -> load
    out_root: Path = Path("reports"),
    coords: tuple[float, float] | None = None,
    hydat_vintage: str | None = None,
) -> tuple[Path, str, FloodRiskEstimate]:
    if geocoder is None:
        from .geocoding.rural import RuralAwareGeocoder
        geocoder = RuralAwareGeocoder(base=NominatimGeocoder())
    own_db = hydat is None
    hydat = hydat or HydatDB()
    try:
        # 1 — locate
        if coords:
            geo = GeocodeResult(coords[0], coords[1], address, "A", 0,
                                "coordinates supplied directly")
        else:
            geo = geocoder.geocode(address)

        # 2 — gauges: basin membership first (M1)
        selection_notes: list[str] = []
        stations = []
        if basins is None:
            try:
                from .spatial.basins import BasinIndex
                basins = BasinIndex()
            except Exception as e:
                selection_notes.append(
                    f"Basin polygons unavailable ({e}); using distance fallback.")
        matched_fallback = False
        if basins is not None:
            matches = basins.containing(geo.latitude, geo.longitude)
            usable = []
            seen_ids = set()
            for m in matches:                      # sorted smallest-first
                if m.station_number in seen_ids:
                    continue
                seen_ids.add(m.station_number)
                try:
                    ams, prov = hydat.annual_maxima(m.station_number)
                except ValueError:
                    continue
                if ams.n_years >= 15:
                    usable.append((m, ams, prov))
            # Smallest containing basin (most local watercourse) AND the
            # largest containing basin WHOSE STATION IS NEAR the property.
            # v0.7 field failure: without the distance guard, High River
            # selected the Nelson River at a Manitoba generating station
            # 1,300 km away — inside whose 1.3M km² basin all of southern
            # Alberta technically lies. Hydrological truth is not hydraulic
            # relevance: the mainstem must actually flow past the property.
            from .data.hydat import haversine_km
            def _dist(m):
                info = hydat.station_info(m.station_number)
                if info is None:
                    return float("inf")
                return haversine_km(geo.latitude, geo.longitude, *info)
            # Regulated structures must be >2% smaller in basin area to win
            # the local pick: exact-float "ties" between a canal and its river
            # never actually tie (1,954.9 vs 1,955.3 km²), which let the
            # Little Bow Canal beat the Highwood twice in the field.
            usable.sort(key=lambda t: t[0].basin_area_km2 *
                        (1.02 if _is_regulated_structure(t[1].station_name)
                         else 1.0))
            by_id = {}
            if usable:
                picks = [usable[0]]
                near_big = [t for t in usable[1:]
                            if _dist(t[0]) <= P.MAINSTEM_MAX_KM
                            and t[0].basin_area_km2 > usable[0][0].basin_area_km2]
                if near_big:
                    near_big.sort(key=lambda t: (
                        _is_regulated_structure(t[1].station_name),
                        -t[0].basin_area_km2))
                    picks.append(near_big[0])
                by_id = {m.station_number: (m, ams, prov)
                         for m, ams, prov in picks}
            current_year = 2026
            ordered = sorted(
                by_id.items(),
                key=lambda kv: (_is_regulated_structure(kv[1][1].station_name),
                                max(kv[1][1].years) < current_year - P.STALE_RECORD_YEARS,
                                kv[1][0].basin_area_km2))
            for sid, (m, ams, prov) in ordered:
                stations.append((sid, ams, prov, m.basin_area_km2,
                    f"the property lies INSIDE this gauge's {m.basin_area_km2:,.0f} km² "
                    f"drainage basin (WSC basin polygons) — a hydrological match, "
                    f"not a distance guess"))
        if not stations:
            matched_fallback = True
            near = hydat.stations_near(geo.latitude, geo.longitude,
                                       radius_km=P.FALLBACK_RADIUS_KM, limit=P.MAX_GAUGES + 3)
            near.sort(key=lambda st: (_is_regulated_structure(st.station_name),
                                      st.distance_km))
            for st in near[:P.MAX_GAUGES]:
                ams, prov = hydat.annual_maxima(st.station_number)
                stations.append((st.station_number, ams, prov, st.drainage_area_km2,
                    f"nearest-gauge fallback ({st.distance_km:.0f} km away): no "
                    f"gauged catchment contains this point"))
        if not stations:
            raise ValueError(
                "No gauged catchment contains this location and no usable gauge "
                f"exists within {P.FALLBACK_RADIUS_KM:.0f} km; gauge-based analysis "
                "is not yet possible here.")

        # 3 — terrain: ONE solve, per-gauge-scale HAND queries (v0.4)
        hand_note_grade_cap = "B"
        terrain = None
        try:
            from .terrain.dem import CogDem
            from .terrain.hand import TerrainModel
            dem_src = dem or CogDem()
            arr, cell = dem_src.window(geo.latitude, geo.longitude, half_km=6.0)
            terrain = TerrainModel(arr, cell)
            terrain_method = "HAND on the 30 m national DEM (MRDEM)"
        except Exception as e:
            selection_notes.append(
                f"DEM window unavailable ({type(e).__name__}: {e}); using the "
                "coarser point-elevation relief method — grade capped at C.")
            terrain_method = "point-elevation relief ring (fallback)"
            hand_note_grade_cap = "C"

        exposure_narrative: list[str] = []
        gauge_inputs, gauge_ams, gauge_thr = [], {}, {}
        gauge_hand: dict[str, float] = {}
        fill_m = 0.0
        reachback_floor = None   # None | "Medium" | "High"
        reachback_lines: list[str] = []

        if terrain is None:      # legacy relief fallback (no DEM)
            from .elevation import WebElevation, assess_exposure
            elev = WebElevation()
            q2_tmp = float(np.median(stations[0][1].flows_array()))
            exp = assess_exposure(geo.latitude, geo.longitude, q2_tmp,
                                  stations[0][3], elev)
            exposure_narrative = list(exp.narrative)
            for sid, ams, prov, da, why in stations:
                gauge_hand[sid] = exp.relief_m
        else:
            fill_m = terrain.center_fill_depth_m
            scale_rejected = set()
            for i, (sid, ams, prov, da, why) in enumerate(stations):
                target = float(np.clip((da or 500.0) * P.GAUGE_SCALE_FRACTION,
                                       P.GAUGE_SCALE_MIN_KM2, P.GAUGE_SCALE_MAX_KM2))
                hres = terrain.hand_at_scale(target)
                # Trust guard (v0.20): if the gauge's real catchment dwarfs
                # the biggest channel this window actually contains, the
                # stage transfer is physically meaningless -- REJECT the
                # gauge instead of caveat-and-proceed (the Lac Ste. Anne
                # county-centroid must not inherit Athabasca statistics).
                if (not hres.reached_scale and (da or 0) >
                        P.GAUGE_SCALE_REJECT_RATIO
                        * max(hres.stream_area_km2, terrain.cell_km2)):
                    scale_rejected.add(sid)
                    selection_notes.append(
                        f"[{sid}] REJECTED (scale-compatibility): gauge "
                        f"catchment {da:,.0f} km2 vs largest watercourse "
                        f"resolved in this window "
                        f"{hres.stream_area_km2:,.0f} km2 exceeds "
                        f"{P.GAUGE_SCALE_REJECT_RATIO:.0f}x. Transferring "
                        "its statistics here would be meaningless; this is "
                        "a refusal, not a low-risk finding.")
                gauge_hand[sid] = hres.hand_m
                for line in hres.narrative:
                    if line not in exposure_narrative:
                        exposure_narrative.append(line)
                if not hres.reached_scale:
                    hand_note_grade_cap = _worst_grade(hand_note_grade_cap, "C")

        if scale_rejected:
            stations = [t for t in stations if t[0] not in scale_rejected]
            if not stations:
                raise ValueError(
                    "SCALE-COMPATIBILITY REFUSAL: every candidate gauge's "
                    "catchment dwarfs the watercourses resolvable in this "
                    "terrain window (see selection notes above) -- no "
                    "defensible gauged estimate exists at this location/"
                    "precision. Use the satellite-only mode instead: "
                    "python -m hazardwise.sat_pipeline, or supply exact "
                    "--lat/--lon of the parcel.")

        stale_cap = "A"
        for sid, ams, prov, da, why in stations:
            record_end = max(ams.years)
            if record_end < 2026 - P.STALE_RECORD_YEARS:
                stale_cap = "C"
                selection_notes.append(
                    f"[{sid}] IMPORTANT: this gauge's record ends in {record_end} — "
                    f"floods of the last {2026 - record_end} years (including any "
                    "recent events at this location) are NOT represented. Where "
                    "provincial hydrometric data exists (e.g. Quebec's DEH "
                    "network), it should be consulted; integrating it is on the "
                    "HazardWise roadmap.")
            if _is_regulated_structure(ams.station_name):
                est_note = (f"[{sid}] note: this station measures a regulated "
                            "structure (canal/diversion); it was deprioritized and "
                            "its statistics describe managed flow, not natural flood "
                            "behaviour.")
                selection_notes.append(est_note)
            flows = ams.flows_array()
            q2 = float(np.median(flows))
            d_b = _bankfull_depth(da)
            h = gauge_hand[sid]
            thr = _threshold(q2, h, d_b)
            gauge_inputs.append((ams, thr))
            gauge_ams[sid], gauge_thr[sid] = ams, thr

            # Observed-flood reach-back: the largest flood in the record maps to
            # an estimated stage rise above bankfull. If a flood that ALREADY
            # HAPPENED would have reached this property's level, no fitted tail
            # is allowed to call the risk negligible.
            if _is_regulated_structure(ams.station_name):
                continue_reachback = False
            else:
                continue_reachback = True

            # MEASURED-STAGE reach-back (v0.8): HYDAT stores annual maximum
            # water LEVELS at many gauges. The observed rise of the biggest
            # flood above a typical annual peak level is direct physical
            # evidence — no rating curve, no hydraulic-geometry constants.
            # It takes precedence over the flow-converted check below, which
            # provably under-converts (Grand Forks 2018: downtown flooded at
            # 3.5x Q2 while the 0.4-exponent rating predicted only 2.3 m).
            if continue_reachback and reachback_floor != "High":
                stages = hydat.annual_stage_maxima(sid)
                if len(stages) >= 10:
                    measured_here = True
                    lvls = np.array([v for _, v in stages])
                    rise = float(np.max(lvls) - np.median(lvls))
                    yr = stages[int(np.argmax(lvls))][0]
                    if rise >= h > 0:
                        reachback_floor = "High"
                        reachback_lines.append(
                            f"Reality check (measured): water levels recorded at "
                            f"gauge {sid} show the {yr} flood rose {rise:.1f} m "
                            f"above a typical annual peak level — at or above this "
                            f"property's {h:.1f} m standing. This is a measured "
                            f"water level, not a model output, and it floors the "
                            f"risk class.")
                    elif rise >= P.REACHBACK_MEDIUM_FRACTION * h and \
                            reachback_floor is None:
                        reachback_floor = "Medium"
                        reachback_lines.append(
                            f"Reality check (measured): the {yr} flood rose "
                            f"{rise:.1f} m above a typical annual peak level at "
                            f"gauge {sid} — a substantial fraction of this "
                            f"property's {h:.1f} m margin.")

            measured_here = False
            qmax = float(np.max(flows))
            year_max = int(ams.years[int(np.argmax(flows))])
            h_max = d_b * ((qmax / max(q2, 1e-9)) ** P.STAGE_EXPONENT - 1.0)
            if not continue_reachback or measured_here:
                pass   # measured evidence supersedes the converted estimate
            elif h_max >= h > 0:
                reachback_floor = "High"
                reachback_lines.append(
                    f"Reality check: the largest observed flood on the "
                    f"{ams.station_name} ({year_max}, {qmax:,.0f} m³/s) would have "
                    f"risen an estimated {h_max:.1f} m above the channel — at or "
                    f"above this property's {h:.1f} m. An event that has already "
                    f"happened is treated as a floor on the risk class, whatever "
                    f"the fitted statistics say.")
            elif h > 0 and h_max >= P.REACHBACK_MEDIUM_FRACTION * h and                     reachback_floor is None:
                reachback_floor = "Medium"
                reachback_lines.append(
                    f"Reality check: the {year_max} flood on the {ams.station_name} "
                    f"would have risen an estimated {h_max:.1f} m above the channel "
                    f"— a substantial fraction of this property's {h:.1f} m margin.")

        est = estimate_property_flood_risk(
            gauge_inputs, ca_status=CAPolygonStatus.ABSENT)
        est.explanation_chain.insert(0, geo.method)
        est.explanation_chain.append(f"Model constants: {P.CALIBRATION_SOURCE}.")
        min_hand = min(gauge_hand.values())
        est.explanation_chain.insert(
            1, f"Terrain method: {terrain_method}; the property stands "
               f"{min_hand:.1f} m above the nearest gauge-scale channel.")
        for line in reachback_lines:
            est.explanation_chain.append(line)
        for sid, ams, prov, da, why in stations:
            est.explanation_chain.append(f"[{sid}] selected because {why}.")
            est.explanation_chain.append(f"[{sid}] data provenance: {prov}.")
        for n in selection_notes:
            est.explanation_chain.append(n)
        if matched_fallback:
            est.mandatory_caveats.append(
                "No gauged catchment contains this property; the nearest-gauge "
                "fallback was used and the property may drain to a different "
                "watercourse.")

        # 5 — grade capping (weakest link)
        if getattr(geo, "stage", 0) >= 3:
            est.mandatory_caveats.append(
                "This address resolved only to a locality centre, not a "
                "specific property. Within this community, homes near the "
                "water and homes on higher ground can have OPPOSITE risk — "
                "this report cannot distinguish them. Provide a precise "
                "address or coordinates for a property-level answer.")
        caps = [est.grade, GEOCODE_GRADE_CAP.get(geo.confidence, "C"),
                hand_note_grade_cap, stale_cap]
        if stale_cap != "A":
            est.mandatory_caveats.append(
                "The federal gauge record for this river ends decades ago; "
                "recent flood history is not reflected in these statistics and "
                "the risk shown here may be an underestimate.")
        if matched_fallback:
            caps.append("C")
        est.grade = _worst_grade(*caps)

        risk = classify_risk(est.aep_central, min_hand, fill_m)
        if reachback_floor == "High":
            risk = "High"
        elif reachback_floor == "Medium" and risk == "Low":
            risk = "Medium"

        # Credibility clamps on the DISPLAYED number (class already decided)
        if est.aep_central > P.AEP_DISPLAY_CAP:
            est.mandatory_caveats.append(
                "The statistical estimate here exceeds a 1-in-4-year frequency — "
                "beyond the precision this method can honestly claim. It is "
                "reported as 'more often than about once in 4 years'; treat the "
                "location as very highly exposed.")
            est.aep_central = P.AEP_DISPLAY_CAP
            est.aep_ci_hi = max(est.aep_ci_hi, P.AEP_DISPLAY_CAP)
            est.aep_ci_lo = min(est.aep_ci_lo, P.AEP_DISPLAY_CAP)
        if 0 < est.aep_central < P.AEP_DISPLAY_FLOOR:
            est.aep_central = P.AEP_DISPLAY_FLOOR
            est.mandatory_caveats.append(
                "The statistical estimate is below what any gauge record can "
                "resolve; it is reported as 'less than about 1-in-10,000 per "
                "year' rather than as a precise tiny number.")
        if fill_m > P.DEPRESSION_BUMP_M:
            est.mandatory_caveats.append(
                "This property sits in a closed depression (diked/pumped or "
                "naturally ponding land). Statistical river frequencies "
                "understate the consequence severity here: when defences fail "
                "or inflows exceed drainage, water depth builds rather than "
                "flowing away.")

        # 6 — report
        vintage = hydat_vintage or _read_vintage()
        sources = [
            ("Environment and Climate Change Canada — HYDAT (Water Survey of Canada)",
             "https://collaboration.cmc.ec.gc.ca/cmc/hydrometrics/www/"),
            ("ECCC — National Hydrometric Network Basin Polygons",
             "https://collaboration.cmc.ec.gc.ca/cmc/hydrometrics/www/HydrometricNetworkBasinPolygons/"),
            ("NRCan — MRDEM 30 m Digital Terrain Model (CanElevation)",
             "https://open.canada.ca/data/en/dataset/18752265-bda3-498c-a4ba-9dfe68cb98da"),
            ("OpenStreetMap Nominatim (geocoding)",
             "https://nominatim.openstreetmap.org/"),
        ]
        out_dir = out_root / slugify(address)
        # 6b -- satellite/map section (v0.13). FAIL-OPEN by design.
        map_html, map_meta = "", None
        if terrain is not None and stations:
            try:
                from .satellite import wiring as satw
                sid0, ams0, _, da0, _ = stations[0]
                case = satw.gauge_case(
                    matched_fallback,
                    _is_regulated_structure(ams0.station_name),
                    ams0.n_years)
                target0 = float(np.clip((da0 or 500.0) * P.GAUGE_SCALE_FRACTION,
                                        P.GAUGE_SCALE_MIN_KM2, P.GAUGE_SCALE_MAX_KM2))
                gp_m = 250.0 if any(t in (geo.method or "").lower()
                                    for t in ("centroid", "locality", "town")) else 15.0
                stamp = next((l for l in est.explanation_chain
                              if l.startswith("Model constants:")),
                             "Model constants: DEFAULT")
                map_html, map_meta = satw.build_map_section_html(
                    terrain=terrain, lat=geo.latitude, lon=geo.longitude,
                    geocode_precision_m=gp_m, est=est, risk_class=risk,
                    case=case, ams=ams0, threshold=gauge_thr.get(sid0),
                    target_km2=target0, out_dir=out_dir,
                    constants_stamp=stamp)
            except Exception as e:
                est.mandatory_caveats.append(
                    f"Map section unavailable this run "
                    f"({type(e).__name__}: {e}); the written findings above "
                    "are unaffected.")
        path = render_report(
            address=address,
            geocode_note=f"Resolved to ({geo.latitude:.5f}, {geo.longitude:.5f}) — "
                         f"{geo.method}",
            risk_class=risk, estimate=est,
            exposure_narrative=exposure_narrative,
            gauge_ams=gauge_ams, gauge_thresholds=gauge_thr,
            sources=sources, hydat_vintage=vintage, out_dir=out_dir,
            map_html=map_html, map_meta=map_meta)
        return path, risk, est
    finally:
        if own_db:
            hydat.close()


def _read_vintage() -> str:
    f = DEFAULT_DATA_DIR / "HYDAT_VERSION.txt"
    return f.read_text(encoding="utf-8").strip() if f.exists() else "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description="HazardWise flood risk report")
    ap.add_argument("address", nargs="?")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--out", default="reports")
    args = ap.parse_args()
    if not args.address and not (args.lat and args.lon):
        ap.error("provide an address or --lat/--lon")
    label = args.address or f"site at {args.lat:.4f}, {args.lon:.4f}"
    coords = (args.lat, args.lon) if args.lat and args.lon else None
    path, risk, est = run_report(label, coords=coords, out_root=Path(args.out))
    print(f"Risk: {risk} | Grade: {est.grade} | AEP {est.aep_central:.2%}")
    print(f"Report: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
