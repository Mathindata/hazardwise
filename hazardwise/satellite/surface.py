"""Fusion: Beta-Binomial posterior of per-event wet probability, prior injected
from the existing model (AEP curve + HAND), annualized back to AEP. The gauge-case
table (GAUGE_OK/FAR/REGULATED/SHORT/UNGAUGED) is implemented purely as different
prior weights and stack admissibility — one code path."""
from dataclasses import dataclass
import numpy as np
from scipy import stats
from . import params_sat as P
from .frequency import per_event_to_annual, annual_to_per_event

@dataclass
class FusionResult:
    aep: np.ndarray        # posterior-mean annual exceedance probability
    aep_lo: np.ndarray     # 5th pct
    aep_hi: np.ndarray     # 95th pct
    mode: str              # "probability" | "susceptibility"

PRIOR_WEIGHTS = {          # effective prior sample size (events) by gauge case
    "GAUGE_OK":        P.SAT_PRIOR_WEIGHT_EVENTS,
    "GAUGE_SHORT":     P.SAT_PRIOR_WEIGHT_EVENTS * 0.5,
    "GAUGE_REGULATED": P.SAT_PRIOR_WEIGHT_EVENTS * 0.5,
    "GAUGE_FAR":       P.SAT_PRIOR_WEIGHT_EVENTS * 0.1,
    "UNGAUGED":        0.0,
    "SATELLITE_ONLY":  8.0,
}

def fuse(prior_aep: np.ndarray, k: np.ndarray, n_eff: float, lam: float,
         gauge_case: str = "GAUGE_OK") -> FusionResult:
    w = PRIOR_WEIGHTS.get(gauge_case, P.SAT_PRIOR_WEIGHT_EVENTS)
    if gauge_case == "UNGAUGED" and n_eff <= 0:
        # no model, no observations -> susceptibility mode handled by caller/render
        z = np.zeros_like(np.asarray(prior_aep, float))
        return FusionResult(z, z, z, "susceptibility")
    p0 = annual_to_per_event(prior_aep, lam)
    a0 = np.maximum(p0 * w, 1e-3); b0 = np.maximum((1 - p0) * w, 1e-3)
    a = a0 + k; b = b0 + np.maximum(n_eff - k, 0.0)
    post_mean = a / (a + b)
    lo = stats.beta.ppf(0.05, a, b); hi = stats.beta.ppf(0.95, a, b)
    return FusionResult(per_event_to_annual(post_mean, lam),
                        per_event_to_annual(lo, lam),
                        per_event_to_annual(hi, lam), "probability")
