"""v0.3 tests: M1 (basins), M2 (HAND), and the beyond-record fix — including
regression scenarios reconstructing BOTH v0.2 validation misses.
"""

import sqlite3

import numpy as np
import pytest
from scipy import stats

from hazardwise.data.hydat import HydatDB
from hazardwise.geocoding.geocoder import FixedGeocoder
from hazardwise.models.ams import AnnualMaximumSeries
from hazardwise.models.estimate import estimate_property_flood_risk
from hazardwise.pipeline import classify_risk, run_report
from hazardwise.terrain.dem import ArrayDem
from hazardwise.terrain.hand import compute_hand, fill_depressions

LAT, LON = 43.856, -79.336


# ------------------------------------------------------------- HAND terrain

def valley_dem(n=120, bench_h=9.0, cell=30.0):
    """V-valley: channel down column n//4, valley walls rising 0.6 m per cell
    up to a bench `bench_h` m above the channel. Centre cell sits on the bench;
    cells beside the channel sit on the valley floor."""
    r, c = np.mgrid[0:n, 0:n]
    ch = n // 4
    wall = bench_h * (1.0 - np.exp(-np.abs(c - ch) * 0.6 / bench_h))
    return 100.0 + 0.001 * cell * r + wall


def test_hand_recovers_bench_height():
    dem = valley_dem(bench_h=9.0)
    res = compute_hand(dem, cell_size_m=30.0, stream_threshold_km2=0.5)
    assert 5.5 <= res.hand_m <= 10.0           # ~7.8 m bench recovered
    assert res.fill_depth_m < 0.1              # no depression on a slope
    assert res.stream_distance_m > 0


