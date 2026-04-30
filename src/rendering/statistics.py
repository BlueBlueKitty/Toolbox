"""
栅格统计值与直方图服务。
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np


@dataclass(frozen=True)
class BandStatistics:
    min_value: float
    max_value: float
    mean: float
    std: float
    sample_count: int


class RasterStatisticsService:
    def __init__(self):
        self._band_cache: dict[tuple, BandStatistics] = {}
        self._hist_cache: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}

    def band_statistics(self, source, band_index: int = 1, *, sample_max_side: int = 512) -> BandStatistics | None:
        meta = source.metadata()
        key = (meta.path, meta.width, meta.height, meta.dtype, meta.nodata, band_index, sample_max_side)
        cached = self._band_cache.get(key)
        if cached is not None:
            return cached
        data = self._sample_band(source, band_index, sample_max_side=sample_max_side)
        if data is None or data.size == 0:
            return None
        stats = BandStatistics(
            min_value=float(np.min(data)),
            max_value=float(np.max(data)),
            mean=float(np.mean(data)),
            std=float(np.std(data)),
            sample_count=int(data.size),
        )
        self._band_cache[key] = stats
        return stats

    def percentile_range(self, source, band_index: int = 1, percent_clip: tuple[float, float] = (2.0, 98.0), *, sample_max_side: int = 512):
        data = self._sample_band(source, band_index, sample_max_side=sample_max_side)
        if data is None or data.size == 0:
            return None
        low, high = percent_clip
        return float(np.percentile(data, low)), float(np.percentile(data, high))

    def histogram(self, source, band_index: int = 1, *, bins: int = 256, sample_max_side: int = 512):
        meta = source.metadata()
        key = (meta.path, band_index, bins, sample_max_side)
        cached = self._hist_cache.get(key)
        if cached is not None:
            return cached
        data = self._sample_band(source, band_index, sample_max_side=sample_max_side)
        if data is None or data.size == 0:
            return None
        hist, edges = np.histogram(data, bins=bins)
        result = (hist.astype(np.int64), edges.astype(np.float64))
        self._hist_cache[key] = result
        return result

    def _sample_band(self, source, band_index: int, *, sample_max_side: int = 512):
        meta = source.metadata()
        band_index = min(max(int(band_index), 1), max(int(meta.band_count or 1), 1))
        if hasattr(source, "_sample_band_values"):
            values = source._sample_band_values(band_index, max_side=sample_max_side)  # noqa: SLF001
            if values is not None:
                return np.asarray(values, dtype=np.float64)
        width = max(1, int(meta.width))
        height = max(1, int(meta.height))
        step_x = max(1, int(math.ceil(width / max(sample_max_side, 1))))
        step_y = max(1, int(math.ceil(height / max(sample_max_side, 1))))
        data = source.read_window_native(0, 0, width, height)
        if data is None:
            return None
        arr = np.asarray(data)
        if arr.ndim == 3:
            arr = arr[:, :, band_index - 1]
        arr = arr[::step_y, ::step_x].astype(np.float64, copy=False)
        valid = np.isfinite(arr)
        nodata = getattr(meta, "nodata", None)
        if nodata is not None:
            try:
                if np.isnan(nodata):
                    valid &= ~np.isnan(arr)
                else:
                    valid &= arr != nodata
            except TypeError:
                valid &= arr != nodata
        if not np.any(valid):
            return None
        return arr[valid]
