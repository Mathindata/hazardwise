"""Local web front end for the satellite-only pipeline (v0.15).

    python -m hazardwise.webapp          ->  http://localhost:8077

One page: type an address (or lat/lon for cottages Nominatim can't resolve),
submit, and the full satellite-only report -- map, evidence chain, indices,
caveats -- opens when the run finishes (typically 15-60 s: geocode + MRDEM +
JRC windows are all live streamed services, so the machine must be online).

Deliberately stdlib-only (no Flask): nothing new to install, nothing exposed
beyond localhost. This is a LOCAL viewer for one analyst, not a deployment.
"""
from __future__ import annotations

import html
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .sat_pipeline import run_sat_report
from .satellite.occurrence import JrcOccurrence
from .reporting.sat_report import slugify

OUT_ROOT = Path("reports_sat")
PORT = 8077

# --- invitation gate (v0.15.2) ---
# Set HW_INVITE_TOKEN to a secret of your choice; otherwise a random one is
# generated and printed at startup. Anyone opening  /invite/<token>  gets a
# session cookie; every other route returns 403 without it. Restarting with a
# new token revokes all previously shared links.
import os, secrets
INVITE_TOKEN = os.environ.get("HW_INVITE_TOKEN") or secrets.token_urlsafe(16)

def _authed(handler) -> bool:
    cookies = handler.headers.get("Cookie", "")
    return f"hw_invite={INVITE_TOKEN}" in cookies

PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>HazardWise — satellite-only flood risk</title><style>
 body{{font-family:Segoe UI,system-ui,sans-serif;max-width:720px;margin:3rem auto;
      padding:0 1rem;color:#222;line-height:1.5}}
 input[type=text]{{width:100%;padding:.5rem;font-size:1rem;margin:.3rem 0}}
 .row input{{width:45%}} button{{padding:.5rem 1.4rem;font-size:1rem;
      background:#045a8d;color:#fff;border:0;border-radius:.3rem;cursor:pointer}}
 .small{{color:#666;font-size:.85rem}} .err{{color:#b30000}}
 li{{margin:.2rem 0}}</style></head><body>
<h1>HazardWise — satellite-only flood risk</h1>
<p class="small">No gauge, no HYDAT: 38 years of satellite water history
(JRC 1984-2021) transferred along the terrain (MRDEM HAND), fused with
SAR-mapped flood extents where available. Evidence grade is capped at C by
construction. Runs live against Nominatim / NRCan / JRC — stay online.
The run takes 15-60 seconds; the page will wait.</p>
<form action="/run" method="get">
 <label>Canadian address</label>
 <input type="text" name="address" placeholder="309B Macleod Trail SW, High River, AB">
 <div class="row"><label class="small">…or coordinates (overrides address):
 </label><br><input type="text" name="lat" placeholder="lat e.g. 50.582">
 <input type="text" name="lon" placeholder="lon e.g. -113.874"></div>
 <p><button>Generate report</button></p>
</form>
{error}
<h2>Previous reports</h2><ul>{reports}</ul>
<p class="small">Each report is self-contained: reports_sat\\&lt;slug&gt;\\
report.html / report.json / flood_map.png.</p>
</body></html>"""


def _index(error: str = "") -> bytes:
    items = []
    if OUT_ROOT.exists():
        for d in sorted(OUT_ROOT.iterdir()):
            if (d / "report.html").exists():
                items.append(f'<li><a href="/view/{d.name}/report.html">'
                             f'{html.escape(d.name)}</a></li>')
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return PAGE.format(error=err,
                       reports="".join(items) or "<li>none yet</li>").encode()


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype="text/html; charset=utf-8", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        url = urllib.parse.urlparse(self.path)
        if url.path.startswith("/invite/"):
            if secrets.compare_digest(url.path[len("/invite/"):], INVITE_TOKEN):
                self.send_response(303)
                self.send_header("Set-Cookie",
                                 f"hw_invite={INVITE_TOKEN}; HttpOnly; "
                                 "SameSite=Lax; Path=/")
                self.send_header("Location", "/")
                self.end_headers()
                return None
            return self._send(b"invalid invitation link", code=403)
        if not _authed(self):
            return self._send(
                b"<h2>Invitation required</h2><p>Ask the report owner for "
                b"an invitation link.</p>", code=403)
        if url.path == "/":
            return self._send(_index())
        if url.path == "/run":
            q = urllib.parse.parse_qs(url.query)
            addr = (q.get("address", [""])[0] or "").strip()
            lat = (q.get("lat", [""])[0] or "").strip()
            lon = (q.get("lon", [""])[0] or "").strip()
            if not addr and not (lat and lon):
                return self._send(_index("Give an address or both coordinates."))
            coords = None
            if lat and lon:
                try:
                    coords = (float(lat), float(lon))
                except ValueError:
                    return self._send(_index("Coordinates must be numbers."))
                addr = addr or f"site at {coords[0]:.4f}, {coords[1]:.4f}"
            try:
                path, risk, aep = run_sat_report(
                    addr, coords=coords, out_root=OUT_ROOT,
                    occ_source=JrcOccurrence())
            except Exception as e:
                traceback.print_exc(limit=2)
                return self._send(_index(
                    f"Run failed ({type(e).__name__}: {e}). Check the console;"
                    " typical causes are being offline or an unresolvable"
                    " address (try coordinates)."))
            slug = slugify(addr)
            self.send_response(303)
            self.send_header("Location", f"/view/{slug}/report.html")
            self.end_headers()
            return None
        if url.path.startswith("/view/"):
            rel = urllib.parse.unquote(url.path[len("/view/"):])
            f = (OUT_ROOT / rel).resolve()
            if OUT_ROOT.resolve() not in f.parents or not f.is_file():
                return self._send(b"not found", code=404)
            ctype = ("image/png" if f.suffix == ".png" else
                     "application/json" if f.suffix == ".json" else
                     "text/html; charset=utf-8")
            return self._send(f.read_bytes(), ctype=ctype)
        return self._send(b"not found", code=404)

    def log_message(self, fmt, *args):  # quieter console
        print("  [web]", fmt % args)


def main() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"HazardWise satellite-only web UI: http://localhost:{PORT}")
    print(f"Local invitation link:  http://localhost:{PORT}/invite/{INVITE_TOKEN}")
    print("Behind a tunnel, share: https://<your-tunnel-host>/invite/"
          f"{INVITE_TOKEN}")
    print("Ctrl+C to stop. Reports accumulate under", OUT_ROOT.resolve())
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
