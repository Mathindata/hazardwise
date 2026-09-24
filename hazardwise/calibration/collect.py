"""Collect calibration ingredients per labeled point.

The expensive parts (DEM windows, gauge selection, distribution fits) run ONCE
per point; everything a candidate constant set needs to re-classify the point
is stored, so sweeping thousands of constant combinations is instant and never
re-runs a pipeline.

Stored per point: label, event, lat/lon, fill_depth, and per-gauge:
  hand_m, Q2, Qmax(+year), drainage area, regulated flag, measured stage rise
  (if the gauge publishes levels), central GEV params (c, loc, scale) and LP3
  params (skew, log-mean, log-sd).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from ..data.hydat import HydatDB
from ..models.frequency import fit_gev, fit_lp3
from ..pipeline import (GAUGE_SCALE_FRACTION, GAUGE_SCALE_MAX_KM2,
                        GAUGE_SCALE_MIN_KM2, MAINSTEM_MAX_KM,
                        _is_regulated_structure)


@dataclass
class GaugeIngredients:
    station: str
    regulated: bool
    hand_m: float
    fill_depth_m: float
    q2: float
    qmax: float
    year_max: int
    da_km2: float
    gev: tuple[float, float, float]        # c, loc, scale
    lp3: tuple[float, float, float]        # skew, log10-mean, log10-sd
    measured_rise_m: float | None = None
    measured_rise_year: int | None = None


@dataclass
class PointIngredients:
    lat: float
    lon: float
    flooded: bool
    event: str
    gauges: list[GaugeIngredients] = field(default_factory=list)
    error: str | None = None


class _TerrainCache:
    """One TerrainModel per ~2 km grid cell; nearby points share windows."""

    def __init__(self, dem_source):
        self.dem = dem_source
        self.cache: dict[tuple[int, int], object] = {}

    def get(self, lat: float, lon: float):
        from ..terrain.hand import TerrainModel
        key = (int(round(lat / 0.02)), int(round(lon / 0.02)))
        if key not in self.cache:
            clat, clon = key[0] * 0.02, key[1] * 0.02
            arr, cell = self.dem.window(clat, clon, half_km=6.0)
            self.cache[key] = TerrainModel(arr, cell)
        return self.cache[key]


def collect_point(lat, lon, flooded, event, hydat: HydatDB, basins,
                  tcache: _TerrainCache) -> PointIngredients:
    from ..data.hydat import haversine_km
    pt = PointIngredients(lat, lon, flooded, event)
    try:
        matches = basins.containing(lat, lon) if basins is not None else []
        cands = []
        for m in matches:
            try:
                ams, _ = hydat.annual_maxima(m.station_number)
            except ValueError:
                continue
            if ams.n_years >= 15:
                cands.append((m.basin_area_km2, ams, m.station_number))
        picks = []
        if cands:
            cands.sort(key=lambda t: t[0] * (1.02 if
                       _is_regulated_structure(t[1].station_name) else 1.0))
            picks = [cands[0]]
            near_big = []
            for da, ams, sid in cands[1:]:
                info = hydat.station_info(sid)
                if info and haversine_km(lat, lon, *info) <= MAINSTEM_MAX_KM \
                        and da > picks[0][0]:
                    near_big.append((da, ams, sid))
            if near_big:
                near_big.sort(key=lambda t: -t[0])
                picks.append(near_big[0])
        if not picks:
            for st in hydat.stations_near(lat, lon, radius_km=60, limit=2):
                ams, _ = hydat.annual_maxima(st.station_number)
                picks.append((st.drainage_area_km2 or 500.0, ams,
                              st.station_number))
        if not picks:
            pt.error = "no usable gauges"
            return pt

        terrain = tcache.get(lat, lon)
        for da, ams, sid in picks:
            target = float(np.clip((da or 500.0) * GAUGE_SCALE_FRACTION,
                                   GAUGE_SCALE_MIN_KM2, GAUGE_SCALE_MAX_KM2))
            hres = terrain.hand_at_scale(target)
            flows = ams.flows_array()
            q2 = float(np.median(flows))
            gev = fit_gev(flows)
            lp3 = fit_lp3(flows)
            gi = GaugeIngredients(
                station=sid,
                regulated=_is_regulated_structure(ams.station_name),
                hand_m=hres.hand_m, fill_depth_m=hres.fill_depth_m,
                q2=q2, qmax=float(np.max(flows)),
                year_max=int(ams.years[int(np.argmax(flows))]),
                da_km2=float(da or 500.0),
                gev=tuple(map(float, (gev.args[0], gev.kwds.get("loc", 0),
                                      gev.kwds.get("scale", 1)))),
                lp3=tuple(map(float, (lp3.args[0], lp3.kwds.get("loc", 0),
                                      lp3.kwds.get("scale", 1)))),
            )
            stages = hydat.annual_stage_maxima(sid)
            if len(stages) >= 10:
                lv = np.array([v for _, v in stages])
                gi.measured_rise_m = float(np.max(lv) - np.median(lv))
                gi.measured_rise_year = stages[int(np.argmax(lv))][0]
            pt.gauges.append(gi)
    except Exception as e:
        pt.error = f"{type(e).__name__}: {e}"
    return pt


def save_points(points: list[PointIngredients], path: Path) -> None:
    path.write_text(json.dumps([asdict(p) for p in points], indent=1),
                    encoding="utf-8")


def load_points(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
