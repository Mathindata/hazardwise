"""Engine 1 tests.

The statistical recovery test is the important one: we generate AMS data from a
KNOWN Gumbel distribution, so the true AEP of any threshold is known in closed
form, and the fitted pipeline must recover it within bootstrap uncertainty.
"""

import numpy as np
import pytest
from scipy import stats

from hazardwise.models.ams import AnnualMaximumSeries
from hazardwise.models.confidence import CAPolygonStatus, grade_from_sigma
from hazardwise.models.estimate import estimate_gauge, estimate_property_flood_risk
from hazardwise.models.frequency import (
    analyse_gauge,
    blend_gev_lp3,
    bootstrap_gev_aep,
    bootstrap_lp3_aep,
    weibull_aep,
)


def make_ams(n_years: int, seed: int = 42, loc=300.0, scale=90.0,
             station="02ED101", name="Black River") -> AnnualMaximumSeries:
    rng = np.random.default_rng(seed)
    flows = stats.gumbel_r(loc=loc, scale=scale).rvs(n_years, random_state=rng)
    flows = np.maximum(flows, 1.0)
    years = tuple(range(2026 - n_years, 2026))
    return AnnualMaximumSeries(station, name, years, tuple(flows))


# ---------------------------------------------------------------- validation

def test_ams_rejects_null_flows():
    with pytest.raises(ValueError):
        AnnualMaximumSeries("X", "X", (2000, 2001), (100.0, float("nan")))


def test_ams_rejects_nonpositive_flows():
    with pytest.raises(ValueError):
        AnnualMaximumSeries("X", "X", (2000, 2001), (100.0, 0.0))


def test_parametric_eligibility_threshold():
    assert not make_ams(19).parametric_eligible
    assert make_ams(20).parametric_eligible


# --------------------------------------------------- statistical correctness

def test_recovers_known_aep_long_record():
    """60-yr Gumbel record: pipeline must bracket the true AEP in its 90% CI
    and land the central estimate within a factor ~2 (log10 error < 0.35)."""
    true_dist = stats.gumbel_r(loc=300.0, scale=90.0)
    threshold = float(true_dist.isf(0.02))  # true AEP exactly 2%
    ams = make_ams(60, seed=7)
    blended = analyse_gauge(ams, threshold)
    assert blended.aep_ci_lo <= 0.02 <= blended.aep_ci_hi
    assert abs(np.log10(blended.aep_central) - np.log10(0.02)) < 0.35


def test_ci_narrows_with_record_length():
    true_dist = stats.gumbel_r(loc=300.0, scale=90.0)
    threshold = float(true_dist.isf(0.02))
    sig_short = analyse_gauge(make_ams(25, seed=3), threshold).log10_sigma
    sig_long = analyse_gauge(make_ams(80, seed=3), threshold).log10_sigma
    assert sig_long < sig_short


def test_blend_weights_sum_to_one_and_prefer_precision():
    ams = make_ams(45, seed=11)
    thr = float(np.percentile(ams.flows_array(), 90))
    g = bootstrap_gev_aep(ams.flows_array(), thr)
    l = bootstrap_lp3_aep(ams.flows_array(), thr)
    b = blend_gev_lp3(g, l)
    assert abs(b.weight_gev + b.weight_lp3 - 1.0) < 1e-9
    more_precise = "gev" if g.log10_sigma < l.log10_sigma else "lp3"
    heavier = "gev" if b.weight_gev > b.weight_lp3 else "lp3"
    assert more_precise == heavier


def test_blend_ci_contains_central():
    ams = make_ams(40, seed=5)
    thr = float(np.percentile(ams.flows_array(), 85))
    b = analyse_gauge(ams, thr)
    assert b.aep_ci_lo <= b.aep_central <= b.aep_ci_hi


def test_determinism_same_data_same_report():
    ams = make_ams(35, seed=9)
    thr = 450.0
    a = analyse_gauge(ams, thr)
    b = analyse_gauge(ams, thr)
    assert a.aep_central == b.aep_central and a.aep_ci_lo == b.aep_ci_lo


# ------------------------------------------------------------ fallback path

def test_short_record_routes_to_weibull_grade_d():
    ams = make_ams(12, seed=2)
    thr = float(np.percentile(ams.flows_array(), 75))
    est = estimate_gauge(ams, thr)
    assert est.weibull_only and est.grade == "D"
    assert any("20-year minimum" in s for s in est.explanation)


def test_weibull_never_returns_zero():
    flows = np.array([100.0] * 15)
    assert weibull_aep(flows, 1e9) == pytest.approx(1 / 16)


# ------------------------------------------------------- confidence & grade

def test_grade_thresholds():
    assert grade_from_sigma(0.10) == "A"
    assert grade_from_sigma(0.20) == "B"
    assert grade_from_sigma(0.40) == "C"
    assert grade_from_sigma(0.60) == "D"


def test_ca_conflict_widens_and_agree_narrows():
    ams = make_ams(50, seed=13)
    thr = float(np.percentile(ams.flows_array(), 88))
    agree = estimate_gauge(ams, thr, ca_status=CAPolygonStatus.PRESENT_AGREES)
    conflict = estimate_gauge(ams, thr, ca_status=CAPolygonStatus.PRESENT_CONFLICTS)
    absent = estimate_gauge(ams, thr, ca_status=CAPolygonStatus.ABSENT)
    assert agree.confidence.sigma_adjusted < absent.confidence.sigma_adjusted
    assert conflict.confidence.sigma_adjusted > absent.confidence.sigma_adjusted
    assert conflict.confidence.mandatory_caveats  # disagreement caveat present


def test_absent_polygon_produces_unmapped_caveat():
    ams = make_ams(40, seed=17)
    est = estimate_gauge(ams, 500.0, ca_status=CAPolygonStatus.ABSENT)
    assert any("data gap" in c for c in est.confidence.mandatory_caveats)


# ------------------------------------------------------------- multi-gauge

def test_multigauge_headline_exceeds_max_single_and_grade_is_worst():
    a = make_ams(50, seed=21, station="02ED101", name="Black River")
    b = make_ams(14, seed=22, station="02EC002", name="Head River")  # -> grade D
    thr_a = float(np.percentile(a.flows_array(), 90))
    thr_b = float(np.percentile(b.flows_array(), 90))
    est = estimate_property_flood_risk([(a, thr_a), (b, thr_b)])
    assert est.aep_central >= max(g.aep_central for g in est.per_gauge.values())
    assert est.grade == "D"
    assert set(est.gauges_used) == {"02ED101", "02EC002"}
    assert any("separate watercourses" in s for s in est.explanation_chain)


def test_access_road_reported_separately():
    a = make_ams(45, seed=23)
    est = estimate_property_flood_risk(
        [(a, float(np.percentile(a.flows_array(), 90)))], access_road_aep=0.1
    )
    assert est.access_road_aep == 0.1
    assert any("access road" in s for s in est.explanation_chain)


# ----------------------------------------------------------- language rules

def test_language_rules_no_forbidden_phrases():
    a = make_ams(45, seed=29)
    est = estimate_property_flood_risk([(a, 400.0)])
    text = " ".join(est.explanation_chain + est.mandatory_caveats).lower()
    assert "100-year flood" not in text
    assert "insufficient data" not in text
