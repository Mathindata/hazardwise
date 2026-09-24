"""Offline end-to-end pipeline tests.

Builds a synthetic Hydat.sqlite3 with the REAL ECCC schema (STATIONS,
ANNUAL_INSTANT_PEAKS, ANNUAL_STATISTICS) so hydat.py runs the same SQL it will
run against the genuine database on Windows. Geocoding and elevation use fake
sources, so no network is needed.
"""

import json
import sqlite3

import numpy as np
import pytest
from scipy import stats

from hazardwise.data.hydat import HydatDB
from hazardwise.geocoding.geocoder import (FixedGeocoder, _street_town_prov,
                                           _strip_unit_postal, _town_prov)
from hazardwise.pipeline import classify_risk, run_report
from hazardwise.terrain.dem import ArrayDem


class _NoBasins:
    def containing(self, lat, lon):
        return []


def _slope_dem(hand_m, n=100):
    """V-valley whose centre cell sits ~hand_m above the channel."""
    r, c = np.mgrid[0:n, 0:n]
    ch = n // 4
    h = max(hand_m, 0.05)
    wall = h * (1.0 - np.exp(-np.abs(c - ch) * 0.6 / h))
    return 100.0 + 0.001 * r + wall   # gentle valley slope

LAT, LON = 50.582, -113.874  # High River-ish


@pytest.fixture()
def hydat_db(tmp_path):
    db = tmp_path / "Hydat.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript("""
    CREATE TABLE STATIONS (
      STATION_NUMBER TEXT PRIMARY KEY, STATION_NAME TEXT,
      PROV_TERR_STATE_LOC TEXT, LATITUDE REAL, LONGITUDE REAL,
      DRAINAGE_AREA_GROSS REAL, HYD_STATUS TEXT);
    CREATE TABLE ANNUAL_INSTANT_PEAKS (
      STATION_NUMBER TEXT, DATA_TYPE TEXT, YEAR INTEGER, PEAK_CODE TEXT,
      PEAK REAL, SYMBOL TEXT);
    CREATE TABLE ANNUAL_STATISTICS (
      STATION_NUMBER TEXT, DATA_TYPE TEXT, YEAR INTEGER,
      MAX REAL, MAX_SYMBOL TEXT);
    """)
    rng = np.random.default_rng(77)
    # Station A: close (on the river), 45 yrs instantaneous peaks
    conn.execute("INSERT INTO STATIONS VALUES (?,?,?,?,?,?,?)",
                 ("05BL024", "HIGHWOOD RIVER NEAR THE MOUTH", "AB",
                  LAT + 0.01, LON + 0.01, 3950.0, "A"))
    flows = stats.gumbel_r(loc=150, scale=80).rvs(45, random_state=rng)
    for i, (y, q) in enumerate(zip(range(1981, 2026), np.maximum(flows, 5.0))):
        conn.execute("INSERT INTO ANNUAL_INSTANT_PEAKS VALUES (?,?,?,?,?,?)",
                     ("05BL024", "Q", y, "H", float(q), "E" if i < 3 else None))
    # Station B: 30 km away, daily-max fallback only, 25 yrs
    conn.execute("INSERT INTO STATIONS VALUES (?,?,?,?,?,?,?)",
                 ("05BM004", "SHEEP RIVER AT OKOTOKS", "AB",
                  LAT + 0.25, LON - 0.15, 1500.0, "A"))
    flows_b = stats.gumbel_r(loc=80, scale=35).rvs(25, random_state=rng)
    for y, q in zip(range(2001, 2026), np.maximum(flows_b, 2.0)):
        conn.execute("INSERT INTO ANNUAL_STATISTICS VALUES (?,?,?,?,?)",
                     ("05BM004", "Q", y, float(q), None))
    # Station C: too far (should be excluded)
    conn.execute("INSERT INTO STATIONS VALUES (?,?,?,?,?,?,?)",
                 ("05AA008", "FARAWAY RIVER", "AB", LAT + 2.5, LON, 900.0, "A"))
    conn.commit(); conn.close()
    return HydatDB(db)


def test_station_search_and_ams(hydat_db):
    st = hydat_db.stations_near(LAT, LON, radius_km=60)
    ids = [s.station_number for s in st]
    assert ids[0] == "05BL024" and "05AA008" not in ids
    ams, prov = hydat_db.annual_maxima("05BL024")
    assert ams.n_years == 45 and ams.n_estimated == 3
    assert "instantaneous" in prov
    ams_b, prov_b = hydat_db.annual_maxima("05BM004")
    assert ams_b.n_years == 25 and "DAILY" in prov_b


def test_geocode_cascade_string_surgery():
    a = "Unit 4, 309B Macleod Trail SW, High River, AB T1V 1Z5"
    assert "T1V" not in _strip_unit_postal(a) and "Unit" not in _strip_unit_postal(a)
    assert _street_town_prov(a) == "Macleod Trail SW, High River, AB"
    assert _town_prov(a) == "High River, AB"


def test_classify_risk_banding_and_terrain_bump():
    assert classify_risk(0.05, hand_m=10) == "High"
    assert classify_risk(0.01, hand_m=10) == "Medium"
    assert classify_risk(0.001, hand_m=10) == "Low"
    assert classify_risk(0.001, hand_m=0.5) == "Medium"   # terrain bump
    assert classify_risk(0.01, hand_m=0.5) == "High"


@pytest.mark.parametrize("relief,expected_risk", [
    (0.5, "High"),    # valley floor property
    (30.0, "Low"),    # bench property far above the river
])
def test_pipeline_end_to_end_offline(hydat_db, tmp_path, relief, expected_risk):
    addr = "309B Macleod Trail SW, High River, AB"
    geo = FixedGeocoder({addr: (LAT, LON, "A")})
    path, risk, est = run_report(
        addr, geocoder=geo, dem=ArrayDem(_slope_dem(relief)), basins=_NoBasins(),
        hydat=hydat_db,
        out_root=tmp_path / "reports", hydat_vintage="Hydat_sqlite3_test.zip")

    assert risk == expected_risk
    assert path.exists() and path.name == "report.html"
    html = path.read_text(encoding="utf-8")
    assert "Flood risk: " in html and "evidence chain" in html
    assert "data:image/png;base64," in html                    # plots embedded
    assert "HYDAT" in html                                      # sources named
    assert "100-year flood" not in html.lower()                 # language rule
    # weakest-link grade: screening cap means never A
    assert est.grade in ("B", "C", "D")

    j = json.loads((path.parent / "report.json").read_text(encoding="utf-8"))
    assert j["risk_class"] == expected_risk
    assert j["estimate"]["gauges_used"] == ["05BL024", "05BM004"]
    assert (path.parent / "05BL024_ams.png").exists()
    assert (path.parent / "05BL024_freq.png").exists()


def test_geocode_confidence_caps_grade(hydat_db, tmp_path):
    addr = "Somewhere vague, High River, AB"
    geo = FixedGeocoder({addr: (LAT, LON, "C")})   # town-centroid geocode
    _, _, est = run_report(addr, geocoder=geo, dem=ArrayDem(_slope_dem(30.0)),
                           basins=_NoBasins(), hydat=hydat_db,
                           out_root=tmp_path / "r", hydat_vintage="test")
    assert est.grade in ("C", "D")
