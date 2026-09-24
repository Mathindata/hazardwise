"""Satellite-only report renderer — v0.12 house style: badges, evidence chain,
mandatory caveats, sources, JSON with the SAME estimate schema the benchmark
audits, plus the flood-risk-index table and the map."""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from .report import RISK_COLORS, GRADE_COLORS, slugify  # noqa: F401

def render_sat_report(*, address, geocode_note, risk_class, grade, aep, aep_lo,
                      aep_hi, chain, caveats, indices, sources, map_html,
                      map_meta, out_dir: Path, hazard: str = "Flood",
                      latlon=None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    rc = RISK_COLORS.get(risk_class, "#555")
    gc = GRADE_COLORS.get(grade, "#555")
    if aep and aep > 0:
        headline = (f"Annual exceedance probability <b>{aep:.1%}</b> "
                    f"(central) — roughly once every {1/aep:,.0f} years; "
                    f"credible range {aep_lo:.1%} – {aep_hi:.1%}.")
    else:
        headline = ("No probability is estimated for this location — "
                    "terrain screening only (see caveats).")
    rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>"
                   for k, v in indices.items())
    chain_html = "".join(f"<li>{l}</li>" for l in chain if l != "—")
    cav_html = "".join(f"<li>{c}</li>" for c in caveats)
    src_html = "".join(f'<li>{n} — <a href="{u}">{u}</a></li>'
                       for n, u in sources)
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>HazardWise satellite-only {hazard.lower()} risk — {address}</title>
<style>
 body{{font-family:Segoe UI,system-ui,sans-serif;max-width:900px;margin:2rem auto;
      padding:0 1rem;color:#222;line-height:1.5}}
 .badge{{display:inline-block;padding:.3rem .8rem;border-radius:.4rem;color:#fff;
        font-weight:600;margin-right:.5rem}}
 table{{border-collapse:collapse;width:100%}} td{{border:1px solid #ddd;
        padding:.35rem .6rem}} td:first-child{{width:55%;color:#444}}
 .small{{color:#666;font-size:.85rem}}
</style></head><body>
<h1>HazardWise — satellite-only {hazard.lower()} risk report</h1>
<p><b>{address}</b><br><span class="small">{geocode_note}</span></p>
{'<p style="background:#8a1c1c;color:#fff;padding:.6rem .8rem;border-radius:.4rem"><b>LOCATION IMPRECISE:</b> this address resolved only to a locality/county centroid — the maps below depict the centroid&#39;s surroundings, which may look SAFER (or riskier) than the true parcel. Supply --lat/--lon for a parcel-scale report.</p>' if 'centroid' in geocode_note.lower() else ''}<p><span class="badge" style="background:{rc}">Risk: {risk_class}</span>
   <span class="badge" style="background:{gc}">Evidence grade: {grade}</span></p>
<p>{headline}</p>
<h2>How this estimate was reached</h2><ol>{chain_html}</ol>
<h2>Flood risk indices</h2><table>{rows}</table>
{map_html}
{"" if not latlon else
 f'<h2>Ground-level view</h2><p><a href="https://www.google.com/maps/@?api=1'
 f'&map_action=pano&viewpoint={latlon[0]},{latlon[1]}">Open Street View at '
 f'this location</a> &middot; <a href="https://www.openstreetmap.org/'
 f'?mlat={latlon[0]}&mlon={latlon[1]}#map=17/{latlon[0]}/{latlon[1]}">'
 f'OpenStreetMap</a></p>'}<h2>Mandatory caveats</h2><ul>{cav_html}</ul>
<h2>Data sources</h2><ul>{src_html}</ul>
<p class="small">Generated {datetime.now():%Y-%m-%d %H:%M}. Satellite-only
mode: no in-situ hydrometric gauge was used anywhere in this analysis.</p>
</body></html>"""
    (out_dir / "report.html").write_text(html, encoding="utf-8")
    payload = {
        "address": address, "risk_class": risk_class, "mode": "satellite_only",
        "estimate": {"aep_central": aep, "aep_ci_lo": aep_lo,
                     "aep_ci_hi": aep_hi, "grade": grade,
                     "explanation_chain": chain, "mandatory_caveats": caveats,
                     "gauges_used": []},
        "indices": indices, "map": map_meta, "sources": sources,
    }
    (out_dir / "report.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return out_dir / "report.html"
