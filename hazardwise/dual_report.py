"""Single-command, single-report dual-hazard assessment (v0.18):

    python -m hazardwise.dual_report "ADDRESS" [more addresses...]

One report.html per address containing BOTH hazards: flood (blue-hatched
map, evidence chain, indices, caveats) and wildfire (red-hatched map, its
own chain, indices, caveats), one JSON (benchmark-compatible: flood fills
the audited `estimate`/`map` blocks; fire ships under `fire`). The
individual single-hazard reports are kept alongside under _flood/ and
_fire/. Visualization flags are shared with combined_report:
--basemap street|hillshade|none, --basemap-opacity, --opacity,
--flood-colors, --fire-colors, --no-hatch.
Fire grades live once an NBAC file is cached (see WINDOWS_SETUP S4);
without it the fire section degrades to grade-D screening, disclosed."""
from __future__ import annotations
import argparse, base64, json, re, sys
from datetime import datetime
from pathlib import Path
from .sat_pipeline import run_sat_report
from .fire_pipeline import run_fire_report
from .satellite.occurrence import JrcOccurrence
from .reporting.sat_report import RISK_COLORS, GRADE_COLORS, slugify
from .combined_report import _viz

def _b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()

def _headline(e):
    a = e.get("aep_central") or 0
    if a > 0:
        return (f"Annual probability <b>{a:.1%}</b> (roughly once every "
                f"{1/a:,.0f} years); credible range "
                f"{e['aep_ci_lo']:.1%} – {e['aep_ci_hi']:.1%}.")
    return "No probability estimated — screening only (see caveats)."

def _section(title, j, map_b64, ctx_b64=None):
    e = j["estimate"]
    rc = RISK_COLORS.get(j["risk_class"], "#555")
    gc = GRADE_COLORS.get(e["grade"], "#555")
    rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>"
                   for k, v in j["indices"].items())
    return f"""
<h2>{title}</h2>
<p><span class="badge" style="background:{rc}">Risk: {j['risk_class']}</span>
<span class="badge" style="background:{gc}">Grade: {e['grade']}</span></p>
<p>{_headline(e)}</p>
<h3>How this estimate was reached</h3>
<ol>{''.join(f'<li>{l}</li>' for l in e['explanation_chain'])}</ol>
<h3>Risk indices</h3><table>{rows}</table>
<img style="max-width:100%" src="data:image/png;base64,{map_b64}">
{f'<h3>Regional context</h3><img style="max-width:100%" src="data:image/png;base64,{ctx_b64}">' if ctx_b64 else ''}
<h3>Mandatory caveats</h3>
<ul>{''.join(f'<li>{c}</li>' for c in e['mandatory_caveats'])}</ul>"""

