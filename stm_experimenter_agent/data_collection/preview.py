"""Preview image rendering for scans.

Generates a PNG suitable for the annotation UI. We deliberately stay
matplotlib-only to avoid pulling in PIL just for save_png.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np

try:  # matplotlib is a heavy import; keep it optional at import time.
    import matplotlib
    matplotlib.use("Agg", force=False)
    from matplotlib.colors import ListedColormap
    import matplotlib.pyplot as plt
    _MPL_AVAILABLE = True
except Exception:  # noqa: BLE001
    _MPL_AVAILABLE = False

_ASSETS_LUT_DIR = Path(__file__).resolve().parents[1] / "assets" / "luts"
_BUILTIN_CMAPS = ("default", "viridis", "gray", "inferno", "magma")


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


def available_luts() -> tuple[str, ...]:
    names = list(_BUILTIN_CMAPS)
    if _ASSETS_LUT_DIR.exists():
        for path in sorted(_ASSETS_LUT_DIR.glob("*.lut")):
            if path.stem not in names:
                names.append(path.stem)
    return tuple(names)


def _parse_lut_file(path: Path) -> list[tuple[float, float, float]]:
    colors: list[tuple[float, float, float]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        parts = line.replace(",", " ").split()
        if len(parts) == 1 and not colors:
            continue
        if len(parts) < 3:
            continue
        try:
            rgb = [float(parts[i]) for i in range(3)]
        except ValueError:
            continue
        if max(rgb) > 1.0:
            rgb = [min(255.0, max(0.0, v)) / 255.0 for v in rgb]
        colors.append((rgb[0], rgb[1], rgb[2]))
    if len(colors) < 2:
        raise ValueError(f"LUT file has too few RGB rows: {path}")
    return colors


def _resolve_cmap(name: str):
    cmap_name = (name or "default").strip()
    if cmap_name == "default":
        lut_path = _ASSETS_LUT_DIR / "default.lut"
        if lut_path.exists():
            return ListedColormap(_parse_lut_file(lut_path), name="default")
        return "viridis"
    lut_path = _ASSETS_LUT_DIR / f"{cmap_name}.lut"
    if lut_path.exists():
        return ListedColormap(_parse_lut_file(lut_path), name=cmap_name)
    return cmap_name if cmap_name in _BUILTIN_CMAPS else "viridis"


def _apply_orientation(
    arr: np.ndarray,
    *,
    orientation: str = "auto",
    scan_dir: Optional[str] = None,
) -> np.ndarray:
    mode = (orientation or "auto").lower()
    out = arr
    if mode == "auto":
        if (scan_dir or "").lower().startswith("down"):
            out = np.flipud(out)
    elif mode == "flip_y":
        out = np.flipud(out)
    elif mode == "flip_x":
        out = np.fliplr(out)
    elif mode == "rotate_90":
        out = np.rot90(out, 1)
    elif mode == "rotate_180":
        out = np.rot90(out, 2)
    elif mode == "rotate_270":
        out = np.rot90(out, 3)
    return np.ascontiguousarray(out)


def prepare_view_array(
    data: np.ndarray,
    *,
    plane_subtract: bool = True,
    orientation: str = "auto",
    scan_dir: Optional[str] = None,
) -> np.ndarray:
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D array, got shape {arr.shape}")
    if plane_subtract:
        arr = _plane_subtract(arr)
    return _apply_orientation(arr, orientation=orientation, scan_dir=scan_dir)


def _scale_limits(
    arrays: Sequence[np.ndarray],
    *,
    percentile_clip: float = 1.0,
    vmin: Optional[float] = None,
    vmax: Optional[float] = None,
) -> tuple[float, float]:
    finite_parts = [arr[np.isfinite(arr)] for arr in arrays]
    finite_parts = [part for part in finite_parts if part.size]
    if finite_parts:
        finite = np.concatenate(finite_parts)
        clip = min(49.0, max(0.0, float(percentile_clip)))
        auto_lo, auto_hi = np.percentile(finite, [clip, 100.0 - clip])
    else:
        auto_lo, auto_hi = 0.0, 1.0
    lo = auto_lo if vmin is None else float(vmin)
    hi = auto_hi if vmax is None else float(vmax)
    if hi - lo < 1e-30:
        hi = lo + 1e-30
    return float(lo), float(hi)


def render_arrays_png_bytes(
    arrays: Sequence[np.ndarray],
    *,
    titles: Optional[Sequence[str]] = None,
    cmap: str = "default",
    show_colorbar: bool = True,
    percentile_clip: float = 1.0,
    vmin: Optional[float] = None,
    vmax: Optional[float] = None,
    dpi: int = 120,
) -> bytes:
    if not _MPL_AVAILABLE:
        raise RuntimeError("matplotlib is required to render previews")
    if not arrays:
        raise ValueError("at least one image array is required")

    prepared = [np.asarray(arr, dtype=np.float32) for arr in arrays]
    lo, hi = _scale_limits(prepared, percentile_clip=percentile_clip, vmin=vmin, vmax=vmax)
    ncols = len(prepared)
    fig_width = max(3.2, 3.2 * ncols + (0.4 if show_colorbar else 0.0))
    fig, axes = plt.subplots(1, ncols, figsize=(fig_width, 3.4), dpi=dpi,
                             constrained_layout=True)
    axes_list = np.atleast_1d(axes).ravel().tolist()
    image = None
    resolved_cmap = _resolve_cmap(cmap)
    for idx, (ax, arr) in enumerate(zip(axes_list, prepared)):
        image = ax.imshow(arr, cmap=resolved_cmap, vmin=lo, vmax=hi, origin="lower")
        ax.set_axis_off()
        if titles and idx < len(titles):
            ax.set_title(titles[idx], fontsize=8)
    if show_colorbar and image is not None:
        fig.colorbar(image, ax=axes_list, shrink=0.78, fraction=0.046, pad=0.02)
    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    return buf.getvalue()


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
    ax.imshow(arr, cmap=_resolve_cmap(cmap), vmin=lo, vmax=hi, origin="lower")
    ax.set_axis_off()
    if title:
        ax.set_title(title, fontsize=8)
    fig.tight_layout(pad=0.2)
    fig.savefig(out_path, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    return out_path
