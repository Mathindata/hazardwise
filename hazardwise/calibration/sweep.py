"""Sweep candidate constants against labeled points — instant re-scoring from
stored ingredients, no pipeline re-runs.

Constants swept (the eight textbook guesses, minus those data replaced):
  stage exponent b          (stage ~ Q^b; threshold Q* = Q2·((D_b+h)/D_b)^(1/b))
  bankfull coefficient k    (D_b = max(1, k · DA^0.30))
  HAND bump threshold       (below this, class bumps one step)
  High/Medium AEP bands
  reach-back fraction       (measured or converted rise vs HAND)

Scoring: wet points should classify High; near-miss dry points should not.
  POD  = hits / wet          FAR = false alarms / predicted-High
  CSI  = hits / (hits + misses + false alarms)   <- selection metric
Events are split into train/test folds so the chosen constants are judged on
events they never saw — the number that goes in the validation report is the
TEST skill, not the training skill.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
from scipy import stats

# v0.12: v0.11 fit pinned b / k_bankfull / hand_bump at grid EDGES — the
# optimum lay outside the box. Grid extended on every pinned side.
GRID = {
    "b": [0.30, 0.40, 0.50, 0.60, 0.70],
    "k_bankfull": [0.20, 0.27, 0.35, 0.45, 0.55],
    "hand_bump_m": [0.5, 1.0, 1.5, 2.5],
    "high_aep": [0.01, 0.02, 0.04, 0.06],
    "rb_frac": [0.4, 0.6, 0.8, 1.0],
}

IN_WATER_HAND_M = 0.25   # wet sample points effectively in the channel


def filter_label_noise(points: list[dict]) -> tuple[list[dict], int]:
    """Drop WET points that sit in the water itself (HAND ~ 0 to the matched
    channel): satellite flood extents include the river's permanent surface,
    and in-channel points are trivially 'flooded' — they inflate POD without
    testing the model. Dry points are kept as-is but remember: 'did not flood
    in this event' is a NOISY negative — genuinely risky floodplain can stay
    dry in any one event, so FAR against these labels is an UPPER BOUND on
    true false-alarm rate."""
    kept, dropped = [], 0
    for p in points:
        if p["flooded"] and p.get("gauges") and \
                min(g["hand_m"] for g in p["gauges"]) < IN_WATER_HAND_M:
            dropped += 1
        else:
            kept.append(p)
    return kept, dropped


def _gauge_aep(g: dict, b: float, k: float) -> float:
    d_b = max(1.0, k * g["da_km2"] ** 0.30)
    thr = g["q2"] * ((d_b + g["hand_m"]) / d_b) ** (1.0 / b)
    c, loc, scale = g["gev"]
    skew, m, sd = g["lp3"]
    a1 = float(np.clip(stats.genextreme(c, loc=loc, scale=scale).sf(thr),
                       1e-6, 0.999))
    a2 = float(np.clip(stats.pearson3(skew, loc=m, scale=sd)
                       .sf(math.log10(max(thr, 1e-9))), 1e-6, 0.999))
    return 10 ** ((math.log10(a1) + math.log10(a2)) / 2)


def classify_point(p: dict, b, k_bankfull, hand_bump_m, high_aep, rb_frac,
                   **_) -> str:
    gauges = [g for g in p["gauges"]]
    quant = [g for g in gauges if not g["regulated"]] or gauges
    if not quant:
        return "Low"
    aep = 1.0 - float(np.prod([1 - _gauge_aep(g, b, k_bankfull)
                               for g in quant]))
    med_aep = high_aep / 4.0
    cls = "High" if aep >= high_aep else ("Medium" if aep >= med_aep else "Low")
    hand = min(g["hand_m"] for g in gauges)
    fill = max(g["fill_depth_m"] for g in gauges)
    if (hand < hand_bump_m or fill > 0.5) and cls != "High":
        cls = {"Low": "Medium", "Medium": "High"}[cls]
    for g in quant:
        d_b = max(1.0, k_bankfull * g["da_km2"] ** 0.30)
        rise = g.get("measured_rise_m")
        if rise is None:
            rise = d_b * ((g["qmax"] / max(g["q2"], 1e-9)) ** b - 1.0)
        h = g["hand_m"]
        if h > 0 and rise >= rb_frac * h:
            return "High"
    return cls


def score(points: list[dict], **params) -> dict:
    hits = misses = fas = dry_ok = 0
    for p in points:
        if p.get("error") or not p.get("gauges"):
            continue
        pred_high = classify_point(p, **params) == "High"
        if p["flooded"]:
            hits += pred_high
            misses += not pred_high
        else:
            fas += pred_high
            dry_ok += not pred_high
    pod = hits / max(hits + misses, 1)
    far = fas / max(hits + fas, 1)
    csi = hits / max(hits + misses + fas, 1)
    return dict(pod=pod, far=far, csi=csi, n_wet=hits + misses,
                n_dry=fas + dry_ok, **params)


def sweep(points: list[dict], grid: dict = GRID,
          test_events: set[str] | None = None):
    """Grid sweep. If test_events given, constants are CHOSEN on the other
    events and REPORTED on these. Returns (best_params, train_row, test_row,
    all_rows)."""
    points, n_dropped = filter_label_noise(points)
    if n_dropped:
        print(f"Label hygiene: dropped {n_dropped} in-channel wet points")
    events = sorted({p["event"] for p in points})
    if test_events is None:
        test_events = set(events[::3])   # every third event held out
    train = [p for p in points if p["event"] not in test_events]
    test = [p for p in points if p["event"] in test_events]
    rows = []
    keys = list(grid)
    for combo in itertools.product(*(grid[k] for k in keys)):
        params = dict(zip(keys, combo))
        rows.append(score(train, **params))
    best = max(rows, key=lambda r: (r["csi"], -r["far"]))
    bp = {k: best[k] for k in keys}
    return bp, score(train, **bp), score(test, **bp), rows
