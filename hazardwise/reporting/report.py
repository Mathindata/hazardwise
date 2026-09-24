"""Report generation — one folder per address: report.html + report.json + PNGs.

The HTML report is the product artifact (handoff Section 15): headline risk
class, grade badge, the full explanation chain, plots, named sources, and
mandatory caveats. JSON carries the same content machine-readably.
"""

from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import asdict
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from ..models.ams import AnnualMaximumSeries
from ..models.estimate import FloodRiskEstimate
from ..models.frequency import fit_gev, fit_lp3, return_flow_gev, return_flow_lp3

RISK_COLORS = {"High": "#c0392b", "Medium": "#e67e22", "Low": "#27ae60"}
GRADE_COLORS = {"A": "#27ae60", "B": "#2e86c1", "C": "#e67e22", "D": "#c0392b"}


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]


# ------------------------------------------------------------------- plots

def _b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def plot_ams(ams: AnnualMaximumSeries, threshold: float | None) -> tuple[str, bytes]:
    fig, ax = plt.subplots(figsize=(7.5, 3.2))
    colors = ["#c0392b" if e else "#2e86c1" for e in ams.estimated_flags]
    ax.bar(ams.years, ams.flows_m3s, color=colors, width=0.8)
    if threshold:
        ax.axhline(threshold, color="#8e44ad", ls="--", lw=1.5,
                   label=f"level relevant to property ({threshold:,.0f} m³/s)")
        ax.legend(fontsize=8)
    ax.set_ylabel("annual peak flow (m³/s)")
    ax.set_title(f"{ams.station_id} — {ams.station_name}: observed annual peaks "
                 f"({ams.span()}); red bars = estimated years", fontsize=9)
    b = _b64(fig)
    return b, base64.b64decode(b)


def plot_frequency_curve(ams: AnnualMaximumSeries, threshold: float | None) -> tuple[str, bytes]:
    flows = ams.flows_array()
    gev, lp3 = fit_gev(flows), fit_lp3(flows)
    T = np.logspace(np.log10(1.05), np.log10(500), 120)
    fig, ax = plt.subplots(figsize=(7.5, 3.4))
    ax.plot(T, [return_flow_gev(gev, t) for t in T], label="GEV fit", color="#2e86c1")
    ax.plot(T, [return_flow_lp3(lp3, t) for t in T], label="Log-Pearson III fit",
            color="#27ae60", ls="--")
    # empirical (Weibull) plotting positions
    srt = np.sort(flows)[::-1]
    n = len(srt)
    T_emp = (n + 1) / np.arange(1, n + 1)
    ax.scatter(T_emp, srt, s=14, color="#555", zorder=3, label="observed years")
    if threshold:
        ax.axhline(threshold, color="#8e44ad", ls="--", lw=1.5,
                   label="level relevant to property")
    ax.set_xscale("log")
    ax.set_xlabel("return period (years)")
    ax.set_ylabel("peak flow (m³/s)")
    ax.set_title(f"{ams.station_id}: flood frequency curve", fontsize=9)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, which="both")
    b = _b64(fig)
    return b, base64.b64decode(b)


# ------------------------------------------------------------------- report

