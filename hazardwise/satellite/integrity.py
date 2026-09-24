"""Map integrity checks — append these counters to benchmark_history.csv columns.
Same philosophy as grade-vs-cap: contradictions must be zeros, not vibes."""
import numpy as np
from . import params_sat as P
from .render import _bin_index

GRADE_TO_BINS = {           # report grade -> acceptable address-pixel bins
    "High":   {3, 4},
    "Medium": {1, 2, 3},
    "Low":    {-1, 0, 1},
}

def grade_vs_map(grade: str, meta: dict, bumped: bool = False) -> int:
    """1 if the map bin at the address contradicts the report grade.
    A terrain bump (low HAND / closed depression) legitimately lifts the
    class one step above the pure-AEP bin; when bumped, the audit accepts
    the pre-bump class's bins too -- disclosed, never smuggled."""
    if meta["mode"] != "probability" or grade is None:
        return 0
    allowed = set(GRADE_TO_BINS.get(grade, set()))
    if bumped:
        allowed |= GRADE_TO_BINS.get(
            {"High": "Medium", "Medium": "Low", "Low": "Low"}[grade], set())
    return 0 if meta["address_bin"] in allowed else 1

def legend_complete(meta: dict) -> int:
    """# of missing mandatory legend blocks (mode-appropriate)."""
    need = ["permanent_water", "observed_extents", "provenance"]
    if meta["mode"] == "probability":
        need += ["bins", "uncertainty"]
    return sum(0 if meta["legend_blocks"].get(k) else 1 for k in need)

def probability_legend_in_susceptibility(meta: dict) -> int:
    """Must be impossible: AEP bins rendered without a probability surface."""
    return 1 if (meta["mode"] == "susceptibility"
                 and meta["legend_blocks"].get("bins")) else 0

def extent_vs_grade_conflicts(fusion, stack, min_events: int = 2) -> int:
    """# pixels the model puts below the lowest AEP bin that were observed wet in
    >= min_events distinct events (the Grand Forks-style silent-regression detector)."""
    if fusion.mode != "probability":
        return 0
    k = stack.wet_event_count()
    low = _bin_index(fusion.aep) < 0
    return int(np.sum(low & (k >= min_events)))

def run_all(grade, fusion, stack, meta, bumped: bool = False) -> dict:
    return {
        "map_grade_mismatch": grade_vs_map(grade, meta, bumped=bumped),
        "map_legend_missing": legend_complete(meta),
        "map_bad_susceptibility_legend": probability_legend_in_susceptibility(meta),
        "map_extent_grade_conflicts": extent_vs_grade_conflicts(fusion, stack),
    }
