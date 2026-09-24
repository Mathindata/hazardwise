import numpy as np
from pathlib import Path
from hazardwise.satellite.basemap import deg2num
from hazardwise.satellite.aoi import AOI
from hazardwise.satellite.extent_stack import ExtentStack
from hazardwise.satellite.surface import fuse
from hazardwise.satellite.render import render_map
from hazardwise.combined_report import load_panel, _score

def test_tile_math():
    x, y = deg2num(0.0, 0.0, 1)
    assert (int(x), int(y)) == (1, 1)
    x, y = deg2num(51.05, -114.07, 12)   # Calgary lands in-range
    assert 0 <= x < 4096 and 0 <= y < 4096

def test_viz_options_plumb_through(tmp_path):
    aoi = AOI(50.58, -113.87); n = aoi.n
    x = np.abs(np.arange(n) - n / 2)
    hand = np.tile(x * 0.12, (n, 1))
    prior = np.clip(0.06 * np.exp(-hand / 2.5), 0, 0.06)
    fus = fuse(prior, np.zeros((n, n), int), 0.0, 0.5, "GAUGE_OK")
    stack = ExtentStack(n, hand)
    meta = render_map(aoi, fus, hand, stack, str(tmp_path / "m.png"),
                      basemap_img=np.zeros((n, n, 3)), basemap_alpha=0.3,
                      layer_alpha=0.4, hatches=(),
                      colors=("#111111", "#333333", "#555555",
                              "#777777", "#999999"), grade=None)
    assert (tmp_path / "m.png").exists()
    assert meta["mode"] == "probability"
    assert meta["legend_blocks"]["provenance"]

def test_panel_csv_and_scoring():
    rows = load_panel(Path("hazardwise/validation/panel_addresses.csv"))
    assert len(rows) >= 20
    assert {"address", "expected_flood", "expected_fire"} <= set(rows[0])
    assert _score("High", "High") == "MATCH"
    assert _score("High", "Low") == "TWO-STEP"
    assert _score("Undetermined", "High") == "Undetermined"

def test_v018_dual_single_report(tmp_path, monkeypatch):
    """One command path: single report, both hazards, two maps, two chains."""
    import json, numpy as np, geopandas as gpd
    from shapely.geometry import box
    from hazardwise.dual_report import run_dual
    from hazardwise.tests.test_v014_satonly import FakeDem, _occ_for
    from hazardwise.tests.test_v016_fire import MountainDem, JASPER
    lat, lon = JASPER
    d = 0.004
    gdf = gpd.GeoDataFrame({"YEAR": [2024], "geometry": [
        box(lon - d, lat - d, lon + d, lat + d)]}, crs="EPSG:4326")
    nb = tmp_path / "nbac"; nb.mkdir()
    gdf.to_file(nb / "n.gpkg", driver="GPKG")
    monkeypatch.setenv("HW_NBAC_DIR", str(nb))
    occ, _ = _occ_for()
    path, (fr, fp), (ar, ap_) = run_dual(
        "dual scenario site", coords=JASPER, out_root=tmp_path / "out",
        flood_kwargs={"dem": FakeDem(), "occ": occ},
        fire_kwargs={"dem": MountainDem()})
    html = path.read_text()
    assert "Flood risk (satellite-only)" in html
    assert "Wildfire risk (archive-only)" in html
    assert html.count("data:image/png;base64,") >= 2
    j = json.loads((path.parent / "report.json").read_text())
    assert j["mode"] == "dual" and j["hazards"] == ["flood", "fire"]
    assert j["estimate"]["grade"] == "C"          # flood, benchmark-audited
    assert j["fire"]["estimate"]["grade"] == "C"  # fire graded via NBAC
    assert fr in ("High", "Medium", "Low") and ar in ("High", "Medium")

