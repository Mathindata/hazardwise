"""Stack of observed flood extents on the AOI grid, with provenance and the
spatial label-hygiene guards (permanent-water mask, HAND clip)."""
from dataclasses import dataclass, field
from typing import List, Optional
import numpy as np
import pandas as pd
from . import params_sat as P

VETTED_SOURCES = {"EGS", "EMS"}   # S1_SELF admitted only above the IoU gate

@dataclass
class ExtentLayer:
    mask: np.ndarray           # bool, grid-shaped
    source: str                # EGS | EMS | S1_SELF | GFM
    event_id: str
    date: pd.Timestamp
    scene_id: str = ""
    iou_vs_egs: Optional[float] = None

    @property
    def admitted(self) -> bool:
        if self.source in VETTED_SOURCES:
            return True
        return self.iou_vs_egs is not None and self.iou_vs_egs >= P.SAT_IOU_ADMIT

@dataclass
class ExtentStack:
    grid_n: int
    hand_m: np.ndarray
    layers: List[ExtentLayer] = field(default_factory=list)
    permanent_water: np.ndarray = None

    def __post_init__(self):
        if self.permanent_water is None:
            self.permanent_water = np.zeros((self.grid_n,) * 2, bool)

    def set_permanent_water_from_occurrence(self, occ_pct: np.ndarray):
        self.permanent_water = occ_pct >= P.SAT_PERMWATER_PCT

    def add(self, layer: ExtentLayer, era_start: Optional[pd.Timestamp] = None):
        """era_start: regulated-reach filter — pre-structure floods are not evidence."""
        if era_start is not None and layer.date < era_start:
            return
        m = layer.mask & (self.hand_m < P.SAT_HAND_MAX_M) & ~self.permanent_water
        self.layers.append(ExtentLayer(m, layer.source, layer.event_id,
                                       layer.date, layer.scene_id, layer.iou_vs_egs))

    def admitted_layers(self):
        return [l for l in self.layers if l.admitted]

    def wet_event_count(self) -> np.ndarray:
        """k per pixel: number of DISTINCT events observed wet (union within event)."""
        k = np.zeros((self.grid_n,) * 2, np.int32)
        by_event = {}
        for l in self.admitted_layers():
            by_event.setdefault(l.event_id, np.zeros_like(k, bool))
            by_event[l.event_id] |= l.mask
        for m in by_event.values():
            k += m.astype(np.int32)
        return k

    def union_mask(self) -> np.ndarray:
        u = np.zeros((self.grid_n,) * 2, bool)
        for l in self.admitted_layers():
            u |= l.mask
        return u

    def provenance(self) -> str:
        by = {}
        for l in self.admitted_layers():
            by.setdefault(l.source, set()).add(l.event_id)
        n_ev = len({l.event_id for l in self.admitted_layers()})
        parts = [f"{len(v)} {k}" for k, v in sorted(by.items())]
        return f"{n_ev} events: " + ", ".join(parts) if parts else "no observed extents"
