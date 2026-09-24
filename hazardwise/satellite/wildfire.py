"""Wildfire risk from satellite/archive data only (v0.16) — the flood
architecture with new nouns (handoff S 'Wildfire extension'):
  JRC occurrence      -> NBAC annual burn perimeters (1972-, NRCan/CWFIS)
  HAND transfer       -> fuel continuity + WUI distance + slope exposure
  permanent water     -> non-fuel mask (water/urban core/rock)
  EGS event fusion    -> per-pixel burned-year counts, same Beta-Binomial
  reach-back          -> 'the YYYY fire burned to within X m of this address'
All functions take arrays so everything is offline-testable; live NBAC/fuel
loaders are the documented next milestone (M-FIRE-1)."""
from __future__ import annotations
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm
from matplotlib.patches import Patch
from matplotlib.lines import Line2D
from scipy import ndimage
from . import params_sat as P
from .frequency import wilson_interval
from .surface import fuse

NBAC_URL = ("https://cwfis.cfs.nrcan.gc.ca/datamart "
            "(NBAC: National Burned Area Composite, annual GDB/SHP)")

def slope_deg(dem: np.ndarray, cell_m: float) -> np.ndarray:
    gy, gx = np.gradient(dem, cell_m)
    return np.degrees(np.arctan(np.hypot(gx, gy)))

def wui_distance_m(fuel: np.ndarray, res_m: float) -> np.ndarray:
    """Distance to nearest burnable fuel; 0 inside fuel."""
    if not fuel.any():
        return np.full(fuel.shape, np.inf)
    return ndimage.distance_transform_edt(~fuel) * res_m

def burn_counts(nbac_layers, shape) -> tuple[np.ndarray, int]:
    """k(x) = number of RECORD YEARS pixel sat inside a burn perimeter."""
    k = np.zeros(shape, np.int32); years = set()
    for mask, year in nbac_layers:
        k += mask.astype(np.int32); years.add(int(year))
    return k, len(years)

def regional_rate(nbac_layers, fuel, n_record_years: int):
    """r = mean annual fraction of BURNABLE window area burned; CI from the
    year-count (a thin record must say so, loudly)."""
    a_fuel = max(int(fuel.sum()), 1)
    burned_frac_years = [float((m & fuel).sum()) / a_fuel for m, _ in nbac_layers]
    r = float(np.sum(burned_frac_years)) / max(n_record_years, 1)
    n_eff = max(n_record_years, 1)
    lo, hi = wilson_interval(r * n_eff, n_eff)
    return r, float(lo), float(hi)

def regional_rate_from_history(regional_events, record_years,
                               region_km=P.FIRE_REGIONAL_RATE_KM):
    """B2 + fix-1: annual burn frequency implied by the wide-window history
    (distinct burn-years / record). A floor on the window rate so a populated
    25 km history can never coexist with a 0.00%% base rate. Returns 0.0 when
    there is no regional history."""
    if not regional_events or record_years <= 0:
        return 0.0
    burn_years = len({e.year for e in regional_events})
    # fraction of the region that burns per year, area-normalised by a nominal
    # 200 ha NBAC-detectable event over the pi*region_km^2 area
    region_area_km2 = 3.14159 * region_km ** 2
    per_event_frac = 2.0 / region_area_km2                 # 200 ha = 2 km2
    return (burn_years / record_years) * per_event_frac