def render_report(
    *,
    address: str,
    geocode_note: str,
    risk_class: str,
    estimate: FloodRiskEstimate,
    exposure_narrative: list[str],
    gauge_ams: dict[str, AnnualMaximumSeries],
    gauge_thresholds: dict[str, float],
    sources: list[tuple[str, str]],           # (name, url)
    hydat_vintage: str,
    out_dir: Path,
    map_html: str = "",
    map_meta: dict | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    plots_html, png_files = [], {}
    for sid, ams in gauge_ams.items():
        thr = gauge_thresholds.get(sid)
        b1, raw1 = plot_ams(ams, thr)
        b2, raw2 = plot_frequency_curve(ams, thr)
        png_files[f"{sid}_ams.png"], png_files[f"{sid}_freq.png"] = raw1, raw2
        plots_html.append(
            f"<h3>Gauge {sid} — {ams.station_name}</h3>"
            f'<img src="data:image/png;base64,{b1}" alt="AMS {sid}">'
            f'<img src="data:image/png;base64,{b2}" alt="frequency curve {sid}">'
        )
    for name, raw in png_files.items():
        (out_dir / name).write_bytes(raw)

    chain_html = "".join(
        f"<li>{line}</li>" for line in estimate.explanation_chain if line != "—"
    )
    exposure_html = "".join(f"<li>{line}</li>" for line in exposure_narrative)
    caveats_html = "".join(f"<li>{c}</li>" for c in estimate.mandatory_caveats)
    sources_html = "".join(
        f'<li>{name} — <a href="{url}">{url}</a></li>' for name, url in sources
    )
    rc, gc = RISK_COLORS.get(risk_class, "#555"), GRADE_COLORS.get(estimate.grade, "#555")

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>HazardWise flood risk report — {address}</title>
<style>
 body{{font-family:Segoe UI,system-ui,sans-serif;max-width:900px;margin:2rem auto;
      padding:0 1rem;color:#222;line-height:1.5}}
 .badge{{display:inline-block;padding:.35rem 1rem;border-radius:6px;color:#fff;
        font-weight:600;margin-right:.6rem}}
 h1{{font-size:1.4rem}} h2{{font-size:1.1rem;border-bottom:1px solid #ddd;
    padding-bottom:.2rem;margin-top:2rem}}
 img{{max-width:100%;margin:.5rem 0;border:1px solid #eee}}
 .meta{{color:#666;font-size:.9rem}}
 .caveat{{background:#fff6e6;border-left:4px solid #e67e22;padding:.6rem 1rem}}
 footer{{margin-top:3rem;font-size:.8rem;color:#888;border-top:1px solid #eee;
        padding-top:1rem}}
</style></head><body>
<h1>Flood Risk Report</h1>
<p class="meta">{address}<br>{geocode_note}<br>
Report date: {date.today().isoformat()} · HYDAT vintage: {hydat_vintage}</p>

<p>
 <span class="badge" style="background:{rc}">Flood risk: {risk_class}</span>
 <span class="badge" style="background:{gc}">Confidence grade: {estimate.grade}</span>
</p>
<p><strong>Annual chance of riverine flooding at this location:
{estimate.aep_central:.1%}</strong>
(90% confidence range {estimate.aep_ci_lo:.1%}–{estimate.aep_ci_hi:.1%})
— roughly once every {1/max(estimate.aep_central,1e-9):,.0f} years.</p>

<h2>Why this rating — the evidence chain</h2>
<ol>{chain_html}</ol>

<h2>Terrain &amp; exposure</h2>
<ul>{exposure_html}</ul>

<h2>What the river record shows</h2>
{''.join(plots_html)}

<h2>Important caveats</h2>
<div class="caveat"><ul>{caveats_html}</ul></div>

<h2>Data sources</h2>
<ul>{sources_html}</ul>

<footer>Generated by HazardWise v0.2 (screening model). This report is an
information product, not an engineering flood study, insurance assessment, or
legal advice. Gauge selection uses a proximity heuristic pending full watershed
delineation; terrain exposure uses a screening elevation model pending
HAND/DEM analysis. Confidence grades reflect these limits honestly.</footer>
{map_html}
</body></html>"""

    report_path = out_dir / "report.html"
    report_path.write_text(html, encoding="utf-8")

    payload = {
        "address": address,
        "geocode": geocode_note,
        "risk_class": risk_class,
        "estimate": asdict(estimate),
        "exposure_narrative": exposure_narrative,
        "sources": [{"name": n, "url": u} for n, u in sources],
        "hydat_vintage": hydat_vintage,
        "report_date": date.today().isoformat(),
        "model_version": "hazardwise-0.20",
    }
    if map_meta is not None:
        payload["map"] = map_meta
    (out_dir / "report.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return report_path
