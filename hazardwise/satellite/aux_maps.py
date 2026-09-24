"""Auxiliary context maps (v0.19): a WIDER view than the 3 km risk map so
the reader can anchor the address against terrain, rivers, burns and wind
they recognize.
- Flood aux: topography (hillshade + elevation tint) with PERMANENT water
  (JRC >= 80%) solid and SEASONAL/temporal water (5-80%) light, ~12 km.
- Fire aux: terrain + historical NBAC perimeters labeled by year, current
  wind arrow (Open-Meteo, fail-open); vegetation/fuel layer is stamped as
  pending M-FIRE-2 rather than faked."""
from __future__ import annotations
import json, urllib.request
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.lines import Line2D
from matplotlib.colors import LightSource

def fetch_wind(lat, lon, timeout=10):
    """Current 10 m wind (speed km/h, direction FROM, deg) via Open-Meteo.
    Raises on any failure; callers treat wind as optional."""
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}"
           f"&longitude={lon}&current=wind_speed_10m,wind_direction_10m")
    with urllib.request.urlopen(url, timeout=timeout) as r:
        c = json.load(r)["current"]
    return float(c["wind_speed_10m"]), float(c["wind_direction_10m"])

def _terrain_ax(ax, dem, title):
    ls = LightSource(azdeg=315, altdeg=45)
    ax.imshow(ls.shade(dem, cmap=plt.cm.gist_earth, blend_mode="overlay",
                       vert_exag=2), interpolation="bilinear")
    ax.set_title(title, fontsize=10)
    ax.set_xticks([]); ax.set_yticks([])

def _scalebar(ax, n, cell_m, km=2):
    px = km * 1000.0 / cell_m
    ax.plot([n*0.05, n*0.05+px], [n*0.95]*2, "k-", lw=3)
    ax.text(n*0.05, n*0.92, f"{km} km", fontsize=8)

def render_flood_context(dem, cell_m, occ, out_png, half_km):
    n = dem.shape[0]
    fig, ax = plt.subplots(figsize=(6.8, 6.8), dpi=140)
    _terrain_ax(ax, dem, f"Regional context: topography & waters "
                         f"(~{2*half_km:.0f} km across)")
    legend = []
    if occ is not None:
        perm = occ >= 80
        seas = (occ >= 5) & (occ < 80)
        if seas.any():
            m = np.ma.masked_where(~seas, np.ones_like(dem))
            ax.imshow(m, cmap=matplotlib.colors.ListedColormap(["#7db8e8"]),
                      alpha=0.75)
        if perm.any():
            m = np.ma.masked_where(~perm, np.ones_like(dem))
            ax.imshow(m, cmap=matplotlib.colors.ListedColormap(["#08306b"]),
                      alpha=0.95)
        legend += [Patch(fc="#08306b", label="permanent water (JRC >=80%)"),
                   Patch(fc="#7db8e8", label="seasonal/temporal water "
                                             "(JRC 5-80%)")]
    ax.plot(n//2, n//2, marker="v", ms=10, mec="k", mfc="k")
    legend.append(Line2D([0], [0], marker="v", color="none", mec="k",
                         mfc="k", label="address"))
    _scalebar(ax, n, cell_m)
    ax.legend(handles=legend, loc="upper left", fontsize=7, framealpha=0.9)
    fig.text(0.02, 0.01, "MRDEM hillshade | JRC GSW 1984-2021", fontsize=6.5)
    fig.tight_layout(); fig.savefig(out_png); plt.close(fig)
    return out_png

def render_fire_context(dem, cell_m, nbac_layers, out_png, half_km,
                        wind=None):
    n = dem.shape[0]
    fig, ax = plt.subplots(figsize=(6.8, 6.8), dpi=140)
    _terrain_ax(ax, dem, f"Regional context: terrain, burn history & wind "
                         f"(~{2*half_km:.0f} km across)")
    legend = []
    years = sorted({y for _, y in nbac_layers})
    cmap = plt.cm.autumn_r
    for m, y in nbac_layers:
        if m.any():
            c = cmap(0.15 + 0.8 * (years.index(y) / max(len(years)-1, 1)))
            ax.contourf(m.astype(float), levels=[0.5, 1.5],
                        colors=[c], alpha=0.45)
            ax.contour(m.astype(float), levels=[0.5], colors=[c],
                       linewidths=1.2)
    for y in years:
        c = cmap(0.15 + 0.8 * (years.index(y) / max(len(years)-1, 1)))
        legend.append(Patch(fc=c, alpha=0.5, label=f"burn {y}"))
    if wind is not None:
        spd, deg = wind
        # direction is FROM; arrow points TOWARD (deg+180), map y is south-down
        th = np.deg2rad((deg + 180.0) % 360.0)
        dx, dy = np.sin(th), -np.cos(th)
        L = n * 0.12
        ax.annotate("", xy=(n*0.85 + dx*L, n*0.15 + dy*L),
                    xytext=(n*0.85, n*0.15),
                    arrowprops=dict(arrowstyle="-|>", lw=2, color="#1a1a1a"))
        ax.text(n*0.85, n*0.10, f"wind {spd:.0f} km/h",
                ha="center", fontsize=8)
        legend.append(Line2D([0], [0], color="#1a1a1a",
                             label=f"current wind (from {deg:.0f} deg)"))
    ax.plot(n//2, n//2, marker="v", ms=10, mec="k", mfc="k")
    legend.append(Line2D([0], [0], marker="v", color="none", mec="k",
                         mfc="k", label="address"))
    _scalebar(ax, n, cell_m)
    ax.legend(handles=legend, loc="upper left", fontsize=7, framealpha=0.9)
    fig.text(0.02, 0.01, "MRDEM hillshade | NBAC perimeters | wind: "
             "Open-Meteo CURRENT conditions (not climatology) | vegetation/"
             "fuel layer: pending M-FIRE-2", fontsize=6)
    fig.tight_layout(); fig.savefig(out_png); plt.close(fig)
    return out_png