def fire_probability(*, dem, cell_m, fuel, nbac_layers, n_record_years,
                     res_m=P.SAT_GRID_RES_M, regional_events=None,
                     region_km=P.FIRE_REGIONAL_RATE_KM):
    """Per-pixel annual burn probability + CI rasters + the pieces of the
    justification chain. The base rate is now the MAX of the local-window rate
    and the regional-history rate (B2) so the map cannot contradict the
    history block. Returns None-mode dict only when there is no record."""
    sl = slope_deg(dem, cell_m)
    d = wui_distance_m(fuel, res_m)
    expo = np.exp(-np.minimum(d, 1e7) / P.FIRE_WUI_DECAY_M)
    s = 1.0 + np.clip(sl, 0, 30) / 30.0 * P.FIRE_SLOPE_MAX_BOOST
    k, n_burn_years = burn_counts(nbac_layers, dem.shape)
    if n_record_years <= 0:   # no RECORD -> screening; an empty
        # window WITH a record is evidence of absence, graded Low
        return {"mode": "screening", "expo": expo, "slope": sl, "d_wui": d}
    r_local, r_lo, r_hi = regional_rate(nbac_layers, fuel, n_record_years)
    r_hist = (regional_rate_from_history(regional_events, n_record_years,
              region_km) if P.FIRE_RATE_FLOOR_FROM_HISTORY else 0.0)
    r = max(r_local, r_hist)
    rate_floored = r_hist > r_local
    prior = np.clip(1.0 - np.exp(-r * expo * s * P.FIRE_PRIOR_C), 0.0, 0.25)
    lam = max(n_burn_years / n_record_years, 1e-3)   # fire-years per year
    n_px_evidence = float(n_record_years) * P.FIRE_PIXEL_EVIDENCE_FRACTION
    fus = fuse(prior, k, n_px_evidence, lam,
               gauge_case="SATELLITE_ONLY")
    return {"mode": "probability", "prob": fus.aep, "lo": fus.aep_lo,
            "hi": fus.aep_hi, "prior": prior, "k": k, "r": (r, r_lo, r_hi),
            "lam": lam, "expo": expo, "slope": sl, "d_wui": d,
            "n_burn_years": n_burn_years, "r_local": r_local,
            "r_hist": r_hist, "rate_floored": rate_floored,
            "region_km": region_km}

def classify_fire(p: float, d_wui_m: float, nearest_burn_m: float) -> str:
    cls = ("High" if p >= P.FIRE_HIGH_AEP else
           "Medium" if p >= P.FIRE_MED_AEP else "Low")
    if cls != "High" and nearest_burn_m <= P.FIRE_NEAR_BURN_BUMP_M:
        cls = {"Low": "Medium", "Medium": "High"}[cls]
    return cls

def nearest_burn(nbac_layers, rc, res_m):
    best = (float("inf"), None)
    for mask, year in nbac_layers:
        if mask.any():
            dmap = ndimage.distance_transform_edt(~mask) * res_m
            if dmap[rc] < best[0]:
                best = (float(dmap[rc]), int(year))
    return best

def _bin_idx(p):
    return np.digitize(np.asarray(p, float),
                       np.array(P.FIRE_AEP_BINS[::-1])) - 1

