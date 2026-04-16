"""
GeoTIFF 数据源。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from osgeo import gdal

from ..models import ImageAsset, RenderTileResult
from ..rendering import SegmentationRenderConfig, render_base_rgb
from .base import BaseImageSource
from .overview_manager import build_overviews, choose_overview_for_scale, detect_overviews
from .render_request import RenderRequest


class GeoTiffImageSource(BaseImageSource):
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.dataset = gdal.Open(str(file_path))
        if self.dataset is None:
            raise ValueError(f"无法打开栅格数据: {file_path}")
        self._refresh_metadata()

    def _refresh_metadata(self) -> None:
        dataset = self.dataset
        projection = dataset.GetProjection()
        geotransform = dataset.GetGeoTransform(can_return_null=True)
        band = dataset.GetRasterBand(1)
        overviews = detect_overviews(dataset)
        self._metadata = ImageAsset(
            id=Path(self.file_path).stem,
            path=str(Path(self.file_path).resolve()),
            path_mode="absolute",
            width=dataset.RasterXSize,
            height=dataset.RasterYSize,
            band_count=dataset.RasterCount,
            dtype=gdal.GetDataTypeName(band.DataType),
            nodata=band.GetNoDataValue(),
            crs_wkt=projection or None,
            geotransform=tuple(geotransform) if geotransform else None,
            resolution=(geotransform[1], geotransform[5]) if geotransform else None,
            has_georef=bool(projection or geotransform),
            overview_levels=overviews,
        )

    def metadata(self) -> ImageAsset:
        return self._metadata

    def build_overviews(self, progress_callback=None) -> tuple[bool, list[int]]:
        success, levels = build_overviews(self.file_path, progress_callback=progress_callback)
        if success:
            self.dataset = None
            self.dataset = gdal.Open(str(self.file_path))
            self._refresh_metadata()
        return success, levels

    def render(self, request: RenderRequest, render_config: SegmentationRenderConfig) -> RenderTileResult:
        req_x0 = max(0.0, min(float(request.x), float(self._metadata.width - 1)))
        req_y0 = max(0.0, min(float(request.y), float(self._metadata.height - 1)))
        req_x1 = max(req_x0 + 1.0, min(float(request.x + request.width), float(self._metadata.width)))
        req_y1 = max(req_y0 + 1.0, min(float(request.y + request.height), float(self._metadata.height)))

        x0 = max(0, int(np.floor(req_x0)))
        y0 = max(0, int(np.floor(req_y0)))
        x1 = min(self._metadata.width, max(x0 + 1, int(np.ceil(req_x1))))
        y1 = min(self._metadata.height, max(y0 + 1, int(np.ceil(req_y1))))
        width = max(1, x1 - x0)
        height = max(1, y1 - y0)
        target_downsample = max(
            width / max(request.screen_width, 1),
            height / max(request.screen_height, 1),
            1.0,
        )
        overview = choose_overview_for_scale(self._metadata.overview_levels, target_downsample)
        if request.bands:
            bands = request.bands
        elif render_config.display_mode == "RGB":
            bands = tuple(
                min(max(int(index), 1), self._metadata.band_count)
                for index in render_config.rgb_bands
            )
        else:
            bands = (min(max(int(render_config.gray_band), 1), self._metadata.band_count),)
        buf_x = max(1, request.screen_width)
        buf_y = max(1, request.screen_height)

        arrays = []
        for band_index in bands:
            band = self.dataset.GetRasterBand(band_index)
            if overview is not None and band.GetOverviewCount() > overview.level_index:
                band = band.GetOverview(overview.level_index)
                source_factor = overview.downsample_factor
                ox0 = int(x0 / source_factor)
                oy0 = int(y0 / source_factor)
                ow = max(1, int(width / source_factor))
                oh = max(1, int(height / source_factor))
                array = band.ReadAsArray(ox0, oy0, ow, oh, buf_x, buf_y)
            else:
                array = band.ReadAsArray(x0, y0, width, height, buf_x, buf_y)
            arrays.append(array)

        if len(arrays) == 1:
            raw = arrays[0]
        else:
            raw = np.stack(arrays, axis=-1)
        display_rgb = render_base_rgb(raw, render_config, nodata_value=self._metadata.nodata)

        return RenderTileResult(
            raw_array=raw,
            display_rgb=display_rgb,
            image_rect=(req_x0, req_y0, req_x1 - req_x0, req_y1 - req_y0),
            overview_level=overview,
            source_window=(x0, y0, width, height),
        )

    def read_pixel(self, x: int, y: int):
        if not (0 <= x < self._metadata.width and 0 <= y < self._metadata.height):
            return None
        values = []
        for band_index in range(1, self._metadata.band_count + 1):
            band = self.dataset.GetRasterBand(band_index)
            value = band.ReadAsArray(x, y, 1, 1)[0, 0]
            values.append(value.item() if hasattr(value, "item") else value)
        if len(values) == 1:
            return values[0]
        return values

    def read_window_native(self, x: int, y: int, width: int, height: int):
        x0 = max(0, int(x))
        y0 = max(0, int(y))
        width = max(1, int(min(self._metadata.width - x0, width)))
        height = max(1, int(min(self._metadata.height - y0, height)))
        arrays = []
        for band_index in range(1, self._metadata.band_count + 1):
            band = self.dataset.GetRasterBand(band_index)
            arrays.append(band.ReadAsArray(x0, y0, width, height))
        if len(arrays) == 1:
            return arrays[0]
        return np.stack(arrays, axis=-1)

    def band_minmax(self, band_index: int) -> tuple[float, float] | None:
        band = self.dataset.GetRasterBand(min(max(int(band_index), 1), self._metadata.band_count))
        if band is None:
            return None
        min_max = band.ComputeRasterMinMax(True)
        if not min_max:
            return None
        return float(min_max[0]), float(min_max[1])
