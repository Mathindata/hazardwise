"""Per-pixel empirical exceedance from the extent stack + event catalog.
'Not wet in the one scene we had' is a noisy negative: n is detection-corrected
and intervals are Wilson, never k/n reported bare."""
import numpy as np

def wilson_interval(k, n, z: float = 1.96):
    k = np.asarray(k, float); n = np.maximum(np.asarray(n, float), 1e-9)
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return np.clip(c - h, 0, 1), np.clip(c + h, 0, 1)

def effective_n(obs_table) -> float:
    """Detection-corrected effective observed-event count: sum of capture_p over
    events with at least one scene, plus partial credit is deliberately NOT given
    to unobserved events (they contribute neither wet nor dry evidence)."""
    t = obs_table[obs_table["observed"]]
    return float(t["capture_p"].clip(upper=1.0).sum())

def per_event_to_annual(p_event, lam: float):
    """AEP = 1 - exp(-lambda * p_wet_given_event), lambda = events/year."""
    return 1.0 - np.exp(-lam * np.asarray(p_event, float))

def annual_to_per_event(aep, lam: float):
    aep = np.clip(np.asarray(aep, float), 0.0, 0.999999)
    return np.clip(-np.log(1.0 - aep) / max(lam, 1e-9), 0.0, 1.0)
