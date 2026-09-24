"""v0.4 tests — each reconstructs a v0.3 field failure.

1. Flat/lake fragmentation: rivers must connect THROUGH a lake after
   epsilon-filling (v0.3: 'upstream area ~0 km²' everywhere).
2. Scale-matched HAND: property beside a small swale but high above the river
   must report small HAND at swale scale and large HAND at river scale
   (Mount Royal / Markham false-Highs).
3. Observed-flood reach-back: a record containing a flood that reached the
   property's level forces High (Merritt / Minden false-Lows).
"""

import sqlite3

import numpy as np
import pytest
from scipy import stats

from hazardwise.data.hydat import HydatDB
from hazardwise.geocoding.geocoder import FixedGeocoder
from hazardwise.pipeline import run_report
from hazardwise.terrain.dem import ArrayDem
from hazardwise.terrain.hand import TerrainModel

LAT, LON = 51.0, -114.1


def river_with_lake_dem(n=140, cell=30.0):
    """Sloping valley with a channel down column n//3 interrupted by a lake
    (flat basin) in the middle rows. Epsilon-filling must route through it."""
    r, c = np.mgrid[0:n, 0:n]
    ch = n // 3
    wall = 12.0 * (1.0 - np.exp(-np.abs(c - ch) * 0.5 / 12.0))
    dem = 100.0 + 0.02 * r + wall
    lake = (slice(n // 2 - 12, n // 2 + 12), slice(ch - 8, ch + 9))
    dem[lake] = np.minimum(dem[lake], 100.0 + 0.02 * (n // 2 - 12) - 2.0)
    return dem


def swale_and_river_dem(n=140, bench=25.0, swale_drop=0.6):
    """Property on a high bench with a tiny swale 2 cells away; the real river
    runs far below at the window edge. The v0.3 bug measured HAND to the swale."""
    r, c = np.mgrid[0:n, 0:n]
    ch = 10                                     # river column, far from centre
    wall = bench * (1.0 - np.exp(-np.abs(c - ch) * 0.35 / bench))
    dem = 200.0 + 0.005 * r + wall
    mid = n // 2
    # SHORT local swale (10 rows) so it stays a swale — a full-length trench
    # would capture the whole hillside and become a river in its own right
    dem[mid - 5:mid + 5, mid + 2] -= swale_drop
    return dem


def test_rivers_connect_through_lakes():
    tm = TerrainModel(river_with_lake_dem(), 30.0)
    # main channel accumulation below the lake must keep growing (no fragmenting)
    assert tm.max_stream_area_km2 > 3.0
    res = tm.hand_at_scale(1.0)
    assert res.reached_scale
    assert res.stream_area_km2 >= 1.0            # not a 2-cell ditch


def test_hand_is_scale_dependent():
    tm = TerrainModel(swale_and_river_dem(), 30.0)
    small = tm.hand_at_scale(0.005)              # the swale scale
    large = tm.hand_at_scale(3.0)                # the river scale
    assert small.hand_m < 2.0                    # v0.3 measured this...
    assert large.hand_m > 12.0                   # ...but the river is far below
    assert large.hand_m > small.hand_m + 8.0


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
    rng = np.random.default_rng(4)

    def add(sid, name, da, loc, scale, n, outlier=None):
        conn.execute("INSERT INTO STATIONS VALUES (?,?,?,?,?,?,?)",
                     (sid, name, da, LAT + 0.02, LON + 0.02, da, "A"))
        flows = np.maximum(stats.gumbel_r(loc=loc, scale=scale)
                           .rvs(n, random_state=rng), 1.0)
        if outlier:
            flows[-5] = outlier                  # e.g. the 2021 flood
        for y, q in zip(range(2026 - n, 2026), flows):
            conn.execute("INSERT INTO ANNUAL_INSTANT_PEAKS VALUES (?,?,?,?,?,?)",
                         (sid, "Q", y, "H", float(q), None))

    add("05BJ001", "ELBOW RIVER AT CALGARY", 1200, 100, 40, 50)
    add("08LG010", "COLDWATER RIVER AT MERRITT", 914, 60, 20, 55,
        outlier=1300.0)                          # flood far beyond the rest
    conn.commit(); conn.close()
    return HydatDB(db)


class FakeBasins:
    def __init__(self, matches): self._m = matches
    def containing(self, lat, lon):
        from hazardwise.spatial.basins import BasinMatch
        return [BasinMatch(s, a, "containment") for s, a in self._m]


def test_mount_royal_regression_bench_property_is_low(hydat_db, tmp_path):
    """v0.3 false-High: bench property, swale nearby, big-river gauge.
    Scale-matched HAND must yield Low, not 77% AEP."""
    addr = "4825 Mount Royal Gate SW, Calgary, AB"
    geo = FixedGeocoder({addr: (LAT, LON, "A")})
    path, risk, est = run_report(
        addr, geocoder=geo, hydat=hydat_db,
        dem=ArrayDem(swale_and_river_dem()),
        basins=FakeBasins([("05BJ001", 1200.0)]),
        out_root=tmp_path / "r", hydat_vintage="test")
    assert risk == "Low"
    assert est.aep_central <= 0.005


def test_merritt_regression_reachback_forces_high(hydat_db, tmp_path):
    """v0.3 false-Low: property ~4 m above the river; the record CONTAINS a
    flood whose estimated stage exceeds that. Reach-back must force High even
    though the fitted tail calls the threshold rare."""
    addr = "2185 Voght Street, Merritt, BC"
    geo = FixedGeocoder({addr: (LAT, LON, "A")})
    n = 140
    r, c = np.mgrid[0:n, 0:n]
    ch = 20
    wall = 4.0 * (1.0 - np.exp(-np.abs(c - ch) * 0.5 / 4.0))   # ~4 m floodplain
    dem = 600.0 + 0.01 * r + wall
    path, risk, est = run_report(
        addr, geocoder=geo, hydat=hydat_db, dem=ArrayDem(dem),
        basins=FakeBasins([("08LG010", 914.0)]),
        out_root=tmp_path / "r2", hydat_vintage="test")
    assert risk == "High"
    assert any("Reality check" in s for s in est.explanation_chain)
    html = path.read_text(encoding="utf-8")
    assert "Reality check" in html


def test_display_clamp_never_shows_absurd_aep(hydat_db, tmp_path):
    """Valley-floor property: class stays High but the displayed AEP is capped
    at 25% with an honesty caveat (no more '77% a year')."""
    addr = "8900 48 Avenue NW, Calgary, AB"
    geo = FixedGeocoder({addr: (LAT, LON, "A")})
    n = 140
    r, c = np.mgrid[0:n, 0:n]
    wall = 0.5 * (1.0 - np.exp(-np.abs(c - 20) * 0.5 / 0.5))   # ~flat floodplain
    dem = 1050.0 + 0.01 * r + wall
    path, risk, est = run_report(
        addr, geocoder=geo, hydat=hydat_db, dem=ArrayDem(dem),
        basins=FakeBasins([("05BJ001", 1200.0)]),
        out_root=tmp_path / "r3", hydat_vintage="test")
    assert risk == "High"
    assert est.aep_central <= 0.25 + 1e-9


# ---------------------------------------------------------------- v0.5 fixes

def big_river_entering_window_dem(n=140, hand=1.8):
    """Bowness/High River scenario: a major river crosses the window near the
    property; its true basin (thousands of km²) lies OUTSIDE, so in-window
    accumulation is modest. The property sits `hand` m above it."""
    r, c = np.mgrid[0:n, 0:n]
    ch = n // 2 - 6                                # river passes near centre
    wall = hand * (1.0 - np.exp(-np.abs(c - ch) * 0.5 / max(hand, 0.1)))
    return 1040.0 + 0.001 * r + wall


def test_window_capped_scale_matches_river_near_property(hydat_db, tmp_path):
    """v0.4 field failure: gauge DA 1200 km² -> 60 km² target unreachable in a
    ~17 km² window -> HAND measured to the window exit 6 km away. v0.5 must
    match the river beside the property and produce High (with reach-back or
    banding), not a floor-AEP Low."""
    tm = TerrainModel(big_river_entering_window_dem(), 30.0)
    res = tm.hand_at_scale(60.0)                   # far beyond window capacity
    assert res.hand_m < 3.0                        # river matched NEARBY
    assert res.stream_distance_m < 1500
    assert not res.reached_scale                   # honesty flag retained

    addr = "8900 48 Avenue NW, Calgary, AB"
    geo = FixedGeocoder({addr: (LAT, LON, "A")})
    path, risk, est = run_report(
        addr, geocoder=geo, hydat=hydat_db,
        dem=ArrayDem(big_river_entering_window_dem()),
        basins=FakeBasins([("08LG010", 914.0)]),   # record contains the flood
        out_root=tmp_path / "r5", hydat_vintage="test")
    assert risk == "High"
    assert any("Reality check" in x for x in est.explanation_chain)
    assert est.aep_central > 0.001                 # not pinned at the floor


def test_regulated_canal_deprioritized(hydat_db, tmp_path):
    """A CANAL station must never lead the analysis when a river gauge exists."""
    import sqlite3 as _sq
    conn = _sq.connect(hydat_db.path)
    conn.execute("INSERT INTO STATIONS VALUES (?,?,?,?,?,?,?)",
                 ("05XX001", "LITTLE BOW CANAL AT HIGH RIVER", 50,
                  LAT + 0.001, LON + 0.001, 50, "A"))
    for y in range(1990, 2026):
        conn.execute("INSERT INTO ANNUAL_INSTANT_PEAKS VALUES (?,?,?,?,?,?)",
                     ("05XX001", "Q", y, "H", 40.0 + (y % 7), None))
    conn.commit(); conn.close()
    addr = "309B Macleod Trail SW, High River, AB"
    geo = FixedGeocoder({addr: (LAT, LON, "A")})
    path, risk, est = run_report(
        addr, geocoder=geo, hydat=hydat_db,
        dem=ArrayDem(big_river_entering_window_dem()),
        basins=FakeBasins([]),                     # force proximity fallback
        out_root=tmp_path / "r6", hydat_vintage="test")
    assert est.gauges_used[0] != "05XX001"         # canal never first


# ---------------------------------------------------------------- v0.6 fixes

def test_clamped_bootstrap_never_grades_A():
    """Sherbrooke field bug: all bootstrap draws pinned at the AEP floor gave
    sigma ~ 3e-6 -> grade A on a wild extrapolation. Clamp-degeneracy must
    force the unresolvable regime (grade D)."""
    from hazardwise.models.frequency import bootstrap_gev_aep
    from hazardwise.models.estimate import estimate_gauge
    from hazardwise.tests.test_engine1 import make_ams as _mk
    def _ams(n, seed, loc, scale, sid, name):
        a = _mk(n, seed=seed, loc=loc, scale=scale)
        from hazardwise.models.ams import AnnualMaximumSeries
        return AnnualMaximumSeries(sid, name, a.years, a.flows_m3s)
    ams = _ams(44, seed=6, loc=1000, scale=250, sid="02OF001",
               name="SAINT-FRANCOIS (RIVIERE) A RICHMOND-1")
    huge = float(np.max(ams.flows_m3s)) * 8.0        # far beyond any draw
    b = bootstrap_gev_aep(ams.flows_array(), huge)
    assert b.clamped_fraction > 0.10
    assert b.log10_sigma >= 1.0
    est = estimate_gauge(ams, huge)
    assert est.grade == "D"


def test_regulated_parsing_water_body_only():
    from hazardwise.pipeline import _is_regulated_structure
    assert _is_regulated_structure("LITTLE BOW CANAL AT HIGH RIVER")
    assert not _is_regulated_structure("HIGHWOOD RIVER BELOW LITTLE BOW CANAL")
    assert not _is_regulated_structure("BOW RIVER NEAR THE DIVERSION")
    assert _is_regulated_structure("WESTERN IRRIGATION DIVERSION NEAR STRATHMORE")


def test_stale_record_caveat_and_cap(hydat_db, tmp_path):
    """A gauge record ending decades ago must cap the grade at C and warn that
    recent floods are not represented (Sherbrooke/Quebec failure mode)."""
    import sqlite3 as _sq
    conn = _sq.connect(hydat_db.path)
    conn.execute("INSERT INTO STATIONS VALUES (?,?,?,?,?,?,?)",
                 ("02OF900", "SAINT-FRANCOIS (RIVIERE) A TESTVILLE", 8664,
                  LAT + 0.001, LON + 0.001, 8664, "D"))
    for y, q in zip(range(1930, 1973),
                    np.maximum(np.random.default_rng(8).gumbel(1000, 300, 43), 10)):
        conn.execute("INSERT INTO ANNUAL_INSTANT_PEAKS VALUES (?,?,?,?,?,?)",
                     ("02OF900", "Q", y, "H", float(q), None))
    conn.commit(); conn.close()
    addr = "85 Bowen Nord, Sherbrooke, QC"
    geo = FixedGeocoder({addr: (LAT, LON, "A")})
    path, risk, est = run_report(
        addr, geocoder=geo, hydat=hydat_db,
        dem=ArrayDem(big_river_entering_window_dem(hand=6.5)),
        basins=FakeBasins([("02OF900", 8664.0)]),
        out_root=tmp_path / "r7", hydat_vintage="test")
    assert est.grade in ("C", "D")
    assert any("ends in 1972" in x for x in est.explanation_chain)
    assert any("may be an underestimate" in c for c in est.mandatory_caveats)


# ---------------------------------------------------------------- v0.7 fixes

def test_selection_includes_mainstem_not_two_creeks(hydat_db, tmp_path):
    """Constance Bay failure: two tiny tributary basins must not crowd out the
    mainstem river the property actually sits on."""
    import sqlite3 as _sq
    conn = _sq.connect(hydat_db.path)
    for sid, name, da, loc in [("02KF900", "TINY CREEK A", 40, 5),
                               ("02KF901", "TINY CREEK B", 90, 8),
                               ("02KF005", "OTTAWA RIVER MAINSTEM", 90000, 3000)]:
        conn.execute("INSERT INTO STATIONS VALUES (?,?,?,?,?,?,?)",
                     (sid, name, da, LAT + 0.01, LON + 0.01, da, "A"))
        rng = np.random.default_rng(da)
        for y, q in zip(range(1985, 2026),
                        np.maximum(rng.gumbel(loc, loc * 0.4, 41), 0.5)):
            conn.execute("INSERT INTO ANNUAL_INSTANT_PEAKS VALUES (?,?,?,?,?,?)",
                         (sid, "Q", y, "H", float(q), None))
    conn.commit(); conn.close()
    addr = "Bayview Drive, Constance Bay, Woodlawn, ON"
    geo = FixedGeocoder({addr: (LAT, LON, "A")})
    _, risk, est = run_report(
        addr, geocoder=geo, hydat=hydat_db,
        dem=ArrayDem(big_river_entering_window_dem(hand=1.8)),
        basins=FakeBasins([("02KF900", 40.0), ("02KF901", 90.0),
                           ("02KF005", 90000.0)]),
        out_root=tmp_path / "r8", hydat_vintage="test")
    assert "02KF005" in est.gauges_used          # mainstem included
    assert "02KF900" in est.gauges_used          # most-local kept too


def test_unresolvable_gauge_does_not_drag_grade(hydat_db, tmp_path):
    """v0.6 field pattern: every report grade D because one secondary gauge's
    threshold clamped. The resolvable primary must set the grade."""
    from hazardwise.models.estimate import estimate_property_flood_risk
    good = _mk_ams(50, 11, 400, 120, "05BJ001", "ELBOW RIVER")
    degen = _mk_ams(45, 12, 30, 8, "05XX900", "SMALL CREEK")
    thr_good = float(np.percentile(np.array(good.flows_m3s), 92))
    thr_degen = float(np.max(degen.flows_m3s)) * 9.0     # clamps hard
    est = estimate_property_flood_risk([(good, thr_good), (degen, thr_degen)])
    assert est.per_gauge["05XX900"].unresolvable
    assert est.grade in ("A", "B")               # not dragged to D
    assert any("Excluded from the headline" in x for x in est.explanation_chain)


def _mk_ams(n, seed, loc, scale, sid, name):
    from scipy import stats as _st
    from hazardwise.models.ams import AnnualMaximumSeries
    rng = np.random.default_rng(seed)
    flows = np.maximum(_st.gumbel_r(loc=loc, scale=scale).rvs(n, random_state=rng), 1.0)
    return AnnualMaximumSeries(sid, name, tuple(range(2026 - n, 2026)), tuple(flows))


def test_distant_megabasin_never_selected_as_mainstem(hydat_db, tmp_path):
    """High River v0.7 failure: the Nelson River basin (1.3M km², station
    1,300 km away in Manitoba) contains all of southern Alberta and must never
    be picked as the 'mainstem'. The nearby Highwood-like gauge must win, and
    a regulated canal must lose the smallest-basin tie to a river."""
    import sqlite3 as _sq
    conn = _sq.connect(hydat_db.path)
    rows = [
        # sid, name, DA, lat, lon (fixture property is at LAT, LON)
        ("05BL904", "HIGHWOOD RIVER NEAR THE MOUTH", 1955, LAT + 0.02, LON + 0.02),
        ("05BL915", "LITTLE BOW CANAL AT HIGH RIVER", 1955, LAT + 0.02, LON + 0.02),
        ("05UF907", "NELSON RIVER AT LONG SPRUCE GS", 1308256, LAT + 8.0, LON + 16.0),
    ]
    rng = np.random.default_rng(3)
    for sid, name, da, la, lo in rows:
        conn.execute("INSERT INTO STATIONS VALUES (?,?,?,?,?,?,?)",
                     (sid, name, "XX", la, lo, da, "A"))
        for y, q in zip(range(1986, 2026),
                        np.maximum(rng.gumbel(300, 100, 40), 5)):
            conn.execute("INSERT INTO ANNUAL_INSTANT_PEAKS VALUES (?,?,?,?,?,?)",
                         (sid, "Q", y, "H", float(q), None))
    conn.commit(); conn.close()
    addr = "cottage near High River v071"
    geo = FixedGeocoder({addr: (LAT, LON, "A")})
    _, risk, est = run_report(
        addr, geocoder=geo, hydat=hydat_db,
        dem=ArrayDem(big_river_entering_window_dem(hand=2.3)),
        basins=FakeBasins([("05BL904", 1955.0), ("05BL915", 1955.0),
                           ("05UF907", 1308256.0)]),
        out_root=tmp_path / "r9", hydat_vintage="test")
    assert "05UF907" not in est.gauges_used       # megabasin excluded
    assert est.gauges_used[0] == "05BL904"        # river wins tie over canal


# ---------------------------------------------------------------- v0.8 fixes

def test_measured_stage_reachback_fires_when_rating_is_silent(hydat_db, tmp_path):
    """Grand Forks reconstruction: the 2018 flood is IN the record and flooded
    downtown, but the 0.4-exponent rating converts 3.5x Q2 into a rise below
    the property's HAND. Measured stage data (DATA_TYPE='H') showing a 4.5 m
    rise must floor the class at High with 'measured, not modeled' language."""
    import sqlite3 as _sq
    conn = _sq.connect(hydat_db.path)
    conn.execute("INSERT INTO STATIONS VALUES (?,?,?,?,?,?,?)",
                 ("08NN012", "KETTLE RIVER AT GRAND FORKS", 5750,
                  LAT + 0.01, LON + 0.01, 5750, "A"))
    rng = np.random.default_rng(18)
    for y, q in zip(range(1980, 2026),
                    np.maximum(rng.gumbel(200, 60, 46), 5)):
        q = 700.0 if y == 2018 else float(q)          # the flood, ~3.5x Q2
        conn.execute("INSERT INTO ANNUAL_INSTANT_PEAKS VALUES (?,?,?,?,?,?)",
                     ("08NN012", "Q", y, "H", q, None))
    for y in range(1980, 2026):                        # measured stage maxima
        lvl = 6.5 if y == 2018 else float(2.0 + rng.normal(0, 0.25))
        conn.execute("INSERT INTO ANNUAL_INSTANT_PEAKS VALUES (?,?,?,?,?,?)",
                     ("08NN012", "H", y, "H", lvl, None))
    conn.commit(); conn.close()

    addr = "7217 4th Street, Grand Forks, BC"
    geo = FixedGeocoder({addr: (LAT, LON, "A")})
    _, risk, est = run_report(
        addr, geocoder=geo, hydat=hydat_db,
        dem=ArrayDem(big_river_entering_window_dem(hand=4.0)),
        basins=FakeBasins([("08NN012", 5750.0)]),
        out_root=tmp_path / "r10", hydat_vintage="test")
    assert risk == "High"
    assert any("measured" in x and "floors the risk class" in x
               for x in est.explanation_chain)


def test_regulated_loses_float_dust_tie(hydat_db, tmp_path):
    """Canal polygon 1,954.9 km² vs river 1,955.3 km²: near-ties must resolve
    to the river, not to whichever float is microscopically smaller."""
    import sqlite3 as _sq
    conn = _sq.connect(hydat_db.path)
    rng = np.random.default_rng(21)
    for sid, name in [("05BL904", "HIGHWOOD RIVER NEAR HIGH RIVER"),
                      ("05BL915", "LITTLE BOW CANAL AT HIGH RIVER")]:
        conn.execute("INSERT INTO STATIONS VALUES (?,?,?,?,?,?,?)",
                     (sid, name, "AB", LAT + 0.01, LON + 0.01, 1955, "A"))
        for y, q in zip(range(1986, 2026),
                        np.maximum(rng.gumbel(150, 60, 40), 5)):
            conn.execute("INSERT INTO ANNUAL_INSTANT_PEAKS VALUES (?,?,?,?,?,?)",
                         (sid, "Q", y, "H", float(q), None))
    conn.commit(); conn.close()
    addr = "float tie test"
    geo = FixedGeocoder({addr: (LAT, LON, "A")})
    _, _, est = run_report(
        addr, geocoder=geo, hydat=hydat_db,
        dem=ArrayDem(big_river_entering_window_dem(hand=2.3)),
        basins=FakeBasins([("05BL915", 1954.9), ("05BL904", 1955.3)]),
        out_root=tmp_path / "r11", hydat_vintage="test")
    assert est.gauges_used[0] == "05BL904"        # river wins the near-tie


# --------------------------------------------------------------- v0.10 fixes

def kamloops_gorge_dem(n=140, river_hand=5.0, gorge_depth=17.0):
    """Big river passing ~900 m from the property; a deep incised creek gorge
    passing ~450 m away that also carries qualifying upstream area. Nearest-
    cell HAND picks the gorge (17 m); min-HAND must pick the river (5 m)."""
    r, c = np.mgrid[0:n, 0:n]
    riv = 25                                     # river column (~2.7 km at 30 m)
    ctr = n // 2
    wall = river_hand * (1.0 - np.exp(-np.abs(c - riv) * 0.5 / river_hand))
    dem = 340.0 + 0.001 * r + wall
    gorge_col = ctr + 15                         # ~450 m east of the property
    dem[:, gorge_col] -= gorge_depth             # full-length incised creek
    return dem


def test_min_hand_picks_the_river_not_the_gorge():
    from hazardwise.terrain.hand import TerrainModel
    tm = TerrainModel(kamloops_gorge_dem(), 30.0)
    res = tm.hand_at_scale(1.0)
    assert res.hand_m < 8.0, f"gorge won again: HAND={res.hand_m:.1f}"
    assert res.hand_m > 2.0                      # still real standing


def test_kamloops_scenario_end_to_end(hydat_db, tmp_path):
    """With min-HAND (5 m instead of the gorge's 17 m) plus measured stage
    showing a 5.5 m historical rise, the class must floor at High. The
    statistical estimate may honestly remain unresolvable for a small gauge —
    the measured evidence carries it."""
    import sqlite3 as _sq
    conn = _sq.connect(hydat_db.path)
    rng = np.random.default_rng(31)
    for y in range(1985, 2026):
        lvl = 7.5 if y == 2018 else float(2.0 + rng.normal(0, 0.25))
        conn.execute("INSERT INTO ANNUAL_INSTANT_PEAKS VALUES (?,?,?,?,?,?)",
                     ("05BJ001", "H", y, "H", lvl, None))
    conn.commit(); conn.close()
    addr = "7 Victoria Street West, Kamloops, BC"
    geo = FixedGeocoder({addr: (LAT, LON, "B")})
    _, risk, est = run_report(
        addr, geocoder=geo, hydat=hydat_db,
        dem=ArrayDem(kamloops_gorge_dem()),
        basins=FakeBasins([("05BJ001", 1200.0)]),
        out_root=tmp_path / "r12", hydat_vintage="test")
    assert risk == "High"
    assert any("measured" in x and "floors the risk class" in x
               for x in est.explanation_chain)


def test_locality_centroid_gets_honest_caveat(hydat_db, tmp_path):
    from hazardwise.geocoding.geocoder import GeocodeResult

    class CentroidGeocoder:
        def geocode(self, address):
            return GeocodeResult(LAT, LON, address, "C", 3,
                                 "locality centroid only (stage 3)")

    addr = "Bayview Drive, Constance Bay, Woodlawn, ON"
    _, risk, est = run_report(
        addr, geocoder=CentroidGeocoder(), hydat=hydat_db,
        dem=ArrayDem(kamloops_gorge_dem()),
        basins=FakeBasins([("05BJ001", 1200.0)]),
        out_root=tmp_path / "r13", hydat_vintage="test")
    assert any("OPPOSITE risk" in c for c in est.mandatory_caveats)
