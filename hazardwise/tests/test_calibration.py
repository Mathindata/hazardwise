"""M5 harness tests: sampler geometry, sweep math, and that calibration can
actually recover a known-good exponent from synthetic ground truth."""
import numpy as np
import geopandas as gpd
from shapely.geometry import Polygon

from hazardwise.calibration.extents import sample_points
from hazardwise.calibration.sweep import classify_point, score, sweep


def _extents():
    sq = Polygon([(-114.2, 50.5), (-114.0, 50.5), (-114.0, 50.7), (-114.2, 50.7)])
    return gpd.GeoDataFrame({"event": ["AB 2013"], "geometry": [sq]},
                            crs="EPSG:4326")


def test_sampler_labels_and_geometry():
    ext = _extents()
    pts = sample_points(ext, n_wet_per_event=25, n_dry_per_event=25, seed=1)
    wet = [p for p in pts if p.flooded]
    dry = [p for p in pts if not p.flooded]
    assert len(wet) == 25 and len(dry) == 25
    poly = ext.geometry.iloc[0]
    from shapely.geometry import Point
    assert all(poly.contains(Point(p.lon, p.lat)) for p in wet)
    assert all(not poly.contains(Point(p.lon, p.lat)) for p in dry)


def _pt(flooded, hand, q2=200.0, qmax=700.0, da=5000.0, rise=None, event="E1"):
    from scipy import stats
    flows = np.maximum(stats.gumbel_r(loc=q2, scale=q2 * 0.3)
                       .rvs(40, random_state=np.random.default_rng(int(hand * 10))), 1)
    from hazardwise.models.frequency import fit_gev, fit_lp3
    gev, lp3 = fit_gev(flows), fit_lp3(flows)
    g = dict(station="X", regulated=False, hand_m=hand, fill_depth_m=0.0,
             q2=float(np.median(flows)), qmax=qmax, year_max=2018, da_km2=da,
             gev=(gev.args[0], gev.kwds["loc"], gev.kwds["scale"]),
             lp3=(lp3.args[0], lp3.kwds["loc"], lp3.kwds["scale"]),
             measured_rise_m=rise, measured_rise_year=2018 if rise else None)
    return dict(lat=0, lon=0, flooded=flooded, event=event, gauges=[g],
                error=None)


def test_measured_rise_dominates_classification():
    p = _pt(True, hand=4.0, rise=4.5)
    assert classify_point(p, b=0.40, k_bankfull=0.27, hand_bump_m=1.5,
                          high_aep=0.02, rb_frac=1.0) == "High"
    p2 = _pt(False, hand=12.0, rise=4.5)
    assert classify_point(p2, b=0.40, k_bankfull=0.27, hand_bump_m=1.5,
                          high_aep=0.02, rb_frac=1.0) != "High"


def test_sweep_recovers_separating_exponent():
    """Ground truth built so wet points sit ~3 m above channels that rose
    ~3.5x Q2 and dry points sit ~10 m up: a higher exponent separates them,
    the sweep must find one with skill on a held-out event."""
    pts = []
    for ev in ("E1", "E2", "E3"):
        for i in range(12):
            pts.append(_pt(True, hand=2.5 + 0.1 * i, event=ev))
            pts.append(_pt(False, hand=9.0 + 0.3 * i, event=ev))
    small = {"b": [0.35, 0.5], "k_bankfull": [0.27], "hand_bump_m": [1.5],
             "high_aep": [0.02], "rb_frac": [0.8, 1.0]}
    best, train_row, test_row, rows = sweep(pts, grid=small, test_events={"E3"})
    assert test_row["csi"] > 0.6
    assert test_row["pod"] > 0.6 and test_row["far"] < 0.4
    assert len(rows) >= 4


def test_score_counts():
    pts = [_pt(True, 2.0, rise=5.0), _pt(False, 15.0)]
    r = score(pts, b=0.4, k_bankfull=0.27, hand_bump_m=1.5,
              high_aep=0.02, rb_frac=1.0)
    assert r["n_wet"] == 1 and r["n_dry"] == 1
    assert r["pod"] == 1.0 and r["far"] == 0.0 and r["csi"] == 1.0
