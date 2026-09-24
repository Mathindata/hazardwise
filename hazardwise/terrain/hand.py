"""HAND terrain analysis, v0.4.

v0.3 field failure: after depression filling, lakes and flats are perfectly
level, D8 finds no downhill neighbour, every flat cell becomes a sink, and the
stream network FRAGMENTS ("upstream area ~0 km²" in every v0.3 report). Fix is
the standard one: priority-flood WITH EPSILON (Barnes et al. 2014) — each cell
is filled to at least (spill level + tiny increment), giving every filled flat
a monotone gradient toward its pour point so rivers connect through lakes.

Second v0.3 failure: HAND was measured to the first "stream" encountered — a
1 km² ditch — while thresholds came from gauges on 100–4,000 km² rivers. v0.4
solves terrain ONCE (TerrainModel), then answers HAND queries PER SCALE: the
downstream profile from the property records every (upstream area, drop)
crossing, so hand_at_scale(50 km²) and hand_at_scale(1 km²) come from the same
solve. Each gauge gets HAND measured against a channel of its own scale.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass

import numpy as np

_D8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
_DIST = np.array([np.hypot(r, c) for r, c in _D8])
_EPS = 1e-5   # flat-resolution gradient, metres per step (negligible physically)


def fill_depressions_eps(dem: np.ndarray) -> np.ndarray:
    """Priority-flood + epsilon: filled >= dem, and every filled flat drains."""
    nr, nc = dem.shape
    filled = np.full((nr, nc), np.inf)
    seen = np.zeros((nr, nc), dtype=bool)
    heap: list[tuple[float, int, int]] = []
    for r in range(nr):
        for c in (0, nc - 1):
            if not seen[r, c]:
                heapq.heappush(heap, (float(dem[r, c]), r, c)); seen[r, c] = True
    for c in range(nc):
        for r in (0, nr - 1):
            if not seen[r, c]:
                heapq.heappush(heap, (float(dem[r, c]), r, c)); seen[r, c] = True
    while heap:
        z, r, c = heapq.heappop(heap)
        filled[r, c] = z
        for dr, dc in _D8:
            rr, cc = r + dr, c + dc
            if 0 <= rr < nr and 0 <= cc < nc and not seen[rr, cc]:
                seen[rr, cc] = True
                heapq.heappush(heap, (max(float(dem[rr, cc]), z + _EPS), rr, cc))
    return filled


# backwards-compatible alias (v0.3 tests)
def fill_depressions(dem: np.ndarray) -> np.ndarray:
    return fill_depressions_eps(np.asarray(dem, dtype=float))


def d8_directions(filled: np.ndarray) -> np.ndarray:
    """Vectorised steepest-descent D8. -1 only at true edge sinks."""
    nr, nc = filled.shape
    pad = np.pad(filled, 1, constant_values=np.inf)
    slopes = np.empty((8, nr, nc))
    for i, (dr, dc) in enumerate(_D8):
        nb = pad[1 + dr:1 + dr + nr, 1 + dc:1 + dc + nc]
        slopes[i] = (filled - nb) / _DIST[i]
    best = slopes.argmax(axis=0).astype(np.int8)
    direc = np.where(slopes.max(axis=0) > 0, best, -1).astype(np.int8)
    return direc


def flow_accumulation(filled: np.ndarray, direc: np.ndarray) -> np.ndarray:
    nr, nc = filled.shape
    acc = np.ones((nr, nc), dtype=np.int64)
    order = np.argsort(filled, axis=None)[::-1]
    dirs = direc.ravel()
    accf = acc.ravel()
    for idx in order:
        d = dirs[idx]
        if d >= 0:
            r, c = divmod(int(idx), nc)
            dr, dc = _D8[d]
            accf[(r + dr) * nc + (c + dc)] += accf[idx]
    return acc


@dataclass(frozen=True)
class HandResult:
    hand_m: float
    fill_depth_m: float
    stream_distance_m: float
    stream_area_km2: float
    max_stream_area_km2: float
    reached_scale: bool          # False -> requested scale not in window
    narrative: list[str]


class TerrainModel:
    """One DEM solve, many scale-specific HAND queries."""

    def __init__(self, dem: np.ndarray, cell_size_m: float):
        self.dem = np.asarray(dem, dtype=float)
        self.cell = float(cell_size_m)
        self.filled = fill_depressions_eps(self.dem)
        self.fill_depth = self.filled - self.dem
        self.direc = d8_directions(self.filled)
        self.acc = flow_accumulation(self.filled, self.direc)
        self.cell_km2 = (self.cell / 1000.0) ** 2
        nr, nc = self.dem.shape
        self.center = (nr // 2, nc // 2)

    @property
    def max_stream_area_km2(self) -> float:
        return float(self.acc.max() * self.cell_km2)

    @property
    def center_fill_depth_m(self) -> float:
        return float(self.fill_depth[self.center])

    def hand_at_scale(self, min_area_km2: float) -> HandResult:
        """HAND = height above the NEAREST channel cell with >= min_area_km2
        upstream (Euclidean nearest, not along-path: for flood purposes what
        matters is the water surface of that river where it passes closest to
        the property, and along-path distance wrongly accumulates channel
        slope when the confluence is far downstream)."""
        # A clipped window can never show a big river's true upstream area:
        # accumulation restarts at the window edge (v0.4 field failure — the
        # Highwood at High River read "~38 km²"). Cap the requested scale by
        # what THIS window can express, and mask the whole channel (>= 25% of
        # window max), not just its exit cells — otherwise "nearest channel
        # cell" lands kilometres downstream at the window edge.
        window_cap_km2 = max(0.25 * self.max_stream_area_km2, 2 * self.cell_km2)
        effective_km2 = min(min_area_km2, window_cap_km2)
        capped = effective_km2 < min_area_km2
        target_cells = max(1, int(round(effective_km2 / self.cell_km2)))
        mask = self.acc >= target_cells
        reached = not capped and bool(mask.any())
        if not mask.any():
            cutoff = max(2, int(self.acc.max() * 0.25))
            mask = self.acc >= cutoff
        rr, cc = np.nonzero(mask)
        r0, c0 = self.center
        d2 = (rr - r0) ** 2 + (cc - c0) ** 2
        # MIN HAND within a search radius, not nearest-cell HAND. Field
        # failure (Kamloops): Peterson Creek's incised gorge sat 480 m from
        # downtown and qualified on upstream area, giving HAND 17.3 m, while
        # the Thompson — the river that actually floods the city — sat 900 m
        # away at HAND ~5 m. A property is threatened by whichever qualifying
        # water body it stands LEAST above; take the channel cell with the
        # highest elevation within the radius. Radius is bounded so upstream
        # channel slope cannot spuriously zero-out real standing.
        R = int(round(1500.0 / self.cell))
        z0 = self.dem[r0, c0]
        near = d2 <= R * R
        # v0.12 (Kelowna false-High): among nearby qualifying cells, consider
        # only those AT OR BELOW the property (+2 m tolerance). A creek running
        # above the property on a hillside zeroed HAND spuriously; closed
        # depressions remain covered by fill-depth detection, not this rule.
        eligible = near & (self.dem[rr, cc] <= z0 + 2.0)
        if eligible.any():
            sel = np.argmax(self.dem[rr[eligible], cc[eligible]])
            idx = np.nonzero(eligible)[0][int(sel)]
        elif near.any():
            sel = np.argmin(self.dem[rr[near], cc[near]])
            idx = np.nonzero(near)[0][int(sel)]
        else:
            idx = int(np.argmin(d2))
        r, c = int(rr[idx]), int(cc[idx])
        drop = max(0.0, float(self.dem[r0, c0] - self.dem[r, c]))
        dist = float(np.sqrt(d2[idx])) * self.cell
        a = float(self.acc[r, c] * self.cell_km2)
        fd = self.center_fill_depth_m
        nr = self.dem.shape[0]
        narrative = [
            f"Terrain analysis of a {nr * self.cell / 1000:.0f} km elevation "
            f"window (30 m national DEM): the property stands {drop:.1f} m above "
            f"the nearest channel of ~{a:,.0f} km² upstream area, {dist:,.0f} m "
            f"away.",
        ]
        if not reached:
            narrative.append(
                f"The gauged river's full upstream area ({min_area_km2:,.0f} km² "
                "at the matching scale) extends beyond this terrain window, so it "
                "was matched to the largest watercourse present in the window; "
                "confidence is reduced accordingly."
            )
        if fd > 0.5:
            narrative.append(
                f"The property lies inside a closed depression: local topography "
                f"would pond roughly {fd:.1f} m of water here before it could "
                "drain away. Diked or pumped lands (former lake beds, low "
                "prairies) show exactly this signature and flood severely when "
                "defences are overtopped — this raises the risk class directly."
            )
        return HandResult(drop, fd, dist, a, self.max_stream_area_km2,
                          reached, narrative)


def compute_hand(dem, cell_size_m: float, stream_threshold_km2: float = 1.0,
                 center=None) -> HandResult:
    """v0.3-compatible wrapper: single-scale HAND at the window centre."""
    tm = TerrainModel(dem, cell_size_m)
    if center is not None:
        tm.center = center
    return tm.hand_at_scale(stream_threshold_km2)
