"""Report map: binned PuBu probability surface over a HAND hillshade backdrop
(offline-safe; swap backdrop for contextily grayscale tiles at deploy), permanent
water, observed-extent outlines, uncertainty hatching, geocode gate, and a
strictly separate susceptibility mode. Returns a metadata dict consumed by
integrity checks — the legend cannot silently go missing."""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm
from matplotlib.patches import Patch, Circle
from matplotlib.lines import Line2D
from . import params_sat as P

def _bin_index(aep):
    """0 = lightest bin ... 4 = darkest; -1 = below lowest bin."""
    bins = np.array(P.SAT_AEP_BINS[::-1])  # ascending: .002 .005 .01 .02 .05
    idx = np.digitize(np.asarray(aep, float), bins) - 1
    return idx  # -1 below 0.002; 4 means >= .05

def render_map(aoi, fusion, hand_m, stack, out_png,
               constants_stamp="Model constants: DEFAULT", date_range="",
               grade=None, basemap_img=None, basemap_alpha=0.5,
               layer_alpha=0.6, colors=None, hatches=None):
    n = aoi.n
    fig, ax = plt.subplots(figsize=(7.2, 7.8), dpi=150)

    # backdrop: HAND hillshade proxy (grayscale)
    if basemap_img is not None:
        ax.imshow(basemap_img, alpha=basemap_alpha)
    else:
        ax.imshow(np.clip(hand_m, 0, 30), cmap="gray_r", alpha=0.35)
    pal = tuple(colors) if colors else P.SAT_AEP_COLORS
    hs = tuple(hatches) if hatches is not None else P.SAT_BIN_HATCHES_ASC

    legend_items = []
    if fusion.mode == "probability":
        bins_asc = list(P.SAT_AEP_BINS[::-1]) + [1.0]           # .002...1.0
        cmap = ListedColormap(list(pal[::-1]))     # light->dark
        norm = BoundaryNorm(bins_asc, cmap.N)
        shown = np.ma.masked_where((fusion.aep < P.SAT_AEP_BINS[-1]) |
                                   stack.permanent_water, fusion.aep)
        ax.imshow(shown, cmap=cmap, norm=norm, alpha=layer_alpha)
        # v0.15: encode the risk level TWICE -- hue and hatch texture -- so
        # neighbourhood risk bands stay readable in grayscale print and for
        # colorblind readers. Ascending density with ascending AEP.
        idx = _bin_index(fusion.aep)
        drawable = ~stack.permanent_water & (fusion.aep >= P.SAT_AEP_BINS[-1])
        for i, h in enumerate(hs):
            if not h:
                continue
            m = drawable & (idx == i)
            if m.any():
                cs = ax.contourf(m.astype(float), levels=[0.5, 1.5],
                                 colors="none", hatches=[h])
                for coll in getattr(cs, "collections", [cs]):
                    coll.set_edgecolor(P.SAT_BIN_HATCH_COLOR)
                    coll.set_linewidth(0.0)
        rp = ["1:200-1:500", "1:100-1:200", "1:50-1:100", "1:20-1:50", "<=1:20"]
        for c, h, lab in zip(pal[::-1], hs, rp):
            legend_items.append(Patch(fc=c, alpha=layer_alpha, hatch=h,
                                      ec=P.SAT_BIN_HATCH_COLOR,
                                      label=f"AEP {lab}"))
        title = "Flood probability (annual exceedance)"
        # uncertainty hatching where CI spans >= SAT_CI_HATCH_BINS bins
        span = _bin_index(fusion.aep_hi) - _bin_index(fusion.aep_lo)
        unc = (span >= P.SAT_CI_HATCH_BINS) & ~stack.permanent_water
        if unc.any():
            cs = ax.contourf(unc.astype(float), levels=[0.5, 1.5],
                             colors="none", hatches=[P.SAT_UNC_HATCH])
            for coll in getattr(cs, "collections", [cs]):
                coll.set_edgecolor("gray"); coll.set_linewidth(0.0)
            legend_items.append(Patch(fc="none", hatch=P.SAT_UNC_HATCH,
                                      ec="gray",
                                      label="probability uncertain (wide CI)"))
    else:
        # susceptibility mode: different ramp, different legend title, no AEP legend
        cmap = ListedColormap(list(P.SAT_SUSCEPT_COLORS[::-1]))
        bands = [0.0] + list(P.SAT_SUSCEPT_BANDS_M)
        norm = BoundaryNorm(bands, cmap.N)
        shown = np.ma.masked_where(hand_m >= P.SAT_SUSCEPT_BANDS_M[-1], hand_m)
        ax.imshow(shown, cmap=cmap, norm=norm, alpha=layer_alpha)
        labs = ["HAND < 2 m", "HAND 2-5 m", "HAND 5-10 m"]
        for c, lab in zip(P.SAT_SUSCEPT_COLORS[::-1], labs):
            legend_items.append(Patch(fc=c, alpha=layer_alpha, label=lab))
        title = "Relative flood SUSCEPTIBILITY (not probability)"

    # permanent water
    if stack.permanent_water.any():
        pw = np.ma.masked_where(~stack.permanent_water,
                                np.ones_like(hand_m))
        ax.imshow(pw, cmap=ListedColormap([P.SAT_PERMWATER_COLOR]), alpha=0.9)
    legend_items.append(Patch(fc=P.SAT_PERMWATER_COLOR, label="permanent water (JRC)"))

    # observed extents outline
    u = stack.union_mask()
    if u.any():
        ax.contour(u.astype(float), levels=[0.5],
                   colors=[P.SAT_EXTENT_OUTLINE_COLOR], linestyles="dashed",
                   linewidths=1.2)
    legend_items.append(Line2D([0], [0], color=P.SAT_EXTENT_OUTLINE_COLOR,
                               ls="--", label=f"observed extents ({stack.provenance()})"))

    # address marker / geocode gate
    r0, c0 = aoi.address_rc
    if aoi.geocode_gated:
        rad = aoi.geocode_precision_m / aoi.res_m
        ax.add_patch(Circle((c0, r0), rad, fill=False, ec="black", lw=1.5))
        addr_lab = f"address (geocode +/-{aoi.geocode_precision_m:.0f} m)"
    else:
        ax.plot(c0, r0, marker="v", ms=11, mec="black", mfc="black", alpha=layer_alpha)
        addr_lab = "address"
    legend_items.append(Line2D([0], [0], marker="v", color="none", mec="black",
                               mfc="black", label=addr_lab))

    # scale bar (500 m) + north arrow
    px500 = 500.0 / aoi.res_m
    ax.plot([n * 0.05, n * 0.05 + px500], [n * 0.95] * 2, "k-", lw=3)
    ax.text(n * 0.05, n * 0.93, "500 m", fontsize=8)
    ax.annotate("N", xy=(n * 0.95, n * 0.08), xytext=(n * 0.95, n * 0.16),
                ha="center", fontsize=10,
                arrowprops=dict(arrowstyle="-|>", color="black"))

    from .basemap import ATTRIB as _BA
    prov = ((f"{_BA} | " if basemap_img is not None else "") + f"{stack.provenance()} | HAND: MRDEM-30 | grid {aoi.res_m:.0f} m | "
            f"{date_range} | {constants_stamp}")
    ax.set_title(title, fontsize=11)
    fig.text(0.02, 0.015, prov, fontsize=6.5)
    ax.legend(handles=legend_items, loc="upper left", fontsize=7, framealpha=0.9)
    ax.set_xticks([]); ax.set_yticks([])
    fig.tight_layout()
    fig.savefig(out_png); plt.close(fig)

    return {  # consumed by integrity checks
        "mode": fusion.mode, "title": title, "out_png": out_png,
        "legend_blocks": {
            "bins": fusion.mode == "probability",
            "permanent_water": True,
            "observed_extents": True,
            "uncertainty": fusion.mode == "probability",
            "bin_hatches": fusion.mode == "probability",
            "provenance": bool(prov.strip()),
        },
        "provenance": prov,
        "address_bin": int(_bin_index(fusion.aep)[r0, c0])
                       if fusion.mode == "probability" else None,
        "geocode_gated": aoi.geocode_gated,
    }
