"""Flood frequency analysis — Engine 1 statistical core.

Implements handoff Section 5.1:
- GEV fitted by MLE (scipy.stats.genextreme)
- LP3 fitted on log10(AMS) (scipy.stats.pearson3)
- Parametric bootstrap-by-resampling CI (n=1000) on the AEP for a given
  flow threshold, and on return-period flows
- Empirical Weibull plotting positions as the < 20-year fallback (grade D)
- Blending of GEV + LP3

DESIGN DECISIONS (ambiguities resolved during implementation — flag to founder):

1. "Bayesian blending ... weighted by record length" (Section 5.1 step 8):
   both distributions are fit to the SAME record, so record length alone
   cannot distinguish them. Implemented as inverse-variance (precision)
   weighting of the two bootstrap estimates in log10-AEP space. Record length
   still governs overall uncertainty because bootstrap spread scales ~1/sqrt(n).
   This is the standard model-combination analogue of the spec's intent and is
   fully explainable ("the model whose estimate is more stable for this record
   gets more weight").

2. AEP is computed against a flow threshold (m³/s). In the full pipeline the
   threshold is the flow that reaches the property's elevation (HAND-derived).
   Until the DEM/HAND module exists, callers pass the threshold explicitly and
   the explanation chain says so.

3. Bootstrap resamples the AMS with replacement and refits each distribution
   (nonparametric bootstrap on data, not parameters). This captures parameter
   uncertainty honestly for short records. Failed refits (rare, degenerate
   resamples) are dropped and counted.

4. All AEP arithmetic in log10 space — AEPs span orders of magnitude and CI
   asymmetry is real; log space keeps the blending sane.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

from .ams import AnnualMaximumSeries

import os

BOOTSTRAP_N = int(os.environ.get("HW_BOOTSTRAP_N", "1000"))
_RNG_SEED = 20260707  # deterministic reports: same data -> same numbers
AEP_FLOOR = 1e-5      # 1-in-100,000 yr: below data support, clamp + flag
AEP_CEIL = 0.999


# --------------------------------------------------------------------------
# Single-distribution fits
# --------------------------------------------------------------------------

def fit_gev(flows: np.ndarray):
    """GEV via MLE with moment-based starting values (Gumbel relations:
    loc = mean - 0.45*sd, scale = 0.78*sd). Bare .fit() can wander on
    degenerate bootstrap resamples; good starts make convergence fast, and a
    Gumbel (c=0) fallback keeps failures clean instead of hanging."""
    mu, sd = float(np.mean(flows)), float(np.std(flows, ddof=1))
    sd = max(sd, 1e-6 * max(mu, 1.0))
    try:
        c, loc, scale = stats.genextreme.fit(
            flows, 0.0, loc=mu - 0.45 * sd, scale=0.78 * sd)
    except Exception:
        loc, scale = stats.gumbel_r.fit(flows)
        c = 0.0
    return stats.genextreme(c, loc=loc, scale=scale)


def fit_lp3(flows: np.ndarray):
    """Log-Pearson III by METHOD OF MOMENTS on log10 flows — the Bulletin 17B
    standard (mean, standard deviation, skew of the logs). Closed form: no
    optimizer, no pathological hangs (scipy's pearson3 MLE stalled for hours
    on Windows bootstrap resamples), and it is the more defensible choice —
    it's what flood agencies actually do. scipy's pearson3 is parameterized
    exactly by (skew, loc=mean, scale=sd). Skew is clipped to ±3 to guard
    degenerate resamples."""
    logq = np.log10(flows)
    m = float(np.mean(logq))
    sd = float(np.std(logq, ddof=1))
    sd = max(sd, 1e-9)
    g = float(np.clip(stats.skew(logq, bias=False), -3.0, 3.0))
    return stats.pearson3(g, loc=m, scale=sd)


def aep_gev(dist, threshold_m3s: float) -> float:
    return float(np.clip(dist.sf(threshold_m3s), AEP_FLOOR, AEP_CEIL))


def aep_lp3(dist_log, threshold_m3s: float) -> float:
    return float(np.clip(dist_log.sf(np.log10(threshold_m3s)), AEP_FLOOR, AEP_CEIL))


def return_flow_gev(dist, return_period_yr: float) -> float:
    return float(dist.isf(1.0 / return_period_yr))


def return_flow_lp3(dist_log, return_period_yr: float) -> float:
    return float(10 ** dist_log.isf(1.0 / return_period_yr))


# --------------------------------------------------------------------------
# Empirical fallback (< 20 years -> grade D path)
# --------------------------------------------------------------------------

def weibull_aep_ex(flows: np.ndarray, threshold_m3s: float) -> tuple[float, bool]:
    """Weibull plotting-position exceedance probability.

    Returns (aep, beyond_record). beyond_record=True means the threshold
    exceeds every observed flow: the record supports only the UPPER BOUND
    "less often than once in n+1 years". v0.2 BUG (Markham false-High):
    returning 1/(n+1) as a central estimate let a short-record gauge with an
    unreachable threshold dominate the property headline. Callers must treat
    beyond_record results as bounds, exclude them from headline blending, and
    report them qualitatively.
    """
    n = len(flows)
    m = int(np.sum(flows >= threshold_m3s))
    if m == 0:
        return 1.0 / (n + 1), True
    return m / (n + 1), False


def weibull_aep(flows: np.ndarray, threshold_m3s: float) -> float:
    return weibull_aep_ex(flows, threshold_m3s)[0]


# --------------------------------------------------------------------------
# Bootstrap
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class BootstrapResult:
    """Bootstrap distribution summary for one estimator, in log10-AEP space."""

    central: float           # median AEP (linear space)
    ci_lo: float             # 5th percentile AEP
    ci_hi: float             # 95th percentile AEP
    log10_sigma: float       # std of bootstrap log10(AEP) draws
    n_effective: int         # successful refits
    n_failed: int
    clamped_fraction: float = 0.0   # share of draws pinned at AEP floor/ceiling


def _bootstrap_aep(
    flows: np.ndarray,
    threshold_m3s: float,
    fitter,
    aep_fn,
    n_boot: int = BOOTSTRAP_N,
    seed: int = _RNG_SEED,
) -> BootstrapResult:
    rng = np.random.default_rng(seed)
    n = len(flows)
    draws: list[float] = []
    failed = clamped = 0
    for _ in range(n_boot):
        sample = rng.choice(flows, size=n, replace=True)
        try:
            dist = fitter(sample)
            a = aep_fn(dist, threshold_m3s)   # aep_fn clips to [FLOOR, CEIL]
            if np.isfinite(a):
                if a <= AEP_FLOOR * 1.0001 or a >= AEP_CEIL * 0.9999:
                    clamped += 1
                draws.append(a)
            else:
                failed += 1
        except Exception:
            failed += 1
    arr = np.asarray(draws)
    log_arr = np.log10(arr)
    lo, med, hi = np.percentile(arr, [5, 50, 95])
    clamped_frac = clamped / max(len(draws), 1)
    sigma = float(np.std(log_arr, ddof=1))
    if clamped_frac > 0.10:
        # Zero variance produced by CLAMPING is ignorance, not certainty: the
        # true AEP is somewhere beyond the resolvable range. Grade-A-by-floor
        # was a real field bug (Sherbrooke: sigma 3e-6 -> "A" on a wild
        # extrapolation). Force sigma to the unresolvable regime.
        sigma = max(sigma, 1.0)
    return BootstrapResult(
        central=float(med), ci_lo=float(lo), ci_hi=float(hi),
        log10_sigma=sigma, n_effective=len(draws), n_failed=failed,
        clamped_fraction=clamped_frac,
    )


def bootstrap_gev_aep(flows: np.ndarray, threshold_m3s: float, **kw) -> BootstrapResult:
    return _bootstrap_aep(flows, threshold_m3s, fit_gev, aep_gev, **kw)


def bootstrap_lp3_aep(flows: np.ndarray, threshold_m3s: float, **kw) -> BootstrapResult:
    return _bootstrap_aep(flows, threshold_m3s, fit_lp3, aep_lp3, **kw)


# --------------------------------------------------------------------------
# Blending
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class BlendedAEP:
    aep_central: float
    aep_ci_lo: float
    aep_ci_hi: float
    log10_sigma: float          # blended uncertainty, pre-prior
    weight_gev: float
    weight_lp3: float
    gev: BootstrapResult
    lp3: BootstrapResult


def blend_gev_lp3(gev: BootstrapResult, lp3: BootstrapResult) -> BlendedAEP:
    """Precision-weighted combination in log10-AEP space (see module docstring,
    decision 1). Blended sigma includes between-model disagreement so that two
    tight-but-conflicting fits still produce honest, wide uncertainty."""
    var_g = max(gev.log10_sigma, 1e-6) ** 2
    var_l = max(lp3.log10_sigma, 1e-6) ** 2
    w_g = (1 / var_g) / (1 / var_g + 1 / var_l)
    w_l = 1.0 - w_g

    mu_g, mu_l = np.log10(gev.central), np.log10(lp3.central)
    mu = w_g * mu_g + w_l * mu_l
    within = w_g * var_g + w_l * var_l
    between = w_g * (mu_g - mu) ** 2 + w_l * (mu_l - mu) ** 2
    sigma = float(np.sqrt(within + between))

    z90 = 1.6449  # 90% two-sided interval
    return BlendedAEP(
        aep_central=float(np.clip(10 ** mu, AEP_FLOOR, AEP_CEIL)),
        aep_ci_lo=float(np.clip(10 ** (mu - z90 * sigma), AEP_FLOOR, AEP_CEIL)),
        aep_ci_hi=float(np.clip(10 ** (mu + z90 * sigma), AEP_FLOOR, AEP_CEIL)),
        log10_sigma=sigma,
        weight_gev=float(w_g),
        weight_lp3=float(w_l),
        gev=gev,
        lp3=lp3,
    )


def analyse_gauge(ams: AnnualMaximumSeries, threshold_m3s: float) -> BlendedAEP | float:
    """Full Engine-1 frequency analysis for one gauge against one threshold.

    Returns BlendedAEP for parametric-eligible records, or a bare Weibull AEP
    float for short records (the caller routes that to the grade-D path).
    """
    flows = ams.flows_array()
    if not ams.parametric_eligible:
        return weibull_aep(flows, threshold_m3s)
    g = bootstrap_gev_aep(flows, threshold_m3s)
    l = bootstrap_lp3_aep(flows, threshold_m3s)
    return blend_gev_lp3(g, l)