def test_hand_valley_floor_is_low():
    dem = valley_dem(bench_h=9.0)
    ch = dem.shape[1] // 4
    res = compute_hand(dem, 30.0, stream_threshold_km2=0.5,
                       center=(dem.shape[0] // 2, ch + 1))
    assert res.hand_m < 3.5                     # beside the channel


def test_depression_detection_sumas_signature():
    """A closed bowl (former lake bed) must show metres of fill depth even when
    local relief looks mild — the exact signature the v0.2 ring missed."""
    n = 100
    r, c = np.mgrid[0:n, 0:n]
    dist = np.hypot(r - n / 2, c - n / 2)
    dem = 10.0 + 0.002 * dist ** 2              # bowl, rim ~ +5 m
    res = compute_hand(dem, 30.0, stream_threshold_km2=0.5)
    assert res.fill_depth_m > 2.0               # deep ponding at the centre
    filled = fill_depressions(dem)
    assert np.all(filled >= dem - 1e-9)


def test_classify_risk_depression_bump():
    assert classify_risk(0.001, hand_m=5.0, fill_depth_m=0.0) == "Low"
    assert classify_risk(0.001, hand_m=5.0, fill_depth_m=3.0) == "Medium"
    assert classify_risk(0.01, hand_m=5.0, fill_depth_m=3.0) == "High"
    assert classify_risk(0.001, hand_m=0.8, fill_depth_m=0.0) == "Medium"


# -------------------------------------------------- beyond-record (Markham)

def _ams(n, seed, loc=30.0, scale=10.0, sid="02HC053", name="LITTLE ROUGE CREEK"):
    rng = np.random.default_rng(seed)
    flows = np.maximum(stats.gumbel_r(loc=loc, scale=scale).rvs(n, random_state=rng), 1.0)
    return AnnualMaximumSeries(sid, name, tuple(range(2026 - n, 2026)), tuple(flows))


def test_beyond_record_gauge_cannot_poison_headline():
    """v0.2 bug reconstruction: long-record gauge says AEP ~ 0 at a huge
    threshold; short-record gauge with an unreachable threshold used to inject
    1/(n+1) = 5.3% into the headline. v0.3: excluded, reported qualitatively."""
    long_g = _ams(54, 1, 40, 12, sid="02HC022", name="ROUGE RIVER")
    short_g = _ams(18, 2, 25, 8)                # 18 yrs -> Weibull path
    huge_long = float(np.max(long_g.flows_m3s)) * 60     # ~AEP floor
    huge_short = float(np.max(short_g.flows_m3s)) * 60   # never observed
    est = estimate_property_flood_risk([(long_g, huge_long), (short_g, huge_short)])
    assert est.aep_central < 0.005, "beyond-record bound leaked into headline"
    assert est.per_gauge["02HC053"].beyond_record
    assert any("never been observed" in s for s in est.explanation_chain)
    assert any("Excluded from the headline" in s or "upper bound" in s
               for s in est.explanation_chain)


def test_all_gauges_beyond_record_headline_is_bound():
    a = _ams(18, 3)
    est = estimate_property_flood_risk([(a, float(np.max(a.flows_m3s)) * 50)])
    assert est.grade == "D"
    assert est.aep_ci_lo == 0.0 and est.aep_central <= 1 / 19 + 1e-9


# --------------------------------------------------------------- M1 basins

class FakeBasins:
    """Mimics spatial.basins.BasinIndex.containing without geopandas data."""

    def __init__(self, matches):
        self._m = matches

    def containing(self, lat, lon):
        from hazardwise.spatial.basins import BasinMatch
        return [BasinMatch(s, a, "containment") for s, a in self._m]


@pytest.fixture()
def hydat_db(tmp_path):
    db = tmp_path / "Hydat.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript("""
    CREATE TABLE STATIONS (STATION_NUMBER TEXT PRIMARY KEY, STATION_NAME TEXT,
     PROV_TERR_STATE_LOC TEXT, LATITUDE REAL, LONGITUDE REAL,
     DRAINAGE_AREA_GROSS REAL, HYD_STATUS TEXT);
    CREATE TABLE ANNUAL_INSTANT_PEAKS (STATION_NUMBER TEXT, DATA_TYPE TEXT,
     YEAR INTEGER, PEAK_CODE TEXT, PEAK REAL, SYMBOL TEXT);
    CREATE TABLE ANNUAL_STATISTICS (STATION_NUMBER TEXT, DATA_TYPE TEXT,
     YEAR INTEGER, MAX REAL, MAX_SYMBOL TEXT);""")
    rng = np.random.default_rng(9)
    # local creek gauge (small basin) + big river gauge + short-record creek
    for sid, name, da, loc, scale, n in [
        ("02HC022", "ROUGE RIVER NEAR MARKHAM", 186, 40, 12, 54),
        ("08MH029", "SUMAS RIVER NEAR HUNTINGDON", 149, 30, 15, 62),
        ("02HC053", "LITTLE ROUGE CREEK", 78, 25, 8, 18),
    ]:
        conn.execute("INSERT INTO STATIONS VALUES (?,?,?,?,?,?,?)",
                     (sid, name, "XX", LAT + 0.02, LON + 0.02, da, "A"))
        flows = np.maximum(stats.gumbel_r(loc=loc, scale=scale)
                           .rvs(n, random_state=rng), 1.0)
        for y, q in zip(range(2026 - n, 2026), flows):
            conn.execute("INSERT INTO ANNUAL_INSTANT_PEAKS VALUES (?,?,?,?,?,?)",
                         (sid, "Q", y, "H", float(q), None))
    conn.commit(); conn.close()
    return HydatDB(db)


def test_basin_selection_smallest_first_and_report_says_inside(hydat_db, tmp_path):
    """Markham scenario end-to-end in v0.3: elevated bench + basin gauges +
    short-record creek beyond record  ->  Low, and the chain explains why."""
    addr = "101 Town Centre Boulevard, Markham, ON"
    geo = FixedGeocoder({addr: (LAT, LON, "A")})
    basins = FakeBasins([("02HC053", 78.0), ("02HC022", 186.0)])
    dem = ArrayDem(valley_dem(bench_h=9.0))
    path, risk, est = run_report(addr, geocoder=geo, hydat=hydat_db, dem=dem,
                                 basins=basins, out_root=tmp_path / "r",
                                 hydat_vintage="test")
    assert risk == "Low"                          # was the v0.2 TWO-STEP MISS
    assert est.gauges_used[0] == "02HC053"        # smallest basin first
    html = path.read_text(encoding="utf-8")
    assert "INSIDE this gauge's" in html
    assert "HAND" in html


def test_sumas_depression_scenario_bumps_high(hydat_db, tmp_path):
    """Sumas scenario: flat bowl terrain + Sumas River basin gauge -> the
    depression signature must force High even with modest channel AEP."""
    addr = "Tolmie Road, Abbotsford, BC"
    geo = FixedGeocoder({addr: (LAT, LON, "A")})
    basins = FakeBasins([("08MH029", 149.0)])
    n = 120
    r, c = np.mgrid[0:n, 0:n]
    bowl = 8.0 + 0.0015 * np.hypot(r - n / 2, c - n / 2) ** 2   # lakebed bowl
    path, risk, est = run_report(addr, geocoder=geo, hydat=hydat_db,
                                 dem=ArrayDem(bowl), basins=basins,
                                 out_root=tmp_path / "r2", hydat_vintage="test")
    assert risk == "High"                         # was the v0.2 false Low
    assert any("closed depression" in cvt for cvt in est.mandatory_caveats)


def test_fallback_when_no_basin_caps_grade(hydat_db, tmp_path):
    addr = "Nowhere Lane, Somewhere, XX"
    geo = FixedGeocoder({addr: (LAT, LON, "A")})
    path, risk, est = run_report(addr, geocoder=geo, hydat=hydat_db,
                                 dem=ArrayDem(valley_dem()),
                                 basins=FakeBasins([]),
                                 out_root=tmp_path / "r3", hydat_vintage="test")
    assert est.grade in ("C", "D")
    assert any("nearest-gauge fallback" in s for s in est.explanation_chain)