def test_v019_aux_maps_and_aerial(tmp_path):
    import numpy as np
    from hazardwise.satellite.aux_maps import (render_flood_context,
                                               render_fire_context)
    from hazardwise.satellite.basemap import AERIAL
    n = 201
    y, x = np.mgrid[0:n, 0:n]
    dem = 1000.0 + 0.3*np.abs(x-n//2) + 0.05*y
    occ = np.zeros((n, n)); occ[:, n//2-2:n//2+2] = 95.0
    occ[:, n//2+2:n//2+8] = 30.0
    p1 = render_flood_context(dem, 30.0, occ,
                              str(tmp_path/"fc.png"), 6.0)
    burn = np.zeros((n, n), bool); burn[20:80, 20:80] = True
    p2 = render_fire_context(dem, 30.0, [(burn, 2024)],
                             str(tmp_path/"fi.png"), 6.0,
                             wind=(22.0, 270.0))
    from pathlib import Path
    assert Path(p1).exists() and Path(p2).exists()
    assert "{z}" in AERIAL and "World_Imagery" in AERIAL

def test_v019_dual_report_carries_context(tmp_path, monkeypatch):
    import json, geopandas as gpd
    from shapely.geometry import box
    from hazardwise.dual_report import run_dual
    from hazardwise.tests.test_v014_satonly import FakeDem, _occ_for
    from hazardwise.tests.test_v016_fire import MountainDem, JASPER
    lat, lon = JASPER; d = 0.004
    gdf = gpd.GeoDataFrame({"YEAR": [2024], "geometry": [
        box(lon-d, lat-d, lon+d, lat+d)]}, crs="EPSG:4326")
    nb = tmp_path/"nbac"; nb.mkdir()
    gdf.to_file(nb/"NBAC_1972to2025_test.gpkg", driver="GPKG")
    monkeypatch.setenv("HW_NBAC_DIR", str(nb))
    occ, _ = _occ_for()
    path, _, _ = run_dual("aux scenario", coords=JASPER,
                          out_root=tmp_path/"out",
                          flood_kwargs={"dem": FakeDem(), "occ": occ},
                          fire_kwargs={"dem": MountainDem()})
    html = path.read_text()
    assert html.count("Regional context") >= 1   # fire aux via loader path

def test_v019_rural_geocode_cascade():
    from hazardwise.geocoding.rural import (RuralAwareGeocoder, _variants,
                                            _locality)
    vs = _variants("61 562007 Rr 113, Rural Two Hills County, AB")
    assert any("Range Road 113" in v and "Rural" not in v for v in vs)
    assert _locality("61 562007 Rr 113, Rural Two Hills County, AB") \
        == "Two Hills County, AB"

    class FakeBase:
        def geocode(self, a):
            if "Range Road 113" in a and "Rural" not in a:
                import types
                return types.SimpleNamespace(latitude=53.7, longitude=-111.7,
                                             method="fake match")
            raise RuntimeError("no match")
    g = RuralAwareGeocoder(base=FakeBase())
    r = g.geocode("61 562007 Rr 113, Rural Two Hills County, AB")
    assert abs(r.latitude - 53.7) < 1e-6
    assert "rural variant" in r.method

def test_v019_locality_fallback_is_gated():
    from hazardwise.geocoding.rural import RuralAwareGeocoder

    class OnlyLocality:
        def geocode(self, a):
            if a == "Two Hills County, AB":
                import types
                return types.SimpleNamespace(latitude=53.6, longitude=-111.8,
                                             method="county")
            raise RuntimeError("no match")
    import hazardwise.geocoding.rural as rural
    rural._photon = lambda q: None            # keep the test offline
    r = RuralAwareGeocoder(base=OnlyLocality()).geocode(
        "999 000000 Nowhere Trail, Rural Two Hills County, AB")
    assert "centroid" in r.method             # triggers the precision gate

def test_v019_all_three_pipelines_use_rural_cascade():
    """Regression for the gap this test exists to prevent: the rural
    geocoder must be the default in EVERY entry point, not just satellite."""
    from pathlib import Path
    import hazardwise
    root = Path(hazardwise.__file__).parent
    for fn in ("pipeline.py", "sat_pipeline.py", "fire_pipeline.py"):
        assert "RuralAwareGeocoder" in (root / fn).read_text(), fn

def test_v020_bump_aware_flood_audit():
    from hazardwise.satellite.integrity import grade_vs_map
    meta = {"mode": "probability", "address_bin": -1}
    assert grade_vs_map("Medium", meta) == 1              # AEP bin says Low
    assert grade_vs_map("Medium", meta, bumped=True) == 0 # terrain bump: ok
    assert grade_vs_map("High", meta, bumped=True) == 1   # two steps: never

def test_v020_trust_guards_present():
    from pathlib import Path
    import hazardwise
    root = Path(hazardwise.__file__).parent
    pipe = (root / "pipeline.py").read_text()
    assert "GAUGE_SCALE_REJECT_RATIO" in pipe            # fix 1 wired
    assert "SCALE-COMPATIBILITY REFUSAL" in pipe
    assert '"D": "D"' in pipe                            # fix 2 wired
    from hazardwise import params as P
    assert P.GAUGE_SCALE_REJECT_RATIO == 30.0

def test_v021_regional_history_buckets():
    from hazardwise.satellite.history import (bucket, summarize,
                                              history_indices, RegionalEvent)
    now = 2026
    evs = [RegionalEvent(2024, "fire", 3.2, "x"),
           RegionalEvent(2011, "fire", 8.0, "x"),
           RegionalEvent(1988, "fire", 12.0, "x"),
           RegionalEvent(1970, "fire", 5.0, "x")]   # >50 yr, excluded
    b = bucket(evs, now)
    assert len(b[10]) == 1 and len(b[20]) == 2 and len(b[50]) == 3
    lines = summarize(evs, "fires", now)
    assert "Past 10 years: 1 fires" in lines[0]
    assert "nearest 3.2 km (2024)" in lines[0]
    assert "Past 50 years: 3 fires" in lines[2]
    idx = history_indices(evs, now=now)
    assert idx["Fires within 10 yr (regional)"].startswith("1")
    assert idx["Fires within 50 yr (regional)"].startswith("3")

def test_v021_empty_history_is_honest():
    from hazardwise.satellite.history import summarize
    lines = summarize([], "mapped fires", now=2026)
    assert all("no mapped fires recorded" in l for l in lines)
    assert len(lines) == 3
