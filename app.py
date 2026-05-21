from __future__ import annotations

import io
import json
import math
import mimetypes
import os
import posixpath
from dataclasses import dataclass
from functools import lru_cache
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import numpy as np
import pydicom
from PIL import Image
from scipy.ndimage import convolve, label
from scipy.optimize import curve_fit


ROOT = Path(__file__).resolve().parent
DATA_ROOT = ROOT / "BME_lab_4"
STATIC_ROOT = ROOT / "static"

# Room-temperature triglyceride model for the items in the container. The
# frequency shifts are in ppm relative to water; relative amplitudes sum to one.
FAT_MODEL_PPM = np.array([0.5, -0.49, -2.04, -2.7, -3.5, -3.9], dtype=np.float64)
FAT_MODEL_AMPLITUDES = np.array([0.048, 0.039, 0.004, 0.128, 0.694, 0.087], dtype=np.float64)
FAT_MAP_DISPLAY_MAX = 1.0


@dataclass(frozen=True)
class ImageEntry:
    path: Path
    echo_time: float
    repetition_time: float | None
    instance_number: int
    slice_location: float


@dataclass
class Series:
    id: str
    label: str
    sequence_type: str
    fit_label: str
    folder: Path
    image_type: str
    rows: int
    cols: int
    echo_times: list[float]
    slice_locations: list[float]
    repetition_time: float | None
    flip_angle: float | None
    imaging_frequency_mhz: float | None
    entries_by_slice_echo: dict[tuple[int, int], ImageEntry]
    phase_folder: Path | None
    phase_image_type: str | None
    phase_entries_by_slice_echo: dict[tuple[int, int], ImageEntry]


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _norm_key(value: float) -> float:
    return round(float(value), 6)


def _dicom_dirs() -> list[Path]:
    if not DATA_ROOT.exists():
        return []
    return sorted(path for path in DATA_ROOT.glob("*/*/*") if path.is_dir())


def _sequence_type(folder_name: str, description: str) -> tuple[str, str] | None:
    name = f"{folder_name} {description}".lower()
    if "megre" in name:
        return "GRE", "T2*"
    if "se_multiecho" in name:
        return "SE", "T2"
    return None


def _is_magnitude(image_type: Any) -> bool:
    parts = [str(part).upper() for part in image_type] if image_type else []
    return "M" in parts and "P" not in parts


def _is_phase(image_type: Any) -> bool:
    parts = [str(part).upper() for part in image_type] if image_type else []
    return "P" in parts


def _entries_for_files(files: list[Path]) -> list[ImageEntry]:
    entries: list[ImageEntry] = []
    for path in files:
        ds = pydicom.dcmread(path, stop_before_pixels=True, force=True)
        entries.append(
            ImageEntry(
                path=path,
                echo_time=float(ds.EchoTime),
                repetition_time=_as_float(getattr(ds, "RepetitionTime", None)),
                instance_number=int(ds.InstanceNumber),
                slice_location=float(getattr(ds, "SliceLocation", 0.0)),
            )
        )
    return entries


def _index_entries(
    entries: list[ImageEntry],
    echo_times: list[float],
    slice_locations: list[float],
) -> dict[tuple[int, int], ImageEntry]:
    echo_index = {te: idx for idx, te in enumerate(echo_times)}
    slice_index = {loc: idx for idx, loc in enumerate(slice_locations)}
    by_key: dict[tuple[int, int], ImageEntry] = {}
    for entry in entries:
        key = (
            slice_index[_norm_key(entry.slice_location)],
            echo_index[_norm_key(entry.echo_time)],
        )
        by_key[key] = entry
    return by_key


def _matching_phase_series(
    magnitude_folder: Path,
    description: str,
    rows: int,
    cols: int,
    tr: float | None,
    flip: float | None,
    echo_times: list[float],
    slice_locations: list[float],
) -> tuple[Path | None, str | None, dict[tuple[int, int], ImageEntry]]:
    for candidate in sorted(magnitude_folder.parent.glob("MEGRE*")):
        if candidate == magnitude_folder or not candidate.is_dir():
            continue
        files = sorted(candidate.glob("*.IMA"))
        if not files:
            continue

        first = pydicom.dcmread(files[0], stop_before_pixels=True, force=True)
        if not _is_phase(getattr(first, "ImageType", [])):
            continue
        if str(getattr(first, "SeriesDescription", "")) != description:
            continue
        if int(first.Rows) != rows or int(first.Columns) != cols:
            continue
        if _as_float(getattr(first, "RepetitionTime", None)) != tr:
            continue
        if _as_float(getattr(first, "FlipAngle", None)) != flip:
            continue

        entries = _entries_for_files(files)
        candidate_echoes = sorted({_norm_key(entry.echo_time) for entry in entries})
        candidate_slices = sorted({_norm_key(entry.slice_location) for entry in entries})
        if candidate_echoes != echo_times or candidate_slices != slice_locations:
            continue

        image_type = "\\".join(str(part) for part in getattr(first, "ImageType", []))
        return candidate, image_type, _index_entries(entries, echo_times, slice_locations)

    return None, None, {}


def scan_series() -> dict[str, Series]:
    series: dict[str, Series] = {}

    for folder in _dicom_dirs():
        files = sorted(folder.glob("*.IMA"))
        if not files:
            continue

        first = pydicom.dcmread(files[0], stop_before_pixels=True, force=True)
        seq = _sequence_type(folder.name, str(getattr(first, "SeriesDescription", "")))
        if not seq or not _is_magnitude(getattr(first, "ImageType", [])):
            continue

        sequence_type, fit_label = seq
        rows = int(first.Rows)
        cols = int(first.Columns)
        image_type = "\\".join(str(part) for part in getattr(first, "ImageType", []))
        description = str(getattr(first, "SeriesDescription", folder.name))

        entries = _entries_for_files(files)

        echo_times = sorted({_norm_key(entry.echo_time) for entry in entries})
        slice_locations = sorted({_norm_key(entry.slice_location) for entry in entries})
        by_key = _index_entries(entries, echo_times, slice_locations)

        tr = _as_float(getattr(first, "RepetitionTime", None))
        flip = _as_float(getattr(first, "FlipAngle", None))
        imaging_frequency = _as_float(getattr(first, "ImagingFrequency", None))
        series_id = folder.name
        label = _human_label(sequence_type, description, tr, flip, echo_times, folder.name)
        phase_folder, phase_image_type, phase_by_key = _matching_phase_series(
            folder,
            description,
            rows,
            cols,
            tr,
            flip,
            echo_times,
            slice_locations,
        )

        series[series_id] = Series(
            id=series_id,
            label=label,
            sequence_type=sequence_type,
            fit_label=fit_label,
            folder=folder,
            image_type=image_type,
            rows=rows,
            cols=cols,
            echo_times=echo_times,
            slice_locations=slice_locations,
            repetition_time=tr,
            flip_angle=flip,
            imaging_frequency_mhz=imaging_frequency,
            entries_by_slice_echo=by_key,
            phase_folder=phase_folder,
            phase_image_type=phase_image_type,
            phase_entries_by_slice_echo=phase_by_key,
        )

    return dict(sorted(series.items(), key=lambda item: (item[1].sequence_type, item[1].label)))


