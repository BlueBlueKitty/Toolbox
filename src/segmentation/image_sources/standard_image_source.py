"""
普通图像数据源。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from ..models import ImageAsset, RenderTileResult
from ..rendering import SegmentationRenderConfig, render_base_rgb
from .base import BaseImageSource
from .render_request import RenderRequest


class StandardImageSource(BaseImageSource):
    def __init__(self, file_path: str):
        self.file_path = file_path
        with Image.open(file_path) as image:
            self._array = np.array(image)
        if self._array.ndim == 3 and self._array.shape[2] == 4:
            self._array = self._array[:, :, :3]

        self._metadata = ImageAsset(
            id=Path(file_path).stem,
            path=str(Path(file_path).resolve()),
            path_mode="absolute",
            width=self._array.shape[1],
            height=self._array.shape[0],
            band_count=1 if self._array.ndim == 2 else self._array.shape[2],
            dtype=str(self._array.dtype),
            nodata=None,
            crs_wkt=None,
            geotransform=None,
            resolution=None,
            has_georef=False,
            overview_levels=[],
        )

    def metadata(self) -> ImageAsset:
        return self._metadata

    def render(self, request: RenderRequest, render_config: SegmentationRenderConfig) -> RenderTileResult:
        display_rgb = render_base_rgb(self._array, render_config, nodata_value=self._metadata.nodata)
        return RenderTileResult(
            raw_array=self._array,
            display_rgb=display_rgb,
            image_rect=(0, 0, self._array.shape[1], self._array.shape[0]),
            overview_level=None,
            source_window=(0, 0, self._array.shape[1], self._array.shape[0]),
        )

    def read_pixel(self, x: int, y: int):
        if not (0 <= x < self._array.shape[1] and 0 <= y < self._array.shape[0]):
            return None
        value = self._array[y, x]
        if hasattr(value, "tolist"):
            return value.tolist()
        return value.item() if hasattr(value, "item") else value

    def read_window_native(self, x: int, y: int, width: int, height: int):
        x0 = max(0, int(x))
        y0 = max(0, int(y))
        x1 = min(self._array.shape[1], x0 + max(1, int(width)))
        y1 = min(self._array.shape[0], y0 + max(1, int(height)))
        return self._array[y0:y1, x0:x1].copy()

    def band_minmax(self, band_index: int) -> tuple[float, float] | None:
        if self._array.ndim == 2:
            data = self._array
        else:
            index = min(max(int(band_index) - 1, 0), self._array.shape[2] - 1)
            data = self._array[:, :, index]
        valid = np.isfinite(data)
        if not np.any(valid):
            return None
        valid_data = data[valid]
        return float(np.min(valid_data)), float(np.max(valid_data))
