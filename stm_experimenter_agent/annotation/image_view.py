"""Dynamic image rendering for the annotation UI."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import numpy as np

from ..data_collection.preview import prepare_view_array, render_arrays_png_bytes
from ..data_collection.sxm_parser import load_sxm


@dataclass(frozen=True)
class ImageViewSettings:
    channel: Optional[str] = None
    mode: str = "forward"
    orientation: str = "auto"
    cmap: str = "default"
    show_colorbar: bool = True
    plane_subtract: bool = True
    percentile_clip: float = 1.0
    vmin: Optional[float] = None
    vmax: Optional[float] = None


def settings_from_query(query: Dict[str, str]) -> ImageViewSettings:
    return ImageViewSettings(
        channel=(query.get("channel") or None),
        mode=(query.get("mode") or query.get("direction") or "forward"),
        orientation=query.get("orientation") or query.get("flip") or "auto",
        cmap=query.get("lut") or query.get("cmap") or "default",
        show_colorbar=_query_bool(query.get("show_colorbar"), default=True),
        plane_subtract=_query_bool(query.get("plane_subtract"), default=True),
        percentile_clip=_query_float(query.get("percentile_clip"), 1.0) or 0.0,
        vmin=_query_float(query.get("vmin"), None),
        vmax=_query_float(query.get("vmax"), None),
    )


def render_scan_image(sxm_path: Path | str, settings: ImageViewSettings) -> bytes:
    sxm = load_sxm(sxm_path)
    if not sxm.data:
        raise ValueError("scan has no channel image data")
    channel = _choose_channel(sxm.data, settings.channel)
    frames = sxm.data[channel]
    mode = settings.mode.lower().replace("-", "_")

    if mode in ("forward", "backward"):
        arr = _frame(frames, mode)
        prepared = prepare_view_array(
            arr,
            plane_subtract=settings.plane_subtract,
            orientation=settings.orientation,
            scan_dir=sxm.scan_dir,
        )
        title = f"{channel} {mode}"
        return render_arrays_png_bytes(
            [prepared],
            titles=[title],
            cmap=settings.cmap,
            show_colorbar=settings.show_colorbar,
            percentile_clip=settings.percentile_clip,
            vmin=settings.vmin,
            vmax=settings.vmax,
        )

    if mode == "side_by_side":
        forward, backward = _forward_backward(frames)
        arrays = [
            prepare_view_array(
                forward,
                plane_subtract=settings.plane_subtract,
                orientation=settings.orientation,
                scan_dir=sxm.scan_dir,
            ),
            prepare_view_array(
                backward,
                plane_subtract=settings.plane_subtract,
                orientation=settings.orientation,
                scan_dir=sxm.scan_dir,
            ),
        ]
        return render_arrays_png_bytes(
            arrays,
            titles=[f"{channel} forward", f"{channel} backward"],
            cmap=settings.cmap,
            show_colorbar=settings.show_colorbar,
            percentile_clip=settings.percentile_clip,
            vmin=settings.vmin,
            vmax=settings.vmax,
        )

    if mode == "difference":
        forward, backward = _forward_backward(frames)
        fwd = prepare_view_array(
            forward,
            plane_subtract=settings.plane_subtract,
            orientation=settings.orientation,
            scan_dir=sxm.scan_dir,
        )
        bwd = prepare_view_array(
            backward,
            plane_subtract=settings.plane_subtract,
            orientation=settings.orientation,
            scan_dir=sxm.scan_dir,
        )
        return render_arrays_png_bytes(
            [np.asarray(fwd, dtype=np.float32) - np.asarray(bwd, dtype=np.float32)],
            titles=[f"{channel} forward - backward"],
            cmap=settings.cmap,
            show_colorbar=settings.show_colorbar,
            percentile_clip=settings.percentile_clip,
            vmin=settings.vmin,
            vmax=settings.vmax,
        )

    raise ValueError("mode must be forward, backward, side_by_side, or difference")


def _choose_channel(data: Dict[str, Dict[str, np.ndarray]], requested: Optional[str]) -> str:
    if requested and requested in data:
        return requested
    for preferred in ("Z", "Current", "LI_Demod_1_X"):
        if preferred in data:
            return preferred
    return next(iter(data.keys()))


def _frame(frames: Dict[str, np.ndarray], direction: str) -> np.ndarray:
    if direction in frames:
        return frames[direction]
    raise ValueError(f"channel does not contain {direction} data")


def _forward_backward(frames: Dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    if "forward" not in frames or "backward" not in frames:
        raise ValueError("channel does not contain both forward and backward data")
    return frames["forward"], frames["backward"]


def _query_bool(value: Optional[str], *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _query_float(value: Optional[str], default: Optional[float]) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return default
    return float(value)