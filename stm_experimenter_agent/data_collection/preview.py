"""Preview image rendering for scans.

Generates a PNG suitable for the annotation UI. We deliberately stay
matplotlib-only to avoid pulling in PIL just for save_png.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

try:  # matplotlib is a heavy import; keep it optional at import time.
    import matplotlib
    matplotlib.use("Agg", force=False)
    import matplotlib.pyplot as plt
    _MPL_AVAILABLE = True
except Exception:  # noqa: BLE001
    _MPL_AVAILABLE = False


def _plane_subtract(z: np.ndarray) -> np.ndarray:
    """Subtract a least-squares fitted plane. NaNs are ignored."""
    ny, nx = z.shape
    yy, xx = np.mgrid[0:ny, 0:nx].astype(np.float32)
    mask = np.isfinite(z)
    if mask.sum() < 3:
        return z - np.nanmean(z) if mask.any() else z
    a = np.column_stack([xx[mask].ravel(), yy[mask].ravel(), np.ones(mask.sum(), dtype=np.float32)])
    b = z[mask].ravel()
    coef, *_ = np.linalg.lstsq(a, b, rcond=None)
    plane = coef[0] * xx + coef[1] * yy + coef[2]
    return z - plane


def render_preview(
    data: np.ndarray,
    out_path: Path | str,
    *,
    cmap: str = "viridis",
    dpi: int = 100,
    plane_subtract: bool = True,
    percentile_clip: float = 1.0,
    title: Optional[str] = None,
) -> Path:
    """Render a single 2D array to a PNG and return the written path."""
    if not _MPL_AVAILABLE:
        raise RuntimeError("matplotlib is required to render previews")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D array, got shape {arr.shape}")

    if plane_subtract:
        arr = _plane_subtract(arr)

    finite = arr[np.isfinite(arr)]
    if finite.size:
        lo, hi = np.percentile(finite, [percentile_clip, 100.0 - percentile_clip])
        if hi - lo < 1e-30:
            hi = lo + 1e-30
    else:
        lo, hi = 0.0, 1.0

    fig, ax = plt.subplots(figsize=(4, 4), dpi=dpi)
    ax.imshow(arr, cmap=cmap, vmin=lo, vmax=hi, origin="lower")
    ax.set_axis_off()
    if title:
        ax.set_title(title, fontsize=8)
    fig.tight_layout(pad=0.2)
    fig.savefig(out_path, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    return out_path
