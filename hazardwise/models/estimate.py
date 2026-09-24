"""FloodRiskEstimate assembly — the mandatory output type (Section 5.1) plus
the plain-language explanation chain (Section 15: explainability IS the product).

Multi-gauge reality (Section 3.3): a property may sit downstream of several
watercourses. `estimate_property_flood_risk` takes ALL contributing gauges,
produces a per-gauge estimate, and reports each watercourse separately. The
headline AEP is the probability that AT LEAST ONE watercourse floods the
property in a year (assuming independence between watercourses — flagged as a
simplification; correlated basin-wide storms make this a mild underestimate of
joint risk, revisit with copulas in Phase 2).

Language rules enforced here (Section 7):
- Never "100-year flood": AEP phrased as "X% annual chance" / "roughly once
  every N years".
- Never "insufficient data": say what IS known.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from .ams import AnnualMaximumSeries
from .confidence import (
    CAPolygonStatus,
    ConfidenceAssessment,
    assess_confidence,
    grade_from_sigma,
)
from .frequency import BlendedAEP, analyse_gauge, weibull_aep_ex


@dataclass
class FloodRiskEstimate:
    """Mandatory output type from handoff Section 5.1."""

    aep_central: float
    aep_ci_lo: float
    aep_ci_hi: float
    grade: Literal["A", "B", "C", "D"]
    data_sources: list[str]
    explanation_chain: list[str]
    gauges_used: list[str]
    access_road_aep: float | None = None
    mandatory_caveats: list[str] = field(default_factory=list)
    per_gauge: dict[str, "GaugeEstimate"] = field(default_factory=dict)


@dataclass
class GaugeEstimate:
    station_id: str
    station_name: str
    aep_central: float
    aep_ci_lo: float
    aep_ci_hi: float
    grade: Literal["A", "B", "C", "D"]
    weibull_only: bool
    beyond_record: bool
    unresolvable: bool
    confidence: ConfidenceAssessment
    explanation: list[str]


def _once_every(aep: float) -> str:
    if aep <= 0:
        return "effectively never in the record"
    yrs = 1.0 / aep
    if yrs >= 2:
        return f"roughly once every {yrs:.0f} years"
    return "in most years"


def estimate_gauge(
    ams: AnnualMaximumSeries,
    threshold_m3s: float,
    ca_status: CAPolygonStatus = CAPolygonStatus.ABSENT,
    ca_vintage_year: int | None = None,
    threshold_provenance: str = "provisional flow threshold supplied by caller "
    "(HAND-derived property elevation threshold pending DEM module)",
) -> GaugeEstimate:
    result = analyse_gauge(ams, threshold_m3s)
    chain: list[str] = [
        f"Gauge {ams.station_id} on the {ams.station_name} — {ams.n_years} years of "
        f"annual peak flow data ({ams.span()}) from Environment Canada's HYDAT database.",
        f"Flows of {threshold_m3s:,.0f} m³/s or more are the level relevant to this "
        f"property ({threshold_provenance}).",
    ]

    if isinstance(result, float):  # Weibull-only path
        _, beyond = weibull_aep_ex(ams.flows_array(), threshold_m3s)
        conf = assess_confidence(
            sigma_raw=float("nan"),
            n_years=ams.n_years,
            fraction_estimated=ams.fraction_estimated,
            ca_status=ca_status,
            ca_vintage_year=ca_vintage_year,
            weibull_only=True,
        )
        if beyond:
            chain.append(
                f"Flows at this level have never been observed in the "
                f"{ams.n_years}-year record at this gauge. The record alone can "
                f"only say such an event happens less often than about once in "
                f"{ams.n_years + 1} years; it cannot give a precise probability."
            )
            conf.mandatory_caveats.append(
                f"Gauge {ams.station_id}: the flow level relevant to this "
                "property lies beyond everything observed in a short record; "
                "this watercourse is reported qualitatively, not numerically."
            )
        else:
            chain.append(
                f"Based on observed frequencies alone, flows at this level occur "
                f"{_once_every(result)} ({result:.1%} annual chance)."
            )
        chain.extend(conf.reasons)
        return GaugeEstimate(
            ams.station_id, ams.station_name,
            aep_central=result, aep_ci_lo=0.0 if beyond else result,
            aep_ci_hi=result,
            grade="D", weibull_only=True, beyond_record=beyond,
            unresolvable=beyond, confidence=conf, explanation=chain,
        )

    blended: BlendedAEP = result
    unresolvable = (blended.gev.clamped_fraction > 0.10 and
                    blended.lp3.clamped_fraction > 0.10)
    conf = assess_confidence(
        sigma_raw=blended.log10_sigma,
        n_years=ams.n_years,
        fraction_estimated=ams.fraction_estimated,
        ca_status=ca_status,
        ca_vintage_year=ca_vintage_year,
    )
    # Re-derive the CI from the ADJUSTED sigma so priors/penalties show up in
    # the numbers, not just the grade.
    z90 = 1.6449
    mu = np.log10(blended.aep_central)
    ci_lo = float(10 ** (mu - z90 * conf.sigma_adjusted))
    ci_hi = float(min(10 ** (mu + z90 * conf.sigma_adjusted), 0.999))

    chain.append(
        f"Two independent statistical models (GEV and Log-Pearson III — the standards "
        f"used by flood agencies in Canada and the US) were fitted to this record and "
        f"combined, weighted {blended.weight_gev:.0%}/{blended.weight_lp3:.0%} by how "
        f"stable each model's answer is for this gauge."
    )
    chain.append(
        f"Result: a {blended.aep_central:.1%} chance in any given year — "
        f"{_once_every(blended.aep_central)}."
    )
    chain.extend(conf.reasons)

    if threshold_m3s > 2.0 * float(np.max(ams.flows_array())):
        conf.mandatory_caveats.append(
            f"Gauge {ams.station_id}: the relevant flow level is more than twice "
            "the largest flood ever observed there; the statistical estimate is "
            "an extrapolation and its confidence interval reflects that."
        )
    return GaugeEstimate(
        ams.station_id, ams.station_name,
        aep_central=blended.aep_central, aep_ci_lo=ci_lo, aep_ci_hi=ci_hi,
        grade=conf.grade, weibull_only=False, beyond_record=False,
        unresolvable=unresolvable, confidence=conf, explanation=chain,
    )


def estimate_property_flood_risk(
    gauge_inputs: list[tuple[AnnualMaximumSeries, float]],
    ca_status: CAPolygonStatus = CAPolygonStatus.ABSENT,
    ca_vintage_year: int | None = None,
    access_road_aep: float | None = None,
) -> FloodRiskEstimate:
    """Property-level estimate from ALL contributing gauges (Section 3.3).

    gauge_inputs: (AMS, flow threshold relevant to this property) per gauge.
    """
    if not gauge_inputs:
        raise ValueError("At least one contributing gauge is required")

    per_gauge = {
        ams.station_id: estimate_gauge(ams, thr, ca_status, ca_vintage_year)
        for ams, thr in gauge_inputs
    }

    # Headline: P(at least one watercourse floods) under independence.
    # v0.3: gauges whose threshold lies beyond a short record contribute only
    # an upper bound, never a central number (Markham false-High fix).
    quant = [g for g in per_gauge.values()
             if not g.beyond_record and not g.unresolvable]
    bound = [g for g in per_gauge.values()
             if g.beyond_record or g.unresolvable]
    combine = lambda ps: 1.0 - float(np.prod([1.0 - p for p in ps]))
    if quant:
        aep_c = combine([g.aep_central for g in quant])
        aep_lo = combine([g.aep_ci_lo for g in quant])
        aep_hi = min(combine([g.aep_ci_hi for g in quant]), 0.999)
        grade = max((g.grade for g in quant), key="ABCD".index)
    else:  # every gauge is beyond-record: headline is an explicit upper bound
        aep_c = min(g.aep_central for g in bound)
        aep_lo, aep_hi = 0.0, aep_c
        grade = "D"

    chain: list[str] = []
    if not quant:
        chain.append(
            "The flow level relevant to this property lies beyond what any "
            "contributing gauge's record can resolve; the headline is reported "
            "as an upper bound rather than an estimate.")
    if len(per_gauge) > 1:
        names = ", ".join(g.station_name for g in per_gauge.values())
        chain.append(
            f"This property is affected by {len(per_gauge)} separate watercourses "
            f"({names}); each is assessed below, and the headline number is the chance "
            f"that any one of them floods the property in a given year."
        )
    for g in per_gauge.values():
        chain.extend(g.explanation)
        chain.append("—")
    if bound and quant:
        names = ", ".join(f"{g.station_name} ({g.station_id})" for g in bound)
        chain.append(
            f"Excluded from the headline number and confidence grade: {names} — "
            "the relevant flow level lies beyond what that gauge's record can "
            "resolve, so it is described qualitatively above rather than "
            "counted as a probability."
        )
    if access_road_aep is not None:
        chain.append(
            f"Separately from the property itself, the access road has a "
            f"{access_road_aep:.1%} annual chance of flooding "
            f"({_once_every(access_road_aep)}) — a lower-lying road can cut off access "
            f"even when the property stays dry."
        )

    caveats: list[str] = []
    for g in per_gauge.values():
        for c in g.confidence.mandatory_caveats:
            if c not in caveats:
                caveats.append(c)

    sources = ["Environment and Climate Change Canada — Water Survey of Canada HYDAT"]
    if ca_status is not CAPolygonStatus.ABSENT:
        sources.append("Ontario Conservation Authority flood plain mapping")

    return FloodRiskEstimate(
        aep_central=aep_c, aep_ci_lo=aep_lo, aep_ci_hi=aep_hi,
        grade=grade, data_sources=sources, explanation_chain=chain,
        gauges_used=list(per_gauge.keys()), access_road_aep=access_road_aep,
        mandatory_caveats=caveats, per_gauge=per_gauge,
    )
