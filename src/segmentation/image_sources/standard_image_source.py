"""
普通图像数据源。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from ..models import ImageAsset, RenderTileResult
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

    def render(self, request: RenderRequest) -> RenderTileResult:
        x0 = max(0, int(request.x))
        y0 = max(0, int(request.y))
        x1 = min(self._array.shape[1], int(request.x + request.width))
        y1 = min(self._array.shape[0], int(request.y + request.height))
        view = self._array[y0:y1, x0:x1]
        return RenderTileResult(
            array=view,
            image_rect=(x0, y0, max(x1 - x0, 1), max(y1 - y0, 1)),
            overview_level=None,
            source_window=(x0, y0, max(x1 - x0, 1), max(y1 - y0, 1)),
        )
