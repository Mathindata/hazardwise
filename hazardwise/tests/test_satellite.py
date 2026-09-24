import numpy as np
import pandas as pd
import pytest
from hazardwise.satellite.aoi import AOI
from hazardwise.satellite import params_sat as P
from hazardwise.satellite.events import (cluster_events, event_observation_table,
                                         annual_event_rate)
from hazardwise.satellite.s1_flood import classify_scene, iou, otsu_threshold
from hazardwise.satellite.extent_stack import ExtentStack, ExtentLayer
from hazardwise.satellite.frequency import wilson_interval, effective_n
from hazardwise.satellite.surface import fuse
from hazardwise.satellite.report_hook import build_map_section

def synthetic_valley(n):
    """HAND valley: low along the center column band, rising outward."""
    x = np.abs(np.arange(n) - n / 2)
    hand = np.tile(x * 0.15, (n, 1))          # 0 m center -> ~22 m at edges
    return hand

def make_events():
    idx = pd.date_range("2015-01-01", "2024-12-31", freq="D")
    flow = pd.Series(10.0, index=idx)
    for yr in (2016, 2018, 2020, 2022, 2024):   # five events, ~6 days each
        flow.loc[f"{yr}-06-10":f"{yr}-06-15"] = 500.0
    return cluster_events(flow, threshold=400.0)

def test_event_clustering_and_rate():
    ev = make_events()
    assert len(ev) == 5
    assert all(e.duration_days >= 6 for e in ev)
    assert annual_event_rate(ev, 10.0) == pytest.approx(0.5)

def test_scene_matching_and_capture():
    ev = make_events()
    scenes = [e.start + pd.Timedelta(days=1) for e in ev[:4]]  # 4 of 5 observed
    t = event_observation_table(ev, scenes, revisit_days=6.0)
    assert t["observed"].sum() == 4
    assert 3.0 <= effective_n(t) <= 4.0

def test_s1_classifier_and_iou():
    n = 120
    x = np.abs(np.arange(n) - n / 2)
    hand = np.tile(x * 0.4, (n, 1))            # steeper: edges ~24 m > HAND guard
    truth = hand < 3.0
    dry = np.full((n, n), -8.0)
    scene = dry.copy()
    scene[truth] = -16.0                        # backscatter drop over water
    wet = classify_scene(scene, dry, hand)
    assert iou(wet, truth) > 0.9
    # HAND guard: darken a high-HAND patch (radar shadow) -> must stay dry
    scene2 = scene.copy(); scene2[5:15, 5:15] = -20.0
    wet2 = classify_scene(scene2, dry, hand)
    assert not wet2[5:15, 5:15].any()

def test_stack_hygiene_and_event_counting():
    n = 100; hand = synthetic_valley(n)
    st = ExtentStack(n, hand)
    occ = np.zeros((n, n)); occ[:, n//2-2:n//2+2] = 95.0   # river channel
    st.set_permanent_water_from_occurrence(occ)
    m = hand < 4.0
    for i, d in enumerate(["2016-06-11", "2016-06-12", "2020-06-11"]):
        st.add(ExtentLayer(m, "EGS", "E000" if i < 2 else "E002", pd.Timestamp(d)))
    k = st.wet_event_count()
    assert k.max() == 2                          # two DISTINCT events, not 3 layers
    assert not k[st.permanent_water].any()       # in-channel hygiene
    # unvetted S1 layer below IoU gate is excluded
    st.add(ExtentLayer(m, "S1_SELF", "E004", pd.Timestamp("2024-06-11"), iou_vs_egs=0.3))
    assert st.wet_event_count().max() == 2
    # era filter (regulated reach)
    st2 = ExtentStack(n, hand)
    st2.add(ExtentLayer(m, "EGS", "E000", pd.Timestamp("1990-06-01")),
            era_start=pd.Timestamp("2000-01-01"))
    assert len(st2.layers) == 0

def test_wilson_never_bare():
    lo, hi = wilson_interval(0, 1)
    assert hi > 0.5                              # one dry event proves ~nothing

def test_fusion_modes_and_monotonicity():
    n = 80; hand = synthetic_valley(n)
    prior = np.clip(0.05 * np.exp(-hand / 3.0), 0, 0.05)
    k = np.zeros((n, n), int); k[hand < 2.0] = 3
    f_ok = fuse(prior, k, n_eff=4.0, lam=0.5, gauge_case="GAUGE_OK")
    f_far = fuse(prior, k, n_eff=4.0, lam=0.5, gauge_case="GAUGE_FAR")
    assert f_ok.mode == "probability"
    assert np.all((f_ok.aep >= 0) & (f_ok.aep <= 1))
    assert np.all(f_ok.aep_lo <= f_ok.aep + 1e-9) and np.all(f_ok.aep <= f_ok.aep_hi + 1e-9)
    # observed-wet pixels must exceed never-wet pixels at equal prior
    assert f_ok.aep[hand < 2.0].mean() > f_ok.aep[(hand > 4) & (hand < 5.5)].mean()
    # weaker prior -> data dominates more where k=0 and prior was high-ish
    wet = hand < 2.0
    assert f_far.aep[wet].mean() >= f_ok.aep[wet].mean() - 1e-9

def test_end_to_end_report_hook(tmp_path):
    n_ev = make_events()
    scenes = [e.start + pd.Timedelta(days=1) for e in n_ev]
    obs = event_observation_table(n_ev, scenes, revisit_days=6.0)
    aoi_n = AOI(51.0, -114.0).n
    hand = synthetic_valley(aoi_n)
    prior = np.clip(0.05 * np.exp(-hand / 3.0), 0, 0.05)
    occ = np.zeros_like(hand); occ[:, aoi_n//2-3:aoi_n//2+3] = 95.0
    layers = [ExtentLayer(hand < 3.0, "EGS", e.event_id, e.start) for e in n_ev[:3]]
    out = build_map_section(51.0, -114.0, hand, prior, n_ev, obs, 10.0, layers,
                            occurrence_pct=occ, gauge_case="GAUGE_OK",
                            grade="High", out_png=str(tmp_path / "map.png"),
                            date_range="2015-2024")
    assert (tmp_path / "map.png").exists()
    assert out["map_meta"]["mode"] == "probability"
    assert out["integrity"]["map_legend_missing"] == 0
    assert out["integrity"]["map_bad_susceptibility_legend"] == 0
    assert out["integrity"]["map_extent_grade_conflicts"] == 0
    assert out["address_aep"] is not None

def test_susceptibility_mode_and_gate(tmp_path):
    aoi_n = AOI(51.0, -114.0, geocode_precision_m=500.0).n
    hand = synthetic_valley(aoi_n)
    out = build_map_section(51.0, -114.0, hand, np.zeros_like(hand), [], None,
                            10.0, [], gauge_case="UNGAUGED",
                            geocode_precision_m=500.0, grade=None,
                            out_png=str(tmp_path / "s.png"))
    assert out["map_meta"]["mode"] == "susceptibility"
    assert out["map_meta"]["geocode_gated"] is True
    assert out["map_meta"]["legend_blocks"]["bins"] is False
    assert out["integrity"]["map_bad_susceptibility_legend"] == 0
    assert out["address_aep"] is None

def test_otsu_sane():
    x = np.concatenate([np.random.normal(-18, 1, 3000), np.random.normal(-8, 1, 3000)])
    t = otsu_threshold(x)
    assert -16 < t < -10
