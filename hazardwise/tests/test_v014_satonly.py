import numpy as np
import json
from pathlib import Path
from hazardwise.satellite import occurrence as socc
from hazardwise.sat_pipeline import run_sat_report
from hazardwise.terrain.hand import TerrainModel
from hazardwise.satellite import wiring as satw
from hazardwise.satellite.aoi import AOI

class FakeDem:
    def window(self, lat, lon, half_km):
        n = 201
        x = np.abs(np.arange(n) - n // 2)[None, :]
        y = np.arange(n)[:, None]
        return 1000.0 + 0.4 * x + 0.02 * y, 30.0

def _occ_for(lat=50.58, lon=-113.87):
    t = TerrainModel(*FakeDem().window(lat, lon, 6.0))
    hraw, _ = satw.hand_raster(t, 5.0)
    hand = satw.to_aoi_grid(hraw, 30.0, AOI(lat, lon).n)
    occ = np.clip(60.0 * np.exp(-hand / 2.0), 0, 100)
    occ[hand < 0.3] = 95.0                       # channel = permanent water
    return occ, hand

def test_curve_monotone_and_bounded():
    occ, hand = _occ_for()
    c = socc.occurrence_aep_curve(occ, hand)
    assert c is not None
    assert np.all(np.diff(c["p"]) <= 1e-12)      # nonincreasing in HAND
    assert np.all((c["lo"] <= c["p"] + 1e-9) & (c["p"] <= c["hi"] + 1e-9))
    assert socc.eval_curve(c, 0.5) > socc.eval_curve(c, 8.0)

def test_satonly_end_to_end(tmp_path):
    occ, _ = _occ_for()
    path, risk, aep = run_sat_report("valley test site",
                                     coords=(50.58, -113.87),
                                     out_root=tmp_path, dem=FakeDem(), occ=occ)
    j = json.loads((path.parent / "report.json").read_text())
    assert j["mode"] == "satellite_only"
    assert j["estimate"]["grade"] == "C"          # never better without a gauge
    assert 0 < j["estimate"]["aep_central"] <= 0.25 + 1e-9
    assert j["estimate"]["aep_ci_lo"] <= j["estimate"]["aep_central"] \
        <= j["estimate"]["aep_ci_hi"]
    assert any(l.startswith("Model constants:")
               for l in j["estimate"]["explanation_chain"])
    assert j["estimate"]["gauges_used"] == []
    assert sum(int(v) for v in j["map"]["integrity"].values()) == 0
    assert "HAND — height above nearest channel" in j["indices"]
    assert (path.parent / "flood_map.png").exists()
    assert risk in ("High", "Medium", "Low")

def test_satonly_no_occurrence_is_screening(tmp_path):
    path, risk, aep = run_sat_report("no-data site", coords=(50.58, -113.87),
                                     out_root=tmp_path, dem=FakeDem(), occ=None)
    j = json.loads((path.parent / "report.json").read_text())
    assert j["estimate"]["grade"] == "D"
    assert risk == "Undetermined" and aep == 0.0
    assert j["map"]["mode"] == "susceptibility"
    assert j["map"]["legend_blocks"]["bins"] is False
