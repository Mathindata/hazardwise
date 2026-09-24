"""Annual Maximum Series (AMS) container.

Encodes the data-quality rules from the HazardWise handoff doc:
- NULL years are excluded upstream (HYDAT extraction), but we validate here too.
- Years flagged as "estimated" in HYDAT are retained but tracked, because they
  inflate the confidence engine's sigma (Section 5.4).
- Records shorter than MIN_YEARS_PARAMETRIC fall back to empirical Weibull
  plotting positions and are capped at grade D (Section 5.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

MIN_YEARS_PARAMETRIC = 20


@dataclass(frozen=True)
class AnnualMaximumSeries:
    """Annual peak flows for one WSC gauge station."""

    station_id: str            # e.g. "02ED101"
    station_name: str          # e.g. "Black River near Washago"
    years: tuple[int, ...]
    flows_m3s: tuple[float, ...]
    estimated_flags: tuple[bool, ...] = field(default=())

    def __post_init__(self) -> None:
        if len(self.years) != len(self.flows_m3s):
            raise ValueError("years and flows must be the same length")
        flags = self.estimated_flags or tuple(False for _ in self.years)
        object.__setattr__(self, "estimated_flags", flags)
        if len(flags) != len(self.years):
            raise ValueError("estimated_flags length mismatch")
        arr = np.asarray(self.flows_m3s, dtype=float)
        if np.any(~np.isfinite(arr)):
            raise ValueError(
                f"{self.station_id}: AMS contains NULL/non-finite flows; "
                "exclude NULL years at extraction time (handoff Section 5.1 step 2)"
            )
        if np.any(arr <= 0):
            raise ValueError(
                f"{self.station_id}: non-positive peak flow found; LP3 requires "
                "log-transformable data. Investigate before fitting."
            )

    @property
    def n_years(self) -> int:
        return len(self.years)

    @property
    def n_estimated(self) -> int:
        return int(sum(self.estimated_flags))

    @property
    def fraction_estimated(self) -> float:
        return self.n_estimated / self.n_years if self.n_years else 0.0

    @property
    def parametric_eligible(self) -> bool:
        """True if the record is long enough for GEV/LP3 (>= 20 years)."""
        return self.n_years >= MIN_YEARS_PARAMETRIC

    def flows_array(self) -> np.ndarray:
        return np.asarray(self.flows_m3s, dtype=float)

    def span(self) -> str:
        return f"{min(self.years)}–{max(self.years)}"