def _human_label(
    sequence_type: str,
    description: str,
    tr: float | None,
    flip: float | None,
    echo_times: list[float],
    fallback: str,
) -> str:
    tr_part = f"TR {tr:g} ms" if tr is not None else "TR ?"
    te_part = f"TE {echo_times[0]:g}-{echo_times[-1]:g} ms" if echo_times else "TE ?"
    if sequence_type == "GRE":
        flip_part = f"FA {flip:g}" if flip is not None else "FA ?"
        return f"GRE {flip_part}, {tr_part}, {te_part}"
    if sequence_type == "SE":
        return f"SE {tr_part}, {te_part}"
    return description or fallback


SERIES = scan_series()


def _write_bytes(handler: SimpleHTTPRequestHandler, data: bytes) -> None:
    try:
        handler.wfile.write(data)
    except (BrokenPipeError, ConnectionResetError):
        return


def _json_response(handler: SimpleHTTPRequestHandler, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    _write_bytes(handler, data)


def _error(handler: SimpleHTTPRequestHandler, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
    _json_response(handler, {"error": message}, status)


def _read_pixels(path: Path) -> np.ndarray:
    ds = pydicom.dcmread(path, force=True)
    pixels = ds.pixel_array.astype(np.float64)
    slope = _as_float(getattr(ds, "RescaleSlope", None), 1.0) or 1.0
    intercept = _as_float(getattr(ds, "RescaleIntercept", None), 0.0) or 0.0
    return pixels * slope + intercept


def _display_window(pixels: np.ndarray) -> tuple[float, float] | None:
    finite = pixels[np.isfinite(pixels)]
    if finite.size == 0:
        return None

    low, high = np.percentile(finite, [1.0, 99.5])
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        low, high = float(finite.min()), float(finite.max())
    return (float(low), float(high)) if high > low else None


def _png_bytes(pixels: np.ndarray, window: tuple[float, float] | None = None) -> bytes:
    window = window or _display_window(pixels)
    if window is None:
        scaled = np.zeros(pixels.shape, dtype=np.uint8)
    else:
        low, high = window
        scaled = np.clip((pixels - low) / (high - low), 0.0, 1.0)
        scaled = (scaled * 255.0).astype(np.uint8)

    image = Image.fromarray(scaled)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


@lru_cache(maxsize=256)
def _fixed_echo_window(series_id: str, slice_idx: int) -> tuple[float, float] | None:
    series = SERIES[series_id]
    frames = [
        _read_pixels(entry.path)
        for echo_idx in range(len(series.echo_times))
        if (entry := series.entries_by_slice_echo.get((slice_idx, echo_idx))) is not None
    ]
    return _display_window(np.stack(frames, axis=0)) if frames else None


def _heatmap_png_bytes(values: np.ndarray) -> tuple[bytes, float | None, float | None, int]:
    finite = values[np.isfinite(values) & (values > 0)]
    if finite.size == 0:
        image = Image.fromarray(np.zeros((*values.shape, 3), dtype=np.uint8))
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue(), None, None, 0

    low, high = np.percentile(finite, [2.0, 98.0])
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        low, high = float(finite.min()), float(finite.max())
    if high <= low:
        high = low + 1.0

    normalized = np.clip((values - low) / (high - low), 0.0, 1.0)
    normalized = np.where(np.isfinite(normalized), normalized, 0.0)
    stops = np.array(
        [
            [11, 20, 28],
            [16, 72, 101],
            [0, 124, 137],
            [239, 201, 70],
            [198, 75, 60],
        ],
        dtype=np.float64,
    )
    segment = np.clip(normalized * (len(stops) - 1), 0.0, len(stops) - 1 - 1e-9)
    idx = np.floor(segment).astype(np.intp)
    amount = (segment - idx)[..., None]
    colors = stops[idx] * (1.0 - amount) + stops[idx + 1] * amount
    colors[~np.isfinite(values) | (values <= 0)] = 0

    image = Image.fromarray(colors.astype(np.uint8))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue(), float(low), float(high), int(finite.size)


def _fraction_map_png_bytes(values: np.ndarray, display_max: float = 1.0) -> tuple[bytes, int]:
    valid = np.isfinite(values) & (values >= 0.0) & (values <= 1.0)
    normalized = np.clip(np.where(valid, values, 0.0) / max(display_max, 1e-9), 0.0, 1.0)
    stops = np.array(
        [
            [11, 20, 28],
            [16, 72, 101],
            [0, 124, 137],
            [239, 201, 70],
            [198, 75, 60],
        ],
        dtype=np.float64,
    )
    segment = np.clip(normalized * (len(stops) - 1), 0.0, len(stops) - 1 - 1e-9)
    idx = np.floor(segment).astype(np.intp)
    amount = (segment - idx)[..., None]
    colors = stops[idx] * (1.0 - amount) + stops[idx + 1] * amount
    colors[~valid] = 0

    image = Image.fromarray(colors.astype(np.uint8))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue(), int(np.count_nonzero(valid))


def _series_payload() -> list[dict[str, Any]]:
    return [
        {
            "id": s.id,
            "label": s.label,
            "sequenceType": s.sequence_type,
            "fitLabel": s.fit_label,
            "folder": str(s.folder.relative_to(ROOT)),
            "rows": s.rows,
            "cols": s.cols,
            "echoTimes": s.echo_times,
            "sliceLocations": s.slice_locations,
            "repetitionTime": s.repetition_time,
            "flipAngle": s.flip_angle,
            "imagingFrequencyMhz": s.imaging_frequency_mhz,
            "hasPhaseImages": bool(s.phase_entries_by_slice_echo),
            "phaseFolder": str(s.phase_folder.relative_to(ROOT)) if s.phase_folder else None,
            "phaseImageType": s.phase_image_type,
            "fatSpectrum": (
                {
                    "ppm": FAT_MODEL_PPM.tolist(),
                    "frequencyHz": (FAT_MODEL_PPM * (s.imaging_frequency_mhz or 123.229482)).tolist(),
                    "amplitudes": FAT_MODEL_AMPLITUDES.tolist(),
                }
                if s.sequence_type == "GRE"
                else None
            ),
            "imageType": s.image_type,
            "missingImages": len(s.echo_times) * len(s.slice_locations) - len(s.entries_by_slice_echo),
        }
        for s in SERIES.values()
    ]


def _get_required(query: dict[str, list[str]], name: str) -> str:
    values = query.get(name)
    if not values:
        raise ValueError(f"missing query parameter: {name}")
    return values[0]


def _entry_for_query(query: dict[str, list[str]]) -> tuple[Series, ImageEntry]:
    series_id = _get_required(query, "series")
    try:
        series = SERIES[series_id]
    except KeyError as exc:
        raise ValueError(f"unknown series: {series_id}") from exc

    slice_idx = int(_get_required(query, "slice"))
    echo_idx = int(_get_required(query, "echo"))
    try:
        entry = series.entries_by_slice_echo[(slice_idx, echo_idx)]
    except KeyError as exc:
        raise ValueError(f"missing image for slice {slice_idx}, echo {echo_idx}") from exc
    return series, entry


def _fit_quality_warnings(
    fit_label: str,
    echo_times: np.ndarray,
    signals: np.ndarray,
    tau: float,
    r2: float,
) -> list[str]:
    warnings: list[str] = []
    echo_span = float(np.max(echo_times) - np.min(echo_times))
    signal_max = float(np.max(signals)) if signals.size else 0.0
    signal_min = float(np.min(signals)) if signals.size else 0.0
    dynamic_range = (signal_max - signal_min) / signal_max if signal_max > 0 else 0.0

    if fit_label == "T2*" and echo_span > 0 and tau > 3.0 * echo_span:
        warnings.append(
            "Estimated T2* is much longer than the acquired TE range, so the value is weakly constrained."
        )
    if dynamic_range < 0.15:
        warnings.append("Signal changes by less than 15% across the fit range.")
    if r2 < 0.9:
        warnings.append("Fit residuals are high for a single-exponential model.")
    return warnings


def _fit_decay(
    echo_times: np.ndarray,
    signals: np.ndarray,
    fit_label: str,
    selected_mask: np.ndarray | None = None,
) -> dict[str, Any]:
    mask = np.isfinite(signals) & (signals > 0) & np.isfinite(echo_times)
    if selected_mask is not None:
        mask &= selected_mask
    used_echoes = echo_times[mask]
    used_signals = signals[mask]
    if used_echoes.size < 2:
        return {
            "valueMs": None,
            "s0": None,
            "offset": None,
            "r2": None,
            "method": None,
            "fitSignals": [],
            "used": mask.tolist(),
            "warnings": [],
            "error": "Need at least two positive signal values for a log-linear fit.",
        }

    if fit_label == "T2" and used_echoes.size >= 4:
        return _fit_decay_with_offset(echo_times, signals, mask, fit_label)

    slope, intercept = np.polyfit(used_echoes, np.log(used_signals), 1)
    if slope >= 0:
        return {
            "valueMs": None,
            "s0": float(np.exp(intercept)),
            "offset": 0.0,
            "r2": None,
            "method": "log-linear zero-offset",
            "fitSignals": [],
            "used": mask.tolist(),
            "warnings": [],
            "error": "Selected signal does not decay monotonically enough for a positive time constant.",
        }

    tau = -1.0 / slope
    s0 = float(np.exp(intercept))
    predicted_log = slope * used_echoes + intercept
    ss_res = float(np.sum((np.log(used_signals) - predicted_log) ** 2))
    ss_tot = float(np.sum((np.log(used_signals) - np.mean(np.log(used_signals))) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    fit_signals = s0 * np.exp(-echo_times / tau)
    warnings = _fit_quality_warnings(fit_label, used_echoes, used_signals, float(tau), float(r2))

    return {
        "valueMs": float(tau),
        "s0": s0,
        "offset": 0.0,
        "r2": float(r2),
        "method": "log-linear zero-offset",
        "fitSignals": fit_signals.tolist(),
        "used": mask.tolist(),
        "warnings": warnings,
        "error": None,
    }


def _fit_decay_with_offset(
    echo_times: np.ndarray,
    signals: np.ndarray,
    mask: np.ndarray,
    fit_label: str,
) -> dict[str, Any]:
    used_echoes = echo_times[mask]
    used_signals = signals[mask]
    offset_upper = max(0.0, float(np.min(used_signals)) * 0.95)
    offset0 = min(float(np.median(used_signals[-max(3, used_signals.size // 5) :])), offset_upper)
    amplitude0 = max(float(used_signals[0] - offset0), 1.0)

    corrected = used_signals - offset0
    corrected_mask = corrected > 0
    if np.count_nonzero(corrected_mask) >= 2:
        slope, intercept = np.polyfit(used_echoes[corrected_mask], np.log(corrected[corrected_mask]), 1)
        tau0 = float(-1.0 / slope) if slope < 0 else 80.0
        amplitude0 = max(float(np.exp(intercept)), 1.0)
    else:
        tau0 = 80.0

    tau0 = min(max(tau0, 1.0), 5000.0)

    def model(te: np.ndarray, amplitude: float, tau: float, offset: float) -> np.ndarray:
        return amplitude * np.exp(-te / tau) + offset

    try:
        params, _ = curve_fit(
            model,
            used_echoes,
            used_signals,
            p0=[amplitude0, tau0, offset0],
            bounds=([0.0, 1.0, 0.0], [np.inf, 5000.0, offset_upper]),
            maxfev=20000,
        )
    except Exception as exc:
        fallback = _fit_decay(echo_times, signals, "T2*", mask)
        fallback["method"] = "log-linear zero-offset fallback"
        fallback["warnings"] = [f"Offset fit failed: {exc}"] + fallback.get("warnings", [])
        return fallback

    amplitude, tau, offset = [float(value) for value in params]
    predicted = model(used_echoes, amplitude, tau, offset)
    ss_res = float(np.sum((used_signals - predicted) ** 2))
    ss_tot = float(np.sum((used_signals - np.mean(used_signals)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    fit_signals = model(echo_times, amplitude, tau, offset)
    warnings = _fit_quality_warnings(fit_label, used_echoes, used_signals, tau, r2)

    return {
        "valueMs": tau,
        "s0": amplitude,
        "offset": offset,
        "r2": float(r2),
        "method": "nonlinear offset",
        "fitSignals": fit_signals.tolist(),
        "used": mask.tolist(),
        "warnings": warnings,
        "error": None,
    }


def _roi_values(pixels: np.ndarray, x: int, y: int, radius: int) -> np.ndarray:
    rows, cols = pixels.shape
    x = min(max(x, 0), cols - 1)
    y = min(max(y, 0), rows - 1)
    radius = max(0, int(radius))
    if radius == 0:
        return np.asarray([pixels[y, x]])

    x0, x1 = max(0, x - radius), min(cols - 1, x + radius)
    y0, y1 = max(0, y - radius), min(rows - 1, y + radius)
    yy, xx = np.ogrid[y0 : y1 + 1, x0 : x1 + 1]
    mask = (xx - x) ** 2 + (yy - y) ** 2 <= radius**2
    return pixels[y0 : y1 + 1, x0 : x1 + 1][mask]


def _roi_mean(pixels: np.ndarray, x: int, y: int, radius: int) -> float:
    return float(np.mean(_roi_values(pixels, x, y, radius)))


def _clamp_echo_range(series: Series, echo_start: int, echo_end: int) -> tuple[int, int]:
    max_echo = len(series.echo_times) - 1
    echo_start = min(max(echo_start, 0), max_echo)
    echo_end = min(max(echo_end, 0), max_echo)
    if echo_start > echo_end:
        echo_start, echo_end = echo_end, echo_start
    return echo_start, echo_end


def _roi_kernel(radius: int) -> np.ndarray:
    radius = max(0, int(radius))
    if radius == 0:
        return np.ones((1, 1), dtype=np.float64)
    yy, xx = np.ogrid[-radius : radius + 1, -radius : radius + 1]
    return ((xx * xx + yy * yy) <= radius * radius).astype(np.float64)


def _roi_mean_stack(stack: np.ndarray, radius: int) -> np.ndarray:
    radius = max(0, int(radius))
    if radius == 0:
        return stack

    kernel = _roi_kernel(radius)
    weights = convolve(np.ones(stack.shape[1:], dtype=np.float64), kernel, mode="constant", cval=0.0)
    return np.stack(
        [convolve(frame, kernel, mode="constant", cval=0.0) / weights for frame in stack],
        axis=0,
    )


def _signal_stack(series: Series, slice_idx: int, echo_start: int, echo_end: int, radius: int) -> tuple[np.ndarray, np.ndarray]:
    frames: list[np.ndarray] = []
    echo_times: list[float] = []
    for echo_idx in range(echo_start, echo_end + 1):
        entry = series.entries_by_slice_echo.get((slice_idx, echo_idx))
        if entry is None:
            frames.append(np.full((series.rows, series.cols), np.nan, dtype=np.float64))
        else:
            frames.append(_read_pixels(entry.path))
        echo_times.append(series.echo_times[echo_idx])
    return np.array(echo_times, dtype=np.float64), _roi_mean_stack(np.stack(frames, axis=0), radius)


def _foreground_mask(reference: np.ndarray, signal_fraction: float = 0.0) -> np.ndarray:
    rows, cols = reference.shape
    border = max(4, min(rows, cols) // 10)
    corners = np.concatenate(
        [
            reference[:border, :border].ravel(),
            reference[:border, -border:].ravel(),
            reference[-border:, :border].ravel(),
            reference[-border:, -border:].ravel(),
        ]
    )
    corners = corners[np.isfinite(corners)]
    finite = reference[np.isfinite(reference)]
    if corners.size:
        median = float(np.median(corners))
        sigma = 1.4826 * float(np.median(np.abs(corners - median)))
        floor = max(median + 8.0 * sigma, float(np.percentile(corners, 99.0)) * 2.0)
        if signal_fraction > 0.0 and finite.size:
            floor = max(floor, signal_fraction * float(np.percentile(finite, 99.5)))
        return reference > floor
    return np.ones(reference.shape, dtype=bool)


def _map_valid_signals(signals: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    positive = np.all(np.isfinite(signals) & (signals > 0), axis=0)
    signal_max = np.nanmax(signals, axis=0)
    signal_min = np.nanmin(signals, axis=0)
    dynamic = np.zeros(signals.shape[1:], dtype=np.float64)
    np.divide(signal_max - signal_min, signal_max, out=dynamic, where=signal_max > 0)

    foreground = _foreground_mask(signal_max)
    return positive, dynamic, foreground


def _remove_small_components(mask: np.ndarray, minimum_size: int) -> np.ndarray:
    components, count = label(mask)
    if count == 0:
        return mask

    sizes = np.bincount(components.ravel())
    keep = sizes >= minimum_size
    keep[0] = False
    return keep[components]


def _log_linear_time_map(echo_times: np.ndarray, signals: np.ndarray) -> np.ndarray:
    result = np.full(signals.shape[1:], np.nan, dtype=np.float64)
    if echo_times.size < 2:
        return result

    valid, dynamic, foreground = _map_valid_signals(signals)
    centered = echo_times - np.mean(echo_times)
    denominator = float(np.sum(centered * centered))
    if denominator <= 0:
        return result

    safe_signals = np.where(valid[None, ...], signals, 1.0)
    slope = np.sum(centered[:, None, None] * np.log(safe_signals), axis=0) / denominator
    decay = valid & foreground & (dynamic >= 0.15) & np.isfinite(slope) & (slope < 0)
    result[decay] = -1.0 / slope[decay]
    return result


def _offset_time_map(echo_times: np.ndarray, signals: np.ndarray) -> np.ndarray:
    if echo_times.size < 4:
        return _log_linear_time_map(echo_times, signals)

    rows, cols = signals.shape[1:]
    flat = signals.reshape(signals.shape[0], rows * cols)
    valid, dynamic, _ = _map_valid_signals(signals)
    foreground = _foreground_mask(signals[0], signal_fraction=0.03)
    foreground = _remove_small_components(foreground, max(32, foreground.size // 256))
    valid_flat = (valid & foreground & (dynamic >= 0.15)).ravel()
    result = np.full(rows * cols, np.nan, dtype=np.float64)
    if not np.any(valid_flat):
        return result.reshape(rows, cols)

    y = flat[:, valid_flat]
    count = float(echo_times.size)
    sum_y = np.sum(y, axis=0)
    sum_y2 = np.sum(y * y, axis=0)
    min_y = np.min(y, axis=0)
    ss_tot = np.sum((y - np.mean(y, axis=0)) ** 2, axis=0)
    best_sse = np.full(y.shape[1], np.inf, dtype=np.float64)
    best_tau = np.full(y.shape[1], np.nan, dtype=np.float64)

    for tau in np.geomspace(1.0, 5000.0, 220):
        basis = np.exp(-echo_times / tau)
        basis_sum = float(np.sum(basis))
        basis_sq_sum = float(np.sum(basis * basis))
        basis_y = basis @ y
        det = count * basis_sq_sum - basis_sum * basis_sum
        if det <= 0:
            continue

        offset = (basis_sq_sum * sum_y - basis_sum * basis_y) / det
        offset = np.clip(offset, 0.0, 0.95 * min_y)
        amplitude = np.maximum((basis_y - offset * basis_sum) / basis_sq_sum, 0.0)
        sse = (
            sum_y2
            - 2.0 * amplitude * basis_y
            - 2.0 * offset * sum_y
            + amplitude * amplitude * basis_sq_sum
            + 2.0 * amplitude * offset * basis_sum
            + offset * offset * count
        )
        improved = (amplitude > 0) & np.isfinite(sse) & (sse < best_sse)
        best_sse[improved] = sse[improved]
        best_tau[improved] = tau

    map_quality = np.zeros_like(best_sse)
    np.divide(best_sse, ss_tot, out=map_quality, where=ss_tot > 0)
    accepted = np.isfinite(best_tau) & (ss_tot > 0) & (map_quality <= 0.4)
    selected = np.flatnonzero(valid_flat)
    result[selected[accepted]] = best_tau[accepted]
    return result.reshape(rows, cols)


@lru_cache(maxsize=48)
def _time_map_result(
    series_id: str,
    slice_idx: int,
    echo_start: int,
    echo_end: int,
    radius: int,
) -> tuple[bytes, float | None, float | None, int, str]:
    series = SERIES[series_id]
    echo_times, signals = _signal_stack(series, slice_idx, echo_start, echo_end, radius)
    if series.fit_label == "T2":
        time_map = _offset_time_map(echo_times, signals)
        method = "offset grid"
    else:
        time_map = _log_linear_time_map(echo_times, signals)
        method = "log-linear"
    data, low, high, valid_count = _heatmap_png_bytes(time_map)
    return data, low, high, valid_count, method


def _fat_spectrum(echo_times: np.ndarray, imaging_frequency_mhz: float) -> np.ndarray:
    echo_seconds = echo_times * 1e-3
    frequencies_hz = FAT_MODEL_PPM * imaging_frequency_mhz
    phases = np.exp(2j * np.pi * frequencies_hz[:, None] * echo_seconds[None, :])
    return FAT_MODEL_AMPLITUDES @ phases


def _fat_peak_contributions(
    fat_fraction: float,
    amplitude: float,
    imaging_frequency_mhz: float,
) -> list[dict[str, float | int]]:
    frequencies_hz = FAT_MODEL_PPM * imaging_frequency_mhz
    return [
        {
            "peak": idx + 1,
            "ppm": float(ppm),
            "frequencyHz": float(frequency),
            "modelAmplitude": float(model_amplitude),
            "fractionOfFat": float(model_amplitude),
            "fractionOfTotal": float(fat_fraction * model_amplitude),
            "signal0": float(amplitude * fat_fraction * model_amplitude),
        }
        for idx, (ppm, frequency, model_amplitude) in enumerate(
            zip(FAT_MODEL_PPM, frequencies_hz, FAT_MODEL_AMPLITUDES)
        )
    ]


def _fat_fraction_map(
    echo_times: np.ndarray,
    signals: np.ndarray,
    imaging_frequency_mhz: float,
) -> np.ndarray:
    result = np.full(signals.shape[1:], np.nan, dtype=np.float64)
    if echo_times.size < 3:
        return result

    rows, cols = signals.shape[1:]
    flat = signals.reshape(signals.shape[0], rows * cols)
    valid, _, foreground = _map_valid_signals(signals)
    foreground = _remove_small_components(foreground, max(24, foreground.size // 1024))
    valid_flat = (valid & foreground).ravel()
    if not np.any(valid_flat):
        return result

    y = flat[:, valid_flat]
    sum_y2 = np.sum(y * y, axis=0)
    best_sse = np.full(y.shape[1], np.inf, dtype=np.float64)
    best_fraction = np.full(y.shape[1], np.nan, dtype=np.float64)
    fat_signal = _fat_spectrum(echo_times, imaging_frequency_mhz)
    fractions = np.linspace(0.0, 1.0, 81)
    mixtures = np.abs((1.0 - fractions[:, None]) + fractions[:, None] * fat_signal[None, :])

    for tau in np.geomspace(1.0, 5000.0, 56):
        basis = mixtures * np.exp(-echo_times[None, :] / tau)
        basis_norm = np.sum(basis * basis, axis=1)
        projections = basis @ y
        sse = sum_y2[None, :] - (projections * projections) / basis_norm[:, None]
        fraction_idx = np.argmin(sse, axis=0)
        columns = np.arange(y.shape[1])
        candidate_sse = sse[fraction_idx, columns]
        improved = np.isfinite(candidate_sse) & (candidate_sse < best_sse)
        best_sse[improved] = candidate_sse[improved]
        best_fraction[improved] = fractions[fraction_idx[improved]]

    normalized_sse = np.full_like(best_sse, np.inf)
    np.divide(best_sse, sum_y2, out=normalized_sse, where=sum_y2 > 0)
    accepted = np.isfinite(best_fraction) & (normalized_sse <= 0.08)
    selected = np.flatnonzero(valid_flat)
    result.ravel()[selected[accepted]] = best_fraction[accepted]
    return result


def _fat_corrected_t2star_map(
    echo_times: np.ndarray,
    signals: np.ndarray,
    imaging_frequency_mhz: float,
) -> np.ndarray:
    result = np.full(signals.shape[1:], np.nan, dtype=np.float64)
    if echo_times.size < 3:
        return result

    fraction_map = _fat_fraction_map(echo_times, signals, imaging_frequency_mhz)
    valid_fraction = np.isfinite(fraction_map) & (fraction_map >= 0.0) & (fraction_map <= 1.0)
    if not np.any(valid_fraction):
        return result

    fat_signal = _fat_spectrum(echo_times, imaging_frequency_mhz)
    mixture = np.abs(
        (1.0 - fraction_map[None, :, :])
        + fraction_map[None, :, :] * fat_signal[:, None, None]
    )
    valid = valid_fraction[None, :, :] & np.isfinite(signals) & (signals > 0) & (mixture > 1e-6)
    corrected = np.full_like(signals, np.nan, dtype=np.float64)
    np.divide(signals, mixture, out=corrected, where=valid)
    return _log_linear_time_map(echo_times, corrected)


def _fit_fat_fraction(
    echo_times: np.ndarray,
    signals: np.ndarray,
    imaging_frequency_mhz: float,
    selected_mask: np.ndarray,
) -> dict[str, Any]:
    mask = np.isfinite(echo_times) & np.isfinite(signals) & (signals > 0) & selected_mask
    used_echoes = echo_times[mask]
    used_signals = signals[mask]
    if used_echoes.size < 3:
        return {
            "fatFraction": None,
            "waterFraction": None,
            "amplitude": None,
            "waterAmplitude": None,
            "fatAmplitude": None,
            "t2starMs": None,
            "fieldHz": None,
            "r2": None,
            "method": "magnitude W/F grid",
            "fitSignals": [],
            "waterSignals": [],
            "fatSignals": [],
            "waterContribution": None,
            "peakContributions": [],
            "used": mask.tolist(),
            "error": "Need at least three selected GRE echoes for a magnitude water-fat fit.",
        }

    best_sse = math.inf
    best_fraction = float("nan")
    best_tau = float("nan")
    best_amplitude = float("nan")
    sum_y2 = float(np.sum(used_signals * used_signals))
    fat_signal = _fat_spectrum(used_echoes, imaging_frequency_mhz)
    fractions = np.linspace(0.0, 1.0, 101)
    mixtures = np.abs((1.0 - fractions[:, None]) + fractions[:, None] * fat_signal[None, :])

    for tau in np.geomspace(1.0, 5000.0, 220):
        basis = mixtures * np.exp(-used_echoes[None, :] / tau)
        basis_norm = np.sum(basis * basis, axis=1)
        projections = basis @ used_signals
        amplitudes = np.maximum(projections / basis_norm, 0.0)
        sse = sum_y2 - 2.0 * amplitudes * projections + amplitudes * amplitudes * basis_norm
        idx = int(np.argmin(sse))
        if np.isfinite(sse[idx]) and sse[idx] < best_sse:
            best_sse = float(sse[idx])
            best_fraction = float(fractions[idx])
            best_tau = float(tau)
            best_amplitude = float(amplitudes[idx])

    if not np.isfinite(best_fraction):
        return {
            "fatFraction": None,
            "waterFraction": None,
            "amplitude": None,
            "waterAmplitude": None,
            "fatAmplitude": None,
            "t2starMs": None,
            "fieldHz": None,
            "r2": None,
            "method": "magnitude W/F grid",
            "fitSignals": [],
            "waterSignals": [],
            "fatSignals": [],
            "waterContribution": None,
            "peakContributions": [],
            "used": mask.tolist(),
            "error": "Magnitude water-fat fit failed.",
        }

    fat_all = _fat_spectrum(echo_times, imaging_frequency_mhz)
    decay = np.exp(-echo_times / best_tau)
    water_amplitude = best_amplitude * (1.0 - best_fraction)
    fat_amplitude = best_amplitude * best_fraction
    fit_signals = np.abs((water_amplitude + fat_amplitude * fat_all) * decay)
    water_signals = water_amplitude * decay
    fat_signals = fat_amplitude * np.abs(fat_all) * decay
    predicted = fit_signals[mask]
    ss_res = float(np.sum((used_signals - predicted) ** 2))
    ss_tot = float(np.sum((used_signals - np.mean(used_signals)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0

    return {
        "fatFraction": best_fraction,
        "waterFraction": 1.0 - best_fraction,
        "amplitude": best_amplitude,
        "waterAmplitude": water_amplitude,
        "fatAmplitude": fat_amplitude,
        "t2starMs": best_tau,
        "fieldHz": None,
        "r2": float(r2),
        "method": "magnitude W/F grid",
        "fitSignals": fit_signals.tolist(),
        "waterSignals": water_signals.tolist(),
        "fatSignals": fat_signals.tolist(),
        "waterContribution": {
            "fractionOfTotal": 1.0 - best_fraction,
            "signal0": water_amplitude,
        },
        "peakContributions": _fat_peak_contributions(best_fraction, best_amplitude, imaging_frequency_mhz),
        "used": mask.tolist(),
        "error": None,
    }


def _fit_fat_corrected_decay(
    echo_times: np.ndarray,
    signals: np.ndarray,
    imaging_frequency_mhz: float,
    selected_mask: np.ndarray,
) -> dict[str, Any]:
    fat_fit = _fit_fat_fraction(echo_times, signals, imaging_frequency_mhz, selected_mask)
    fat_fraction = fat_fit.get("fatFraction")
    if fat_fraction is None:
        return {
            "valueMs": None,
            "s0": None,
            "offset": None,
            "r2": None,
            "method": "fat-corrected log-linear",
            "fitSignals": [],
            "correctedSignals": [],
            "used": fat_fit.get("used", selected_mask.tolist()),
            "warnings": [],
            "fatFraction": None,
            "error": fat_fit.get("error") or "Fat correction failed.",
        }

    fat_signal = _fat_spectrum(echo_times, imaging_frequency_mhz)
    mixture = np.abs((1.0 - fat_fraction) + fat_fraction * fat_signal)
    corrected = np.full_like(signals, np.nan, dtype=np.float64)
    valid = np.isfinite(signals) & (signals > 0) & np.isfinite(mixture) & (mixture > 1e-6)
    np.divide(signals, mixture, out=corrected, where=valid)
    corrected_fit = _fit_decay(echo_times, corrected, "T2*", selected_mask)
    corrected_fit["method"] = "fat-corrected log-linear"
    corrected_fit["correctedSignals"] = corrected.tolist()
    if corrected_fit.get("fitSignals"):
        fit_signals = np.array(corrected_fit["fitSignals"], dtype=np.float64)
        corrected_fit["rawFitSignals"] = (fit_signals * mixture).tolist()
    else:
        corrected_fit["rawFitSignals"] = []
    corrected_fit["fatFraction"] = fat_fraction
    return corrected_fit


@lru_cache(maxsize=48)
def _fat_map_result(
    series_id: str,
    slice_idx: int,
    echo_start: int,
    echo_end: int,
    radius: int,
) -> tuple[bytes, int, str]:
    series = SERIES[series_id]
    echo_times, signals = _signal_stack(series, slice_idx, echo_start, echo_end, radius)
    imaging_frequency = series.imaging_frequency_mhz or 123.229482
    fraction_map = _fat_fraction_map(echo_times, signals, imaging_frequency)
    method = "magnitude W/F grid"
    data, valid_count = _fraction_map_png_bytes(fraction_map, FAT_MAP_DISPLAY_MAX)
    return data, valid_count, method


@lru_cache(maxsize=48)
def _fat_corrected_map_result(
    series_id: str,
    slice_idx: int,
    echo_start: int,
    echo_end: int,
    radius: int,
) -> tuple[bytes, float | None, float | None, int, str]:
    series = SERIES[series_id]
    echo_times, signals = _signal_stack(series, slice_idx, echo_start, echo_end, radius)
    imaging_frequency = series.imaging_frequency_mhz or 123.229482
    corrected_map = _fat_corrected_t2star_map(echo_times, signals, imaging_frequency)
    data, low, high, valid_count = _heatmap_png_bytes(corrected_map)
    return data, low, high, valid_count, "fat-corrected log-linear"


def _handle_image(handler: SimpleHTTPRequestHandler, query: dict[str, list[str]]) -> None:
    try:
        series, entry = _entry_for_query(query)
        slice_idx = int(_get_required(query, "slice"))
    except ValueError as exc:
        _error(handler, str(exc))
        return

    auto_window = query.get("autoWindow", ["0"])[0].lower() in {"1", "true", "yes"}
    window = None if auto_window else _fixed_echo_window(series.id, slice_idx)
    data = _png_bytes(_read_pixels(entry.path), window)
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", "image/png")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    _write_bytes(handler, data)


def _handle_map(handler: SimpleHTTPRequestHandler, query: dict[str, list[str]]) -> None:
    try:
        series_id = _get_required(query, "series")
        series = SERIES[series_id]
        slice_idx = int(_get_required(query, "slice"))
        radius = max(0, int(round(float(query.get("radius", ["0"])[0]))))
        echo_start = int(round(float(query.get("echoStart", ["0"])[0])))
        echo_end = int(round(float(query.get("echoEnd", [str(len(series.echo_times) - 1)])[0])))
    except (ValueError, KeyError) as exc:
        _error(handler, str(exc))
        return

    echo_start, echo_end = _clamp_echo_range(series, echo_start, echo_end)
    if not 0 <= slice_idx < len(series.slice_locations):
        _error(handler, f"unknown slice: {slice_idx}")
        return

    data, low, high, valid_count, method = _time_map_result(
        series_id,
        slice_idx,
        echo_start,
        echo_end,
        radius,
    )
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", "image/png")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Map-Fit-Label", series.fit_label)
    handler.send_header("X-Map-Method", method)
    handler.send_header("X-Map-Low-Ms", "" if low is None else f"{low:.6g}")
    handler.send_header("X-Map-High-Ms", "" if high is None else f"{high:.6g}")
    handler.send_header("X-Map-Valid-Pixels", str(valid_count))
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    _write_bytes(handler, data)


def _handle_fat_map(handler: SimpleHTTPRequestHandler, query: dict[str, list[str]]) -> None:
    try:
        series_id = _get_required(query, "series")
        series = SERIES[series_id]
        slice_idx = int(_get_required(query, "slice"))
        radius = max(0, int(round(float(query.get("radius", ["0"])[0]))))
        echo_start = int(round(float(query.get("echoStart", ["0"])[0])))
        echo_end = int(round(float(query.get("echoEnd", [str(len(series.echo_times) - 1)])[0])))
    except (ValueError, KeyError) as exc:
        _error(handler, str(exc))
        return

    if series.sequence_type != "GRE":
        _error(handler, "water-fat map requires a GRE series")
        return

    echo_start, echo_end = _clamp_echo_range(series, echo_start, echo_end)
    if not 0 <= slice_idx < len(series.slice_locations):
        _error(handler, f"unknown slice: {slice_idx}")
        return

    try:
        data, valid_count, method = _fat_map_result(
            series_id,
            slice_idx,
            echo_start,
            echo_end,
            radius,
        )
    except ValueError as exc:
        _error(handler, str(exc))
        return
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", "image/png")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Fat-Method", method)
    handler.send_header("X-Fat-Valid-Pixels", str(valid_count))
    handler.send_header("X-Fat-Display-Max", f"{FAT_MAP_DISPLAY_MAX:.6g}")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    _write_bytes(handler, data)


def _handle_fat_corrected_map(handler: SimpleHTTPRequestHandler, query: dict[str, list[str]]) -> None:
    try:
        series_id = _get_required(query, "series")
        series = SERIES[series_id]
        slice_idx = int(_get_required(query, "slice"))
        radius = max(0, int(round(float(query.get("radius", ["0"])[0]))))
        echo_start = int(round(float(query.get("echoStart", ["0"])[0])))
        echo_end = int(round(float(query.get("echoEnd", [str(len(series.echo_times) - 1)])[0])))
    except (ValueError, KeyError) as exc:
        _error(handler, str(exc))
        return

    if series.sequence_type != "GRE":
        _error(handler, "fat-corrected T2* map requires a GRE series")
        return

    echo_start, echo_end = _clamp_echo_range(series, echo_start, echo_end)
    if not 0 <= slice_idx < len(series.slice_locations):
        _error(handler, f"unknown slice: {slice_idx}")
        return

    data, low, high, valid_count, method = _fat_corrected_map_result(
        series_id,
        slice_idx,
        echo_start,
        echo_end,
        radius,
    )
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", "image/png")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Corrected-Map-Method", method)
    handler.send_header("X-Corrected-Map-Low-Ms", "" if low is None else f"{low:.6g}")
    handler.send_header("X-Corrected-Map-High-Ms", "" if high is None else f"{high:.6g}")
    handler.send_header("X-Corrected-Map-Valid-Pixels", str(valid_count))
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    _write_bytes(handler, data)


def _handle_fat_fit(handler: SimpleHTTPRequestHandler, query: dict[str, list[str]]) -> None:
    try:
        series_id = _get_required(query, "series")
        series = SERIES[series_id]
        slice_idx = int(_get_required(query, "slice"))
        x = int(round(float(_get_required(query, "x"))))
        y = int(round(float(_get_required(query, "y"))))
        radius = int(round(float(query.get("radius", ["0"])[0])))
        echo_start = int(round(float(query.get("echoStart", ["0"])[0])))
        echo_end = int(round(float(query.get("echoEnd", [str(len(series.echo_times) - 1)])[0])))
    except (ValueError, KeyError) as exc:
        _error(handler, str(exc))
        return

    if series.sequence_type != "GRE":
        _error(handler, "water-fat fit requires a GRE series")
        return

    echo_start, echo_end = _clamp_echo_range(series, echo_start, echo_end)
    signals: list[float] = []
    for echo_idx in range(len(series.echo_times)):
        entry = series.entries_by_slice_echo.get((slice_idx, echo_idx))
        if entry is None:
            signals.append(float("nan"))
            continue
        magnitude = _read_pixels(entry.path)
        signals.append(_roi_mean(magnitude, x, y, radius))

    echo_times = np.array(series.echo_times, dtype=np.float64)
    signal_values = np.array(signals, dtype=np.float64)
    selected_mask = np.zeros(len(series.echo_times), dtype=bool)
    selected_mask[echo_start : echo_end + 1] = True
    fit = _fit_fat_fraction(
        echo_times,
        signal_values,
        series.imaging_frequency_mhz or 123.229482,
        selected_mask,
    )
    _json_response(
        handler,
        {
            "series": series_id,
            "x": x,
            "y": y,
            "slice": slice_idx,
            "radius": radius,
            "echoStart": echo_start,
            "echoEnd": echo_end,
            "echoTimes": series.echo_times,
            "signals": signals,
            "hasPhaseImages": bool(series.phase_entries_by_slice_echo),
            "phaseSignals": [],
            "fit": fit,
        },
    )


def _handle_decay(handler: SimpleHTTPRequestHandler, query: dict[str, list[str]]) -> None:
    try:
        series_id = _get_required(query, "series")
        series = SERIES[series_id]
        slice_idx = int(_get_required(query, "slice"))
        x = int(round(float(_get_required(query, "x"))))
        y = int(round(float(_get_required(query, "y"))))
        radius = int(round(float(query.get("radius", ["0"])[0])))
        echo_start = int(round(float(query.get("echoStart", ["0"])[0])))
        echo_end = int(round(float(query.get("echoEnd", [str(len(series.echo_times) - 1)])[0])))
    except (ValueError, KeyError) as exc:
        _error(handler, str(exc))
        return

    echo_start, echo_end = _clamp_echo_range(series, echo_start, echo_end)

    signals: list[float] = []
    for echo_idx in range(len(series.echo_times)):
        entry = series.entries_by_slice_echo.get((slice_idx, echo_idx))
        if entry is None:
            signals.append(float("nan"))
            continue
        signals.append(_roi_mean(_read_pixels(entry.path), x, y, radius))

    echo_times = np.array(series.echo_times, dtype=np.float64)
    signal_values = np.array(signals, dtype=np.float64)
    selected_mask = np.zeros(len(series.echo_times), dtype=bool)
    selected_mask[echo_start : echo_end + 1] = True
    fit = _fit_decay(echo_times, signal_values, series.fit_label, selected_mask)
    corrected_fit = (
        _fit_fat_corrected_decay(
            echo_times,
            signal_values,
            series.imaging_frequency_mhz or 123.229482,
            selected_mask,
        )
        if series.sequence_type == "GRE"
        else None
    )
    _json_response(
        handler,
        {
            "series": series_id,
            "fitLabel": series.fit_label,
            "x": x,
            "y": y,
            "slice": slice_idx,
            "radius": radius,
            "echoStart": echo_start,
            "echoEnd": echo_end,
            "echoTimes": series.echo_times,
            "signals": signals,
            "fit": fit,
            "correctedFit": corrected_fit,
        },
    )


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        parsed = urlparse(path)
        normalized = posixpath.normpath(parsed.path)
        rel = normalized.lstrip("/")
        if rel.startswith("../"):
            rel = ""
        rel = rel or "index.html"
        return str(STATIC_ROOT / rel)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/api/series":
            _json_response(self, {"series": _series_payload()})
            return
        if parsed.path == "/api/image":
            _handle_image(self, query)
            return
        if parsed.path == "/api/map":
            _handle_map(self, query)
            return
        if parsed.path == "/api/fat-map":
            _handle_fat_map(self, query)
            return
        if parsed.path == "/api/fat-corrected-map":
            _handle_fat_corrected_map(self, query)
            return
        if parsed.path == "/api/fat-fit":
            _handle_fat_fit(self, query)
            return
        if parsed.path == "/api/decay":
            _handle_decay(self, query)
            return

        return super().do_GET()

    def end_headers(self) -> None:
        if self.path.startswith("/api/"):
            self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    address = ("0.0.0.0", port)
    mimetypes.add_type("text/css", ".css")
    mimetypes.add_type("application/javascript", ".js")
    print(f"Loaded {len(SERIES)} GRE/SE magnitude series")
    for series in SERIES.values():
        print(f"- {series.label}: {len(series.slice_locations)} slices, {len(series.echo_times)} echoes")
    print(f"Serving http://{address[0]}:{address[1]}")
    ThreadingHTTPServer(address, Handler).serve_forever()


if __name__ == "__main__":
    main()
