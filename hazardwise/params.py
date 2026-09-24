"""HazardWise tunable parameters — single source of truth (v0.11).

Every screening constant lives here. Calibration output is applied
automatically: if calibration/best_params.json exists (or HW_PARAMS points to
one), its fitted values override the defaults below at import time, and every
report generated afterwards uses them. Humans and agents edit ONE file.

Mapping from sweep.py GRID keys -> parameters here:
  b           -> STAGE_EXPONENT      (threshold uses 1/b internally)
  k_bankfull  -> K_BANKFULL
  hand_bump_m -> HAND_BUMP_M
  high_aep    -> RISK_HIGH_AEP       (RISK_MED_AEP = high_aep / 4)
  rb_frac     -> REACHBACK_MEDIUM_FRACTION
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# ---- risk classification -------------------------------------------------
RISK_HIGH_AEP = 0.02          # AEP >= this -> High
RISK_MED_AEP = 0.005          # AEP >= this -> Medium
HAND_BUMP_M = 1.5             # standing below this bumps class one step
DEPRESSION_BUMP_M = 0.5       # ponding depth that bumps class one step

# ---- stage/flow rating (screening) ----------------------------------------
STAGE_EXPONENT = 0.40         # stage ~ Q^b; calibration target
K_BANKFULL = 0.27             # D_b = max(1, K * DA^BANKFULL_DA_EXP)
BANKFULL_DA_EXP = 0.30

# ---- reach-back ------------------------------------------------------------
REACHBACK_MEDIUM_FRACTION = 0.6

# ---- gauge selection -------------------------------------------------------
MAINSTEM_MAX_KM = 150.0
GAUGE_SCALE_FRACTION = 0.05
GAUGE_SCALE_MIN_KM2 = 1.0
GAUGE_SCALE_MAX_KM2 = 500.0
STALE_RECORD_YEARS = 25
MAX_GAUGES = 2
FALLBACK_RADIUS_KM = 60.0

# ---- display ---------------------------------------------------------------
AEP_DISPLAY_CAP = 0.25
AEP_DISPLAY_FLOOR = 1e-4

CALIBRATION_SOURCE = "defaults (uncalibrated)"

_KEYMAP = {
    "b": "STAGE_EXPONENT",
    "k_bankfull": "K_BANKFULL",
    "hand_bump_m": "HAND_BUMP_M",
    "high_aep": "RISK_HIGH_AEP",
    "rb_frac": "REACHBACK_MEDIUM_FRACTION",
}


def apply_calibration(path: str | Path | None = None) -> str:
    """Apply fitted constants from best_params.json. Returns a provenance
    string (stamped into reports via CALIBRATION_SOURCE)."""
    global CALIBRATION_SOURCE, RISK_MED_AEP
    g = globals()
    p = Path(path or os.environ.get("HW_PARAMS",
                                    "calibration/best_params.json"))
    if not p.exists():
        return CALIBRATION_SOURCE
    try:
        best = json.loads(p.read_text(encoding="utf-8")).get("best", {})
        applied = []
        for k, target in _KEYMAP.items():
            if k in best:
                g[target] = float(best[k])
                applied.append(f"{target}={best[k]}")
        if "high_aep" in best:
            RISK_MED_AEP = float(best["high_aep"]) / 4.0
        if applied:
            CALIBRATION_SOURCE = f"calibrated ({p}): " + ", ".join(applied)
    except Exception as e:
        CALIBRATION_SOURCE = f"defaults (failed to read {p}: {e})"
    return CALIBRATION_SOURCE


apply_calibration()

# v0.20 trust guard: reject the stage transfer when the gauge's drainage
# area exceeds the largest channel actually resolved in the terrain window
# by more than this factor -- a county-centroid on upland farmland must not
# inherit the Athabasca's statistics (the Lac Ste. Anne failure).
GAUGE_SCALE_REJECT_RATIO = 30.0



# v0.13: satellite/map constants (SAT_ prefix; overridable like the rest)
from .satellite.params_sat import *  # noqa: F401,F403