def run_dual(address, *, coords=None, out_root=Path("reports_dual"),
             viz_flood=None, viz_fire=None, flood_kwargs=None,
             fire_kwargs=None, region_km=None, debug=False):
    out_dir = out_root / slugify(address)
    from .debuglog import DebugLog
    dbg = DebugLog(enabled=bool(debug), to_stdout=(debug == "stdout"))
    fk = dict(flood_kwargs or {})
    if "occ" not in fk and "occ_source" not in fk:
        fk["occ_source"] = JrcOccurrence()
    p1, frisk, faep = run_sat_report(address, coords=coords,
                                     out_root=out_dir / "_flood",
                                     render_kwargs=viz_flood,
                                     region_km=region_km, debug=dbg, **fk)
    p2, arisk, aprob = run_fire_report(address, coords=coords,
                                       out_root=out_dir / "_fire",
                                       render_kwargs=viz_fire,
                                       region_km=region_km, debug=dbg,
                                       **(fire_kwargs or {}))
    jf = json.loads((p1.parent / "report.json").read_text())
    jr = json.loads((p2.parent / "report.json").read_text())
    m = re.search(r"\((-?[\d.]+), (-?[\d.]+)\)",
                  (p1.parent / "report.html").read_text())
    gv = ""
    if m:
        lat, lon = m.group(1), m.group(2)
        gv = (f'<h2>Ground-level view</h2><p><a href="https://www.google.com/'
              f'maps/@?api=1&map_action=pano&viewpoint={lat},{lon}">Street '
              f'View</a> &middot; <a href="https://www.openstreetmap.org/'
              f'?mlat={lat}&mlon={lon}#map=17/{lat}/{lon}">OpenStreetMap</a>'
              f'</p>')
    srcs = {n: u for n, u in (jf["sources"] + jr["sources"])}
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>HazardWise dual-hazard — {address}</title><style>
body{{font-family:Segoe UI,system-ui,sans-serif;max-width:900px;margin:2rem
 auto;padding:0 1rem;color:#222;line-height:1.5}}
.badge{{display:inline-block;padding:.3rem .8rem;border-radius:.4rem;
 color:#fff;font-weight:600;margin-right:.5rem}}
table{{border-collapse:collapse;width:100%}}td{{border:1px solid #ddd;
 padding:.35rem .6rem}}td:first-child{{width:55%;color:#444}}
.small{{color:#666;font-size:.85rem}}</style></head><body>
<h1>HazardWise — dual-hazard risk report</h1>
<p><b>{address}</b></p>
{'<p style="background:#8a1c1c;color:#fff;padding:.6rem .8rem;border-radius:.4rem"><b>LOCATION IMPRECISE:</b> locality-centroid geocode — maps may not depict the true parcel&#39;s surroundings. Supply --lat/--lon.</p>' if (jf.get("map") or {}).get("geocode_gated") else ''}
{_section("Flood risk (satellite-only)", jf,
          _b64(p1.parent / "flood_map.png"),
          _b64(p1.parent / "flood_context.png")
          if (p1.parent / "flood_context.png").exists() else None)}
{_section("Wildfire risk (archive-only)", jr,
          _b64(p2.parent / "fire_map.png"),
          _b64(p2.parent / "fire_context.png")
          if (p2.parent / "fire_context.png").exists() else None)}
{gv}
<h2>Data sources</h2><ul>{''.join(
    f'<li>{n} — <a href="{u}">{u}</a></li>' for n, u in srcs.items())}</ul>
<p class="small">Generated {datetime.now():%Y-%m-%d %H:%M}. Two independent
evidence chains; neither hazard borrows the other's grade. Single-hazard
reports: <a href="_flood/{p1.parent.name}/report.html">flood</a> ·
<a href="_fire/{p2.parent.name}/report.html">fire</a>.</p>
</body></html>"""
    (out_dir / "report.html").write_text(html, encoding="utf-8")
    payload = {"address": address, "mode": "dual",
               "hazards": ["flood", "fire"],
               "estimate": jf["estimate"], "map": jf.get("map"),
               "indices": jf["indices"],
               "fire": {"risk_class": jr["risk_class"],
                        "estimate": jr["estimate"],
                        "indices": jr["indices"], "map": jr.get("map")},
               "sources": list(srcs.items())}
    (out_dir / "report.json").write_text(json.dumps(payload, indent=2,
                                                    default=str))
    return out_dir / "report.html", (frisk, faep), (arisk, aprob)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("addresses", nargs="+")
    ap.add_argument("--out", type=Path, default=Path("reports_dual"))
    ap.add_argument("--basemap", choices=["street", "aerial", "hillshade", "none"],
                    default="street")
    ap.add_argument("--basemap-opacity", type=float, default=0.5)
    ap.add_argument("--opacity", type=float, default=0.6)
    ap.add_argument("--flood-colors"); ap.add_argument("--fire-colors")
    ap.add_argument("--no-hatch", action="store_true")
    ap.add_argument("--radius", type=float, default=None,
                    help="regional history/rate radius in km (default 25)")
    ap.add_argument("--debug", nargs="?", const=True, default=False,
                    help="write a debug trace to reports_debug/; "
                         "--debug stdout also streams it (pipe to Claude "
                         "Desktop)")
    a = ap.parse_args()
    for addr in a.addresses:
        print(f"\n=== {addr} ===")
        try:
            dbg = "stdout" if a.debug == "stdout" else bool(a.debug)
            path, (fr, fp), (ar, apb) = run_dual(
                addr, out_root=a.out,
                viz_flood=_viz(a, a.flood_colors),
                viz_fire=_viz(a, a.fire_colors),
                region_km=a.radius, debug=dbg)
            print(f"  Flood: {fr} ({fp:.2%}) | Fire: {ar} ({apb:.2%})")
            print(f"  Report: {path}")
        except Exception as e:
            print(f"  FAILED: {e}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
