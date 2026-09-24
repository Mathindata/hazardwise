"""v0.13: satellite map wired into the real TerrainModel + report generator."""
import types
import numpy as np
from pathlib import Path
from hazardwise.terrain.hand import TerrainModel
from hazardwise.satellite import wiring as satw

def _terrain(n=201, cell=30.0):
    x = np.abs(np.arange(n) - n // 2)[None, :]
    y = np.arange(n)[:, None]
    dem = 1000.0 + 0.4 * x * (cell / 30.0) + 0.02 * y   # tilted valley
    return TerrainModel(dem, cell)

def _ams():
    flows = np.array([100.0] * 27 + [900.0, 850.0, 950.0])  # 3 exceedances / 30 yr
    return types.SimpleNamespace(flows_array=lambda: flows, n_years=30,
                                 station_name="TEST RIVER AT TESTVILLE")

def test_gauge_case_mapping():
    assert satw.gauge_case(True, False, 40) == "GAUGE_FAR"
    assert satw.gauge_case(False, True, 40) == "GAUGE_REGULATED"
    assert satw.gauge_case(False, False, 10) == "GAUGE_SHORT"
    assert satw.gauge_case(False, False, 40) == "GAUGE_OK"

def test_hand_raster_and_grid():
    t = _terrain()
    h, target = satw.hand_raster(t, 5.0)
    assert h.shape == t.dem.shape and h.min() >= 0
    assert h[100, 100] < h[100, 5]            # valley floor below valley wall
    g = satw.to_aoi_grid(h, t.cell, 300)
    assert g.shape == (300, 300)

def test_map_section_end_to_end(tmp_path):
    t = _terrain()
    est = types.SimpleNamespace(aep_central=0.06)
    html, meta = satw.build_map_section_html(
        terrain=t, lat=50.58, lon=-113.87, geocode_precision_m=15.0,
        est=est, risk_class="High", case="GAUGE_OK", ams=_ams(),
        threshold=800.0, target_km2=5.0, out_dir=tmp_path,
        constants_stamp="Model constants: TEST")
    assert (tmp_path / "flood_map.png").exists()
    assert "data:image/png;base64," in html
    assert meta["mode"] == "probability"
    assert sum(meta["integrity"].values()) == 0
    assert meta["gauge_case"] == "GAUGE_OK"

def test_geocode_gate(tmp_path):
    t = _terrain()
    est = types.SimpleNamespace(aep_central=0.02)
    html, meta = satw.build_map_section_html(
        terrain=t, lat=50.58, lon=-113.87, geocode_precision_m=250.0,
        est=est, risk_class="Medium", case="GAUGE_FAR", ams=_ams(),
        threshold=800.0, target_km2=5.0, out_dir=tmp_path,
        constants_stamp="Model constants: TEST")
    assert meta["geocode_gated"] is True
    assert sum(meta["integrity"].values()) == 0

def test_v015_bin_hatches_in_legend(tmp_path):
    import types
    t = _terrain()
    est = types.SimpleNamespace(aep_central=0.06)
    html, meta = satw.build_map_section_html(
        terrain=t, lat=50.58, lon=-113.87, geocode_precision_m=15.0,
        est=est, risk_class="High", case="GAUGE_OK", ams=_ams(),
        threshold=800.0, target_km2=5.0, out_dir=tmp_path,
        constants_stamp="Model constants: TEST")
    assert meta["legend_blocks"]["bin_hatches"] is True
    assert sum(meta["integrity"].values()) == 0
