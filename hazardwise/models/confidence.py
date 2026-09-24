"""Bayesian confidence engine — handoff Section 5.4.

Takes the blended statistical sigma and adjusts it for data-quality context,
then maps to the A–D grade. Every adjustment appends a plain-language reason
so the explanation chain (the product) can cite it.

Grade thresholds (Section 5.4, sigma in log10-AEP space):
    A: sigma < 0.15   B: < 0.30   C: < 0.55   D: >= 0.55

DESIGN DECISIONS:
- "Estimated years -> inflated sigma": multiplicative penalty
  (1 + 0.5 * fraction_estimated). At 20% estimated years this widens sigma
  by 10% — noticeable, not punitive. Tunable constant, surfaced in reasons.
- "Official CA polygon acts as a strong prior -> narrows CI": implemented as a
  precision-multiplier only when the polygon AGREES with the statistical
  estimate's risk direction. A CA polygon that contradicts the gauge analysis
  must widen uncertainty, not narrow it — narrowing on contradiction would be
  exactly the false confidence Section 3.5 forbids. Agreement multiplier 0.85
  on sigma; contradiction multiplier 1.25 plus a mandatory caveat.
- Grade-D floor for Weibull-only (short record) results regardless of sigma.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

Grade = Literal["A", "B", "C", "D"]

GRADE_THRESHOLDS = ((0.15, "A"), (0.30, "B"), (0.55, "C"))

ESTIMATED_YEAR_PENALTY = 0.5      # sigma *= 1 + penalty * fraction_estimated
CA_AGREE_FACTOR = 0.85            # official polygon corroborates -> narrow
CA_CONFLICT_FACTOR = 1.25         # official polygon contradicts -> widen
SHORT_RECORD_SOFT_FLOOR = 30      # < 30 yrs: mild inflation even if eligible
SHORT_RECORD_PENALTY = 0.10


class CAPolygonStatus(Enum):
    ABSENT = "absent"              # no CA mapping here — unmapped != safe
    PRESENT_AGREES = "agrees"      # property inside polygon & stats say risk (or both low)
    PRESENT_CONFLICTS = "conflicts"


@dataclass
class ConfidenceAssessment:
    sigma_raw: float
    sigma_adjusted: float
    grade: Grade
    reasons: list[str] = field(default_factory=list)
    mandatory_caveats: list[str] = field(default_factory=list)


def grade_from_sigma(sigma: float) -> Grade:
    for cutoff, g in GRADE_THRESHOLDS:
        if sigma < cutoff:
            return g  # type: ignore[return-value]
    return "D"


def assess_confidence(
    sigma_raw: float,
    n_years: int,
    fraction_estimated: float,
    ca_status: CAPolygonStatus,
    ca_vintage_year: int | None = None,
    current_year: int = 2026,
    weibull_only: bool = False,
) -> ConfidenceAssessment:
    reasons: list[str] = []
    caveats: list[str] = []
    sigma = sigma_raw

    if weibull_only:
        reasons.append(
            f"Only {n_years} years of gauge data — below the 20-year minimum for "
            "statistical modelling, so this estimate uses observed frequencies only."
        )
        caveats.append(
            "Limited data: this estimate is based on a short observation record "
            "and cannot reliably describe rare events. Treat it as indicative only."
        )
        return ConfidenceAssessment(sigma_raw, float("inf"), "D", reasons, caveats)

    if fraction_estimated > 0:
        factor = 1 + ESTIMATED_YEAR_PENALTY * fraction_estimated
        sigma *= factor
        reasons.append(
            f"{fraction_estimated:.0%} of the gauge record is flagged as estimated "
            "rather than directly measured, which widens the uncertainty."
        )

    if n_years < SHORT_RECORD_SOFT_FLOOR:
        sigma *= 1 + SHORT_RECORD_PENALTY
        reasons.append(
            f"The gauge record is {n_years} years long — usable, but shorter records "
            "make rare-event estimates less certain."
        )
    else:
        reasons.append(f"The gauge record spans {n_years} years — a solid basis for this analysis.")

    if ca_status is CAPolygonStatus.PRESENT_AGREES:
        sigma *= CA_AGREE_FACTOR
        vintage = f" (mapped {ca_vintage_year})" if ca_vintage_year else ""
        reasons.append(
            f"Official Conservation Authority flood plain mapping{vintage} is consistent "
            "with the gauge-based estimate, which strengthens confidence."
        )
        if ca_vintage_year and current_year - ca_vintage_year > 15:
            caveats.append(
                f"The flood plain mapping here is {current_year - ca_vintage_year} years old; "
                "land use and climate have changed since it was drawn."
            )
    elif ca_status is CAPolygonStatus.PRESENT_CONFLICTS:
        sigma *= CA_CONFLICT_FACTOR
        reasons.append(
            "Official flood plain mapping and the gauge-based estimate disagree here; "
            "we widened the uncertainty rather than picking a side."
        )
        caveats.append(
            "Two credible sources disagree about this location. We recommend the "
            "conservative interpretation until a site assessment resolves it."
        )
    else:  # ABSENT
        reasons.append(
            "No official flood plain mapping exists for this location. Unmapped does "
            "not mean safe — this estimate relies on gauge data alone."
        )
        caveats.append(
            "This area has no official flood plain mapping. The absence of a mapped "
            "flood zone is a data gap, not evidence of low risk."
        )

    grade = grade_from_sigma(sigma)
    if grade in ("C", "D"):
        caveats.append(
            "This estimate carries meaningful uncertainty. What is known is shown; "
            "where the data is thin, we say so rather than guess."
        )
    return ConfidenceAssessment(sigma_raw, sigma, grade, reasons, caveats)
