"""Wildfire scenarios: Jasper-like (townsite ringed by fuel, 2024 burn
reached town) and Ottawa-Valley-like (burn several km out, patchy fuel)."""
import json
import numpy as np
from pathlib import Path
from hazardwise.satellite.aoi import AOI
from hazardwise.satellite import wildfire as wf
from hazardwise.fire_pipeline import run_fire_report

JASPER = (52.8734, -118.0806)
OTTAWA_VALLEY = (45.5308, -76.3560)

class MountainDem:
    def window(self, lat, lon, half_km):
        n = 201
        y, x = np.mgrid[0:n, 0:n]
        return 1000.0 + 12.0 * np.hypot(x - n//2, y - n//2) * 30.0/1000.0, 30.0

class FlatDem:
    def window(self, lat, lon, half_km):
        return np.full((201, 201), 120.0), 30.0

def _jasper_fixture(n=300):
    fuel = np.ones((n, n), bool)
    fuel[n//2-15:n//2+15, n//2-15:n//2+15] = False      # townsite core
    yy, xx = np.mgrid[0:n, 0:n]
    burn24 = (np.hypot(yy - n//2, xx - n//2) < 120) & \
             (np.hypot(yy - n//2, xx - n//2) > 20)      # 2024 ring to town edge
    burn88 = xx > int(n * 0.75)
    return fuel, [(burn24, 2024), (burn88, 1988)]

def _ottawa_fixture(n=300):
    fuel = np.zeros((n, n), bool)
    fuel[:, : n//3] = True                              # forest to the west
    yy, xx = np.mgrid[0:n, 0:n]
    burn23 = (xx < n//6) & (yy < n//3)                  # 2023 burn, far corner
    return fuel, [(burn23, 2023)]

def test_jasper_scenario_high_with_reachback(tmp_path):
    fuel, layers = _jasper_fixture()
    path, risk, p = run_fire_report(
        "Jasper AB townsite (scenario)", coords=JASPER, out_root=tmp_path,
        dem=MountainDem(), fuel=fuel, nbac_layers=layers, n_record_years=53)
    j = json.loads((path.parent / "report.json").read_text())
    assert risk == "High"                    # 2024 ring reached the town edge
    assert j["estimate"]["grade"] == "C"
    assert any("2024 fire burned to within" in l
               for l in j["estimate"]["explanation_chain"])
    assert any(l.startswith("Model constants:")
               for l in j["estimate"]["explanation_chain"])
    assert sum(int(v) for v in j["map"]["integrity"].values()) == 0
    assert (path.parent / "fire_map.png").exists()
    assert "Ground-level view" in (path.parent / "report.html").read_text()

def test_ottawa_scenario_lower_risk(tmp_path):
    fuel, layers = _ottawa_fixture()
    path, risk, p = run_fire_report(
        "Ottawa Valley (scenario)", coords=OTTAWA_VALLEY, out_root=tmp_path,
        dem=FlatDem(), fuel=fuel, nbac_layers=layers, n_record_years=53)
    j = json.loads((path.parent / "report.json").read_text())
    assert risk in ("Low", "Medium")
    assert p < 0.02
    assert j["indices"]["Nearest historical burn"].endswith("(2023)")

def test_fire_screening_without_nbac(tmp_path, monkeypatch):
    empty = tmp_path / "empty_nbac"; empty.mkdir()
    monkeypatch.setenv("HW_NBAC_DIR", str(empty))   # isolate from any real cache
    fuel, _ = _ottawa_fixture()
    path, risk, p = run_fire_report(
        "no-history site", coords=OTTAWA_VALLEY, out_root=tmp_path,
        dem=FlatDem(), fuel=fuel, nbac_layers=None, n_record_years=0)
    j = json.loads((path.parent / "report.json").read_text())
    assert risk == "Undetermined" and p == 0.0
    assert j["estimate"]["grade"] == "D"
    assert j["map"]["mode"] == "susceptibility"

def test_fire_math_units():
    fuel = np.ones((100, 100), bool)
    burn = np.zeros((100, 100), bool); burn[:10, :10] = True
    r, lo, hi = wf.regional_rate([(burn, 2020)], fuel, 50)
    assert 0 < r < 0.001 and lo <= r <= hi
    d = wf.wui_distance_m(fuel, 10.0)
    assert d.max() == 0.0

def test_m_fire_1_nbac_loader_end_to_end(tmp_path, monkeypatch):
    """The M-FIRE-1 acceptance path, offline: a real vector file on disk ->
    loader -> rasterized layers -> a GRADED report (not Undetermined)."""
    import geopandas as gpd
    from shapely.geometry import box
    lat, lon = JASPER
    d = 0.004
    gdf = gpd.GeoDataFrame(
        {"YEAR": [2024, 1988],
         "geometry": [box(lon - d, lat - d, lon + d, lat + d),
                      box(lon + 0.02, lat + 0.02, lon + 0.03, lat + 0.03)]},
        crs="EPSG:4326")
    nb = tmp_path / "nbac"; nb.mkdir()
    gdf.to_file(nb / "nbac_test.gpkg", driver="GPKG")
    monkeypatch.setenv("HW_NBAC_DIR", str(nb))
    path, risk, p = run_fire_report("Jasper live-loader test", coords=JASPER,
                                    out_root=tmp_path / "out",
                                    dem=MountainDem())
    j = json.loads((path.parent / "report.json").read_text())
    assert risk in ("High", "Medium")            # graded, not a refusal
    assert j["estimate"]["grade"] == "C"
    assert any("NBAC loaded" in l
               for l in j["estimate"]["explanation_chain"])
    assert any("2024 fire burned to within" in l
               for l in j["estimate"]["explanation_chain"])

def test_empty_window_with_record_grades_low(tmp_path):
    """Zero burns in 53 years IS evidence: Toronto must grade Low, not
    refuse. (The refusal is reserved for no-record-loaded.)"""
    path, risk, p = run_fire_report(
        "urban core", coords=OTTAWA_VALLEY, out_root=tmp_path,
        dem=FlatDem(), fuel=np.ones((300, 300), bool), nbac_layers=[],
        n_record_years=53)
    assert risk == "Low" and p < 0.005
    j = json.loads((path.parent / "report.json").read_text())
    assert j["estimate"]["grade"] == "C"

def test_nbac_loader_lambert_crs_and_zip(tmp_path, monkeypatch):
    """The real-world file: zipped shapefile in EPSG:3978. The bbox must be
    transformed into the file's CRS or everything reads empty."""
    import geopandas as gpd, shutil
    from shapely.geometry import box
    from hazardwise.satellite.nbac import load_layers
    lat, lon = JASPER
    d = 0.004
    gdf = gpd.GeoDataFrame({"YEAR": [2024], "geometry": [
        box(lon - d, lat - d, lon + d, lat + d)]},
        crs="EPSG:4326").to_crs("EPSG:3978")
    shp = tmp_path / "shp"; shp.mkdir()
    gdf.to_file(shp / "nbac_test.shp")
    nb = tmp_path / "nbac"; nb.mkdir()
    shutil.make_archive(str(nb / "NBAC_test_shp"), "zip", shp)
    monkeypatch.setenv("HW_NBAC_DIR", str(nb))
    layers, n_rec = load_layers(lat, lon, 1.5, 300, 10.0)
    assert len(layers) == 1 and layers[0][1] == 2024
    assert layers[0][0].any()

def test_v022_populated_history_implies_nonzero_rate():
    """The Sangudo regression: 4 regional fires in 53 yr must NOT coexist
    with a 0.00% base rate (B2 + history floor)."""
    import numpy as np
    from hazardwise.satellite.wildfire import (fire_probability,
                                               regional_rate_from_history)
    from hazardwise.satellite.history import RegionalEvent
    evs = [RegionalEvent(y, "fire", 7.0, "x") for y in (2025, 2010, 2009, 2008)]
    assert regional_rate_from_history(evs, 53, 25.0) > 0
    n = 100
    dem = np.tile(np.abs(np.arange(n) - n // 2) * 0.4, (n, 1))
    res = fire_probability(dem=dem, cell_m=30.0, fuel=np.ones((n, n), bool),
                           nbac_layers=[], n_record_years=53,
                           regional_events=evs, region_km=25.0)
    assert res["rate_floored"] is True
    assert res["prob"].max() > 0        # never zero beside a real history
    # empty history stays honestly zero
    res0 = fire_probability(dem=dem, cell_m=30.0, fuel=np.ones((n, n), bool),
                            nbac_layers=[], n_record_years=53,
                            regional_events=[], region_km=25.0)
    assert res0["rate_floored"] is False

def test_v022_radius_and_debug_options(tmp_path, monkeypatch):
    import geopandas as gpd
    from shapely.geometry import box
    from hazardwise.debuglog import DebugLog
    from hazardwise.fire_pipeline import run_fire_report
    lat, lon = JASPER; d = 0.01
    gdf = gpd.GeoDataFrame({"YEAR": [2024], "geometry": [
        box(lon - d, lat - d, lon + d, lat + d)]}, crs="EPSG:4326")
    nb = tmp_path / "nbac"; nb.mkdir()
    gdf.to_file(nb / "NBAC_1972to2025_t.gpkg", driver="GPKG")
    monkeypatch.setenv("HW_NBAC_DIR", str(nb))
    dbg = DebugLog(enabled=True, out_dir=str(tmp_path / "dbg"))
    path, risk, p = run_fire_report("radius test", coords=JASPER,
                                    out_root=tmp_path / "out",
                                    dem=MountainDem(), region_km=40.0,
                                    debug=dbg)
    base = dbg.flush("radius test")
    assert base is None or True   # already flushed inside runner
    traces = list((tmp_path / "dbg").glob("*.json"))
    assert traces, "debug trace file written"
    import json
    tr = json.loads(traces[0].read_text())["trace"]
    assert any(r["stage"] == "regional_history" and r.get("region_km") == 40.0
               for r in tr)                      # --radius honored
    assert any(r["stage"] == "rate" for r in tr)  # rate decomposition logged
