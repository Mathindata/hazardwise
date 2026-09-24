"""Self-generated Sentinel-1 flood extents: change detection vs dry reference,
Otsu split, HAND mask, morphological cleanup. Arrays in, boolean mask out —
scene IO stays in the caller so this is fully testable offline."""
import numpy as np
from scipy import ndimage
from . import params_sat as P

def otsu_threshold(x: np.ndarray, nbins: int = 256) -> float:
    x = x[np.isfinite(x)]
    hist, edges = np.histogram(x, bins=nbins)
    hist = hist.astype(float); centers = 0.5 * (edges[:-1] + edges[1:])
    w1 = np.cumsum(hist); w2 = w1[-1] - w1
    m1 = np.cumsum(hist * centers) / np.maximum(w1, 1e-12)
    m2 = (np.cumsum((hist * centers)[::-1])[::-1]) / np.maximum(w2, 1e-12)
    var_between = w1[:-1] * w2[:-1] * (m1[:-1] - m2[:-1]) ** 2
    return float(centers[np.argmax(var_between)])

def classify_scene(gamma0_db: np.ndarray, dry_ref_db: np.ndarray,
                   hand_m: np.ndarray,
                   drop_db: float = P.SAT_S1_DROP_DB,
                   hand_max_m: float = P.SAT_HAND_MAX_M,
                   open_px: int = P.SAT_MORPH_OPEN_PX) -> np.ndarray:
    diff = gamma0_db - dry_ref_db
    wet = diff <= drop_db
    thr = otsu_threshold(gamma0_db)
    wet |= gamma0_db <= thr            # open-water low backscatter
    wet &= hand_m < hand_max_m         # slope/shadow false-positive guard
    if open_px > 0:
        st = ndimage.generate_binary_structure(2, 1)
        wet = ndimage.binary_opening(wet, structure=st, iterations=open_px)
    return wet

def iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = np.logical_and(a, b).sum(); union = np.logical_or(a, b).sum()
    return float(inter) / union if union else 0.0