def render_fire_map(aoi, result, fuel, nbac_layers, out_png, stamp,
                    date_range="", grade=None, graded_p=None, bumped=False,
                    basemap_img=None, basemap_alpha=0.5, layer_alpha=0.6,
                    colors=None, hatches=None):
    n = aoi.n
    fig, ax = plt.subplots(figsize=(7.2, 7.8), dpi=150)
    if basemap_img is not None:
        ax.imshow(basemap_img, alpha=basemap_alpha)
    else:
        ax.imshow(np.clip(result["slope"], 0, 35), cmap="gray_r", alpha=0.30)
    pal = tuple(colors) if colors else P.FIRE_AEP_COLORS
    hs = tuple(hatches) if hatches is not None else P.SAT_BIN_HATCHES_ASC
    legend = []
    if result["mode"] == "probability":
        bins_asc = list(P.FIRE_AEP_BINS[::-1]) + [1.0]
        cmap = ListedColormap(list(pal[::-1]))
        norm = BoundaryNorm(bins_asc, cmap.N)
        shown = np.ma.masked_where((result["prob"] < P.FIRE_AEP_BINS[-1])
                                   | ~fuel, result["prob"])
        ax.imshow(shown, cmap=cmap, norm=norm, alpha=layer_alpha)
        idx = _bin_idx(result["prob"])
        for i, h in enumerate(hs):
            if not h:
                continue
            m = fuel & (idx == i)
            if m.any():
                cs = ax.contourf(m.astype(float), levels=[0.5, 1.5],
                                 colors="none", hatches=[h])
                for c in getattr(cs, "collections", [cs]):
                    c.set_edgecolor(P.FIRE_PERIM_COLOR); c.set_linewidth(0)
        rp = ["1:200-1:500", "1:100-1:200", "1:50-1:100", "1:20-1:50",
              "<=1:20"]
        for c, h, lab in zip(pal[::-1],
                             hs, rp):
            legend.append(Patch(fc=c, alpha=layer_alpha, hatch=h,
                                ec=P.FIRE_PERIM_COLOR,
                                label=f"annual burn prob {lab}"))
        span = _bin_idx(result["hi"]) - _bin_idx(result["lo"])
        unc = (span >= P.SAT_CI_HATCH_BINS) & fuel
        if unc.any():
            cs = ax.contourf(unc.astype(float), levels=[0.5, 1.5],
                             colors="none", hatches=[P.SAT_UNC_HATCH])
            for c in getattr(cs, "collections", [cs]):
                c.set_edgecolor("gray"); c.set_linewidth(0)
            legend.append(Patch(fc="none", hatch=P.SAT_UNC_HATCH, ec="gray",
                                label="probability uncertain (wide CI)"))
        title = "Wildfire probability (annual burn)"
    else:
        shown = np.ma.masked_where(~fuel, result["expo"])
        ax.imshow(shown, cmap="Oranges", alpha=0.5)
        legend.append(Patch(fc="#fdae6b", alpha=layer_alpha,
                            label="fuel exposure (relative)"))
        title = "Wildfire SUSCEPTIBILITY (no burn-history record)"
    if (~fuel).any():
        nf = np.ma.masked_where(fuel, np.ones_like(result["slope"]))
        ax.imshow(nf, cmap=ListedColormap([P.FIRE_NONFUEL_COLOR]), alpha=0.55)
    legend.append(Patch(fc=P.FIRE_NONFUEL_COLOR,
                        label="non-fuel (water/urban core/rock)"))
    for mask, year in nbac_layers:
        if mask.any():
            ax.contour(mask.astype(float), levels=[0.5],
                       colors=[P.FIRE_PERIM_COLOR], linestyles="dashed",
                       linewidths=1.1)
    if nbac_layers:
        legend.append(Line2D([0], [0], color=P.FIRE_PERIM_COLOR, ls="--",
                             label=f"historical burns "
                                   f"({len(nbac_layers)} NBAC perimeters)"))
    r0, c0 = aoi.address_rc
    ax.plot(c0, r0, marker="v", ms=11, mec="black", mfc="black", alpha=layer_alpha)
    px500 = 500.0 / aoi.res_m
    ax.plot([n*0.05, n*0.05+px500], [n*0.95]*2, "k-", lw=3)
    ax.text(n*0.05, n*0.93, "500 m", fontsize=8)
    prov = (f"NBAC {date_range} | MRDEM slope | grid {aoi.res_m:.0f} m | "
            f"{stamp}")
    ax.set_title(title, fontsize=11)
    fig.text(0.02, 0.015, prov, fontsize=6.5)
    ax.legend(handles=legend, loc="upper left", fontsize=7, framealpha=0.9)
    ax.set_xticks([]); ax.set_yticks([])
    fig.tight_layout(); fig.savefig(out_png); plt.close(fig)
    meta = {"mode": result["mode"] if result["mode"] == "probability"
            else "susceptibility",
            "legend_blocks": {"bins": result["mode"] == "probability",
                              "fuel": True, "perimeters": bool(nbac_layers),
                              "provenance": True},
            "address_bin": (int(_bin_idx(graded_p)) if graded_p is not None
                            else int(_bin_idx(result["prob"])[r0, c0]))
            if result["mode"] == "probability" else None,
            "provenance": prov}
    meta["integrity"] = fire_integrity(grade, meta, bumped=bumped)
    return meta

FIRE_GRADE_TO_BINS = {"High": {3, 4}, "Medium": {1, 2, 3},
                      "Low": {-1, 0, 1}}

def fire_integrity(grade, meta, bumped=False) -> dict:
    """Grade-vs-map consistency. A near-burn bump legitimately raises the
    class one step above the probability bin, so the audit compares against
    the pre-bump class in that case -- the bump is disclosed in the chain,
    not smuggled past the check."""
    mism = 0
    if meta["mode"] == "probability" and grade in FIRE_GRADE_TO_BINS:
        eff = grade
        if bumped:
            eff = {"High": "Medium", "Medium": "Low", "Low": "Low"}[grade]
        allowed = FIRE_GRADE_TO_BINS[eff] | FIRE_GRADE_TO_BINS[grade]
        mism = int(meta["address_bin"] not in allowed)
    missing = sum(0 if v else 1 for k, v in meta["legend_blocks"].items()
                  if k in ("fuel", "provenance")
                  or (k == "bins" and meta["mode"] == "probability"))
    return {"map_grade_mismatch": mism, "map_legend_missing": missing,
            "map_bad_susceptibility_legend":
                int(meta["mode"] == "susceptibility"
                    and meta["legend_blocks"]["bins"])}
