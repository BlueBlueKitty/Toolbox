"""
通用栅格数据源。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import h5py
import numpy as np
from osgeo import gdal
from PIL import Image

from src.utils.display_pyramid import (
    DEFAULT_PYRAMID_THRESHOLD_MB,
    _h5_cache_path,
    _write_h5_dataset_geotiff_cache,
    ensure_gamma_vrt,
    ensure_gdal_overviews,
)
from src.utils.gamma_file_process import read_gamma_pixel, read_gamma_region, validate_dimensions

from .config import RasterRenderConfig, render_raster_rgb
from .models import ImageSourceMetadata, RenderRequest, RenderTileResult
from .overview_manager import build_overviews, choose_overview_for_scale, detect_overviews


class RasterImageSource:
    def metadata(self) -> ImageSourceMetadata:
        raise NotImplementedError

    def render(self, request: RenderRequest, render_config: RasterRenderConfig) -> RenderTileResult:
        raise NotImplementedError

    def read_pixel(self, x: int, y: int):
        raise NotImplementedError

    def read_window_native(self, x: int, y: int, width: int, height: int):
        raise NotImplementedError

    def build_overviews(self, progress_callback=None) -> tuple[bool, list[int]]:
        return False, []

    def band_minmax(self, band_index: int) -> tuple[float, float] | None:
        raise NotImplementedError


class GdalRasterSource(RasterImageSource):
    def __init__(
        self,
        file_path: str,
        source_path: str | None = None,
        pyramid_threshold_mb: int | float | None = DEFAULT_PYRAMID_THRESHOLD_MB,
        auto_build_overviews: bool = True,
    ):
        self.file_path = str(file_path)
        self.source_path = str(source_path or file_path)
        if auto_build_overviews and _file_meets_threshold(self.file_path, pyramid_threshold_mb):
            ensure_gdal_overviews(self.file_path)
        self.dataset = gdal.Open(self.file_path)
        if self.dataset is None:
            raise ValueError(f"无法打开栅格数据: {file_path}")
        self._refresh_metadata()

    def _refresh_metadata(self) -> None:
        ds = self.dataset
        band = ds.GetRasterBand(1)
        projection = ds.GetProjection()
        geotransform = ds.GetGeoTransform(can_return_null=True)
        self._metadata = ImageSourceMetadata(
            id=Path(self.source_path).stem,
            path=str(Path(self.source_path).resolve()),
            path_mode="absolute",
            width=ds.RasterXSize,
            height=ds.RasterYSize,
            band_count=ds.RasterCount,
            dtype=gdal.GetDataTypeName(band.DataType),
            nodata=band.GetNoDataValue(),
            crs_wkt=projection or None,
            geotransform=tuple(geotransform) if geotransform else None,
            resolution=(geotransform[1], geotransform[5]) if geotransform else None,
            has_georef=bool(projection or geotransform),
            overview_levels=detect_overviews(ds),
        )

    def metadata(self) -> ImageSourceMetadata:
        return self._metadata

    def render(self, request: RenderRequest, render_config: RasterRenderConfig) -> RenderTileResult:
        x0, y0, width, height, req_rect = self._clip_request(request)
        target_downsample = max(width / max(request.screen_width, 1), height / max(request.screen_height, 1), 1.0)
        overview = choose_overview_for_scale(self._metadata.overview_levels, target_downsample)
        bands = self._select_bands(request, render_config)
        buf_x = max(1, request.screen_width)
        buf_y = max(1, request.screen_height)

        arrays = []
        for band_index in bands:
            band = self.dataset.GetRasterBand(band_index)
            if overview is not None and band.GetOverviewCount() > overview.level_index:
                band = band.GetOverview(overview.level_index)
                factor = overview.downsample_factor
                ox0 = int(x0 / factor)
                oy0 = int(y0 / factor)
                ow = max(1, int(width / factor))
                oh = max(1, int(height / factor))
                arrays.append(band.ReadAsArray(ox0, oy0, ow, oh, buf_x, buf_y))
            else:
                arrays.append(band.ReadAsArray(x0, y0, width, height, buf_x, buf_y))

        raw = arrays[0] if len(arrays) == 1 else np.stack(arrays, axis=-1)
        display_rgb = render_raster_rgb(raw, render_config, nodata_value=self._metadata.nodata)
        return RenderTileResult(raw, display_rgb, req_rect, overview, (x0, y0, width, height))

    def read_pixel(self, x: int, y: int):
        if not (0 <= x < self._metadata.width and 0 <= y < self._metadata.height):
            return None
        values = []
        for band_index in range(1, self._metadata.band_count + 1):
            value = self.dataset.GetRasterBand(band_index).ReadAsArray(x, y, 1, 1)[0, 0]
            values.append(value.item() if hasattr(value, "item") else value)
        return values[0] if len(values) == 1 else np.array(values)

    def read_window_native(self, x: int, y: int, width: int, height: int):
        x0 = max(0, int(x))
        y0 = max(0, int(y))
        width = max(1, int(min(self._metadata.width - x0, width)))
        height = max(1, int(min(self._metadata.height - y0, height)))
        arrays = []
        for band_index in range(1, self._metadata.band_count + 1):
            arrays.append(self.dataset.GetRasterBand(band_index).ReadAsArray(x0, y0, width, height))
        return arrays[0] if len(arrays) == 1 else np.stack(arrays, axis=-1)

    def build_overviews(self, progress_callback=None) -> tuple[bool, list[int]]:
        success, levels = build_overviews(self.file_path, progress_callback=progress_callback)
        if success:
            self.dataset = gdal.Open(self.file_path)
            self._refresh_metadata()
        return success, levels

    def band_minmax(self, band_index: int) -> tuple[float, float] | None:
        band = self.dataset.GetRasterBand(min(max(int(band_index), 1), self._metadata.band_count))
        try:
            stats = band.GetStatistics(True, True)
            if stats and len(stats) >= 2:
                return float(stats[0]), float(stats[1])
        except Exception:
            pass
        min_max = band.ComputeRasterMinMax(True)
        return None if not min_max else (float(min_max[0]), float(min_max[1]))

    def _clip_request(self, request: RenderRequest):
        req_x0 = max(0.0, min(float(request.x), float(self._metadata.width - 1)))
        req_y0 = max(0.0, min(float(request.y), float(self._metadata.height - 1)))
        req_x1 = max(req_x0 + 1.0, min(float(request.x + request.width), float(self._metadata.width)))
        req_y1 = max(req_y0 + 1.0, min(float(request.y + request.height), float(self._metadata.height)))
        x0 = max(0, int(np.floor(req_x0)))
        y0 = max(0, int(np.floor(req_y0)))
        x1 = min(self._metadata.width, max(x0 + 1, int(np.ceil(req_x1))))
        y1 = min(self._metadata.height, max(y0 + 1, int(np.ceil(req_y1))))
        return x0, y0, max(1, x1 - x0), max(1, y1 - y0), (req_x0, req_y0, req_x1 - req_x0, req_y1 - req_y0)

    def _select_bands(self, request: RenderRequest, render_config: RasterRenderConfig):
        if request.bands:
            return request.bands
        if render_config.display_mode == "RGB":
            return tuple(min(max(int(index), 1), self._metadata.band_count) for index in render_config.rgb_bands)
        return (min(max(int(render_config.gray_band), 1), self._metadata.band_count),)


class GammaVrtRasterSource(GdalRasterSource):
    def __init__(self, file_path: str, width: int, height: int, gamma_format: str, pyramid_threshold_mb=None):
        if not validate_dimensions(file_path, int(width), int(height), gamma_format):
            raise ValueError(f"GAMMA行列数/数据类型与文件体积不匹配: {width}x{height}, {gamma_format}")
        self.gamma_file_path = file_path
        self.gamma_format = gamma_format
        vrt_path = ensure_gamma_vrt(file_path, int(width), int(height), gamma_format)
        vrt_threshold = 0 if _file_meets_threshold(file_path, pyramid_threshold_mb) else None
        super().__init__(vrt_path, source_path=file_path, pyramid_threshold_mb=vrt_threshold)
        self._metadata.nodata = 0

    def render(self, request: RenderRequest, render_config: RasterRenderConfig) -> RenderTileResult:
        result = super().render(request, render_config)
        if self.gamma_format.lower().startswith("cpx"):
            raw = np.angle(result.raw_array).astype(np.float32)
            display_rgb = render_raster_rgb(raw, render_config, nodata_value=0)
            return RenderTileResult(raw, display_rgb, result.image_rect, result.overview_level, result.source_window)
        return result

    def read_pixel(self, x: int, y: int):
        value = read_gamma_pixel(self.gamma_file_path, x, y, self._metadata.width, self._metadata.height, self.gamma_format)
        return np.angle(value) if self.gamma_format.lower().startswith("cpx") else value

    def read_window_native(self, x: int, y: int, width: int, height: int):
        data = read_gamma_region(self.gamma_file_path, x, y, x + width, y + height, self._metadata.width, self._metadata.height, self.gamma_format)
        return np.angle(data) if self.gamma_format.lower().startswith("cpx") else data


class H5DatasetRasterSource(GdalRasterSource):
    def __init__(self, file_path: str, dataset_name: str, frame_index: Optional[int] = None, pyramid_threshold_mb=None):
        self.h5_file_path = file_path
        self.dataset_name = dataset_name
        self.frame_index = frame_index
        cache_path = _h5_cache_path(file_path, dataset_name, frame_index)
        band_count = _h5_band_count(file_path, dataset_name, frame_index)
        _write_h5_dataset_geotiff_cache(cache_path, file_path, dataset_name, frame_index, band_count)
        cache_threshold = 0 if _file_meets_threshold(file_path, pyramid_threshold_mb) else None
        super().__init__(str(cache_path), source_path=file_path, pyramid_threshold_mb=cache_threshold)

    def read_pixel(self, x: int, y: int):
        with h5py.File(self.h5_file_path, "r") as h5f:
            ds = h5f[self.dataset_name]
            if ds.ndim == 2:
                return ds[y, x]
            if self.frame_index is not None:
                return ds[int(self.frame_index), y, x]
            if ds.shape[0] < ds.shape[1] and ds.shape[0] < ds.shape[2]:
                return ds[:, y, x]
            return ds[0, y, x]

    def read_window_native(self, x: int, y: int, width: int, height: int):
        with h5py.File(self.h5_file_path, "r") as h5f:
            ds = h5f[self.dataset_name]
            if ds.ndim == 2:
                return ds[y:y + height, x:x + width].astype(np.float32)
            if self.frame_index is not None:
                return ds[int(self.frame_index), y:y + height, x:x + width].astype(np.float32)
            if ds.shape[0] < ds.shape[1] and ds.shape[0] < ds.shape[2]:
                return np.moveaxis(ds[:, y:y + height, x:x + width], 0, -1).astype(np.float32)
            return ds[0, y:y + height, x:x + width].astype(np.float32)


class StandardImageSource(RasterImageSource):
    def __init__(self, file_path: str):
        self.file_path = file_path
        with Image.open(file_path) as image:
            self._array = np.array(image)
        if self._array.ndim == 3 and self._array.shape[2] == 4:
            self._array = self._array[:, :, :3]

        self._metadata = ImageSourceMetadata(
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

    def metadata(self) -> ImageSourceMetadata:
        return self._metadata

    def render(self, request: RenderRequest, render_config: RasterRenderConfig) -> RenderTileResult:
        display_rgb = render_raster_rgb(self._array, render_config, nodata_value=self._metadata.nodata)
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


def _file_meets_threshold(file_path: str, threshold_mb) -> bool:
    if threshold_mb is None:
        threshold_mb = DEFAULT_PYRAMID_THRESHOLD_MB
    if threshold_mb <= 0:
        return True
    try:
        return Path(file_path).stat().st_size >= float(threshold_mb) * 1024 * 1024
    except OSError:
        return False


def _h5_band_count(file_path: str, dataset_name: str, frame_index: Optional[int]) -> int:
    with h5py.File(file_path, "r") as h5f:
        ds = h5f[dataset_name]
        if ds.ndim == 3 and frame_index is None and ds.shape[0] < ds.shape[1] and ds.shape[0] < ds.shape[2]:
            return int(ds.shape[0])
    return 1
