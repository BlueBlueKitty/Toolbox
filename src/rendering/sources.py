"""
通用栅格数据源。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from dataclasses import replace

import h5py
import numpy as np
from osgeo import gdal
from PIL import Image

from src.utils.display_pyramid import (
    DEFAULT_PYRAMID_THRESHOLD_MB,
    _h5_cache_path,
    _write_h5_dataset_geotiff_cache,
    cached_gdal_dataset_path,
    ensure_gamma_vrt,
    ensure_gdal_overviews,
    has_gdal_overviews,
)
from src.utils.gamma_file_process import read_gamma_pixel, read_gamma_region, validate_dimensions

from .config import RasterRenderConfig
from .models import ImageSourceMetadata, RawRasterBlock, RenderRequest, RenderTileResult
from .overview_manager import build_overviews, choose_overview_for_scale, detect_overviews
from .pipeline import DEFAULT_RENDER_PIPELINE
from .style_auto_selector import DefaultRenderStyleFactory
from .styles import default_display_settings, legacy_config_to_style

_ALIGN_EPS = 1e-6


class RasterImageSource:
    def metadata(self) -> ImageSourceMetadata:
        raise NotImplementedError

    def read_block(self, request: RenderRequest, style=None) -> RawRasterBlock:
        raise NotImplementedError

    def render(self, request: RenderRequest, render_config: RasterRenderConfig) -> RenderTileResult:
        metadata = self.metadata()
        style = legacy_config_to_style(render_config, metadata)
        display_settings = default_display_settings(nodata_value=metadata.nodata)
        return DEFAULT_RENDER_PIPELINE.render_source(
            self,
            request,
            style,
            display_settings,
            layer_id=getattr(request, "layer_id", None),
            layer_revision=0,
        )

    def read_pixel(self, x: int, y: int):
        raise NotImplementedError

    def read_window_native(self, x: int, y: int, width: int, height: int):
        raise NotImplementedError

    def build_overviews(self, progress_callback=None) -> tuple[bool, list[int]]:
        return False, []

    def band_minmax(self, band_index: int) -> tuple[float, float] | None:
        raise NotImplementedError

    def value_range_for_settings(self, settings: dict) -> tuple[float, float] | None:
        if settings.get("display_mode") == "RGB":
            ranges = [self.band_value_range(index, settings) for index in settings.get("rgb_bands", (1, 2, 3))]
            ranges = [item for item in ranges if item is not None]
            if not ranges:
                return None
            return float(min(item[0] for item in ranges)), float(max(item[1] for item in ranges))
        return self.band_value_range(settings.get("gray_band", 1), settings)

    def band_value_range(self, band_index: int, settings: dict) -> tuple[float, float] | None:
        return self.band_minmax(band_index)


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
        if auto_build_overviews and has_gdal_overviews(self.file_path):
            pass
        elif auto_build_overviews and _file_meets_threshold(self.file_path, pyramid_threshold_mb):
            self.file_path = cached_gdal_dataset_path(self.file_path)
            ensure_gdal_overviews(self.file_path)
        self.dataset = gdal.Open(self.file_path)
        if self.dataset is None:
            raise ValueError(f"无法打开栅格数据: {file_path}")
        self._refresh_metadata()

    def _refresh_metadata(self) -> None:
        ds = self.dataset
        band = ds.GetRasterBand(1)
        color_table = None
        has_color_table = False
        color_interpretations = []
        for band_index in range(1, ds.RasterCount + 1):
            try:
                interp = gdal.GetColorInterpretationName(ds.GetRasterBand(band_index).GetColorInterpretation())
                color_interpretations.append(str(interp or "").lower())
            except Exception:
                color_interpretations.append("")
        try:
            ct = band.GetColorTable()
            if ct is not None:
                has_color_table = True
                color_table = [tuple(int(v) for v in ct.GetColorEntry(i)) for i in range(ct.GetCount())]
        except Exception:
            color_table = None
            has_color_table = False
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
            has_color_table=has_color_table,
            color_table=color_table,
            overview_levels=detect_overviews(ds),
            color_interpretations=color_interpretations,
            custom_properties={"source_kind": "gdal"},
        )

    def metadata(self) -> ImageSourceMetadata:
        return self._metadata

    def read_block(self, request: RenderRequest, style=None) -> RawRasterBlock:
        x0, y0, width, height, req_rect = self._clip_request(request)
        req_x, req_y, req_width, req_height = req_rect
        target_pixel_x = max(float(request.width) / max(request.screen_width, 1), 1e-9)
        target_pixel_y = max(float(request.height) / max(request.screen_height, 1), 1e-9)
        target_downsample = max(target_pixel_x, target_pixel_y, 1.0)
        overview = choose_overview_for_scale(self._metadata.overview_levels, target_downsample)
        bands = self._select_bands(request, style)
        buf_x = max(1, int(request.screen_width))
        buf_y = max(1, int(request.screen_height))
        # 使用稳定 floor/ceil 取整，避免 round 在 0.5 临界点造成缩放暂停后的像素相位抖动。
        dst_x = min(max(0, int(np.floor((req_x - request.x) / target_pixel_x + _ALIGN_EPS))), buf_x - 1)
        dst_y = min(max(0, int(np.floor((req_y - request.y) / target_pixel_y + _ALIGN_EPS))), buf_y - 1)
        read_buf_x = max(1, min(buf_x - dst_x, int(np.ceil(req_width / target_pixel_x - _ALIGN_EPS))))
        read_buf_y = max(1, min(buf_y - dst_y, int(np.ceil(req_height / target_pixel_y - _ALIGN_EPS))))
        image_rect = (float(request.x), float(request.y), float(request.width), float(request.height))
        source_window = (x0, y0, width, height)
        clipped_to_request = dst_x != 0 or dst_y != 0 or read_buf_x != buf_x or read_buf_y != buf_y

        arrays = []
        for band_index in bands:
            band = self.dataset.GetRasterBand(band_index)
            if overview is not None and band.GetOverviewCount() > overview.level_index:
                overview_band = band.GetOverview(overview.level_index)
                factor_x = self._metadata.width / max(float(overview_band.XSize), 1.0)
                factor_y = self._metadata.height / max(float(overview_band.YSize), 1.0)
                arrays.append(
                    overview_band.ReadAsArray(
                        req_x / factor_x,
                        req_y / factor_y,
                        max(req_width / factor_x, 1e-9),
                        max(req_height / factor_y, 1e-9),
                        read_buf_x,
                        read_buf_y,
                    )
                )
            else:
                arrays.append(band.ReadAsArray(req_x, req_y, req_width, req_height, read_buf_x, read_buf_y))
            if clipped_to_request:
                arrays[-1] = _embed_array_in_request(arrays[-1], (buf_y, buf_x), dst_x, dst_y, self._metadata.nodata)

        raw = arrays[0] if len(arrays) == 1 else np.stack(arrays, axis=-1)
        return RawRasterBlock(
            data=raw,
            image_rect=image_rect,
            source_window=source_window,
            overview_level=overview,
            nodata_value=self._metadata.nodata,
            metadata=self._metadata,
            band_indices=tuple(bands),
        )

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

    def band_value_range(self, band_index: int, settings: dict) -> tuple[float, float] | None:
        values = self._sample_band_values(band_index)
        if values is None or values.size == 0:
            return self.band_minmax(band_index)
        stretch_mode = settings.get("stretch_mode", "最大最小")
        if stretch_mode == "百分比截断":
            low, high = settings.get("percent_clip", (2.0, 98.0))
            return float(np.percentile(values, low)), float(np.percentile(values, high))
        if stretch_mode == "标准差":
            n = float(settings.get("std_dev_n", 2.0))
            mean = float(np.mean(values))
            std = float(np.std(values))
            return mean - n * std, mean + n * std
        return float(np.min(values)), float(np.max(values))

    def _sample_band_values(self, band_index: int, max_side: int = 1024):
        band = self.dataset.GetRasterBand(min(max(int(band_index), 1), self._metadata.band_count))
        source_band = band
        if band.GetOverviewCount() > 0:
            source_band = band.GetOverview(band.GetOverviewCount() - 1)
        width = int(source_band.XSize)
        height = int(source_band.YSize)
        if max(width, height) > max_side:
            scale = max_side / max(width, height)
            buf_x = max(1, int(width * scale))
            buf_y = max(1, int(height * scale))
            data = source_band.ReadAsArray(buf_xsize=buf_x, buf_ysize=buf_y)
        else:
            data = source_band.ReadAsArray()
        if data is None:
            return None
        arr = np.asarray(data, dtype=np.float64)
        valid = np.isfinite(arr)
        nodata = self._metadata.nodata
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

    def _clip_request(self, request: RenderRequest):
        max_w = float(self._metadata.width)
        max_h = float(self._metadata.height)
        req_x0 = max(0.0, min(float(request.x), max_w))
        req_y0 = max(0.0, min(float(request.y), max_h))
        req_x1 = max(0.0, min(float(request.x + request.width), max_w))
        req_y1 = max(0.0, min(float(request.y + request.height), max_h))
        if req_x1 <= req_x0:
            req_x1 = min(max_w, req_x0 + 1.0)
            req_x0 = max(0.0, req_x1 - 1.0)
        if req_y1 <= req_y0:
            req_y1 = min(max_h, req_y0 + 1.0)
            req_y0 = max(0.0, req_y1 - 1.0)

        x0 = max(0, int(np.floor(req_x0 + _ALIGN_EPS)))
        y0 = max(0, int(np.floor(req_y0 + _ALIGN_EPS)))
        x1 = min(self._metadata.width, max(x0 + 1, int(np.ceil(req_x1 - _ALIGN_EPS))))
        y1 = min(self._metadata.height, max(y0 + 1, int(np.ceil(req_y1 - _ALIGN_EPS))))
        return x0, y0, max(1, x1 - x0), max(1, y1 - y0), (req_x0, req_y0, req_x1 - req_x0, req_y1 - req_y0)

    def _select_bands(self, request: RenderRequest, style):
        if request.bands:
            return request.bands
        if style is not None and getattr(style, "band_indices", None):
            return tuple(min(max(int(index), 1), self._metadata.band_count) for index in style.band_indices)
        return (1,)


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
        self._metadata.custom_properties.update({
            "source_kind": "gamma",
            "gamma_format": gamma_format,
        })

    def read_pixel(self, x: int, y: int):
        value = read_gamma_pixel(self.gamma_file_path, x, y, self._metadata.width, self._metadata.height, self.gamma_format)
        return np.angle(value) if self.gamma_format.lower().startswith("cpx") else value

    def read_window_native(self, x: int, y: int, width: int, height: int):
        data = read_gamma_region(self.gamma_file_path, x, y, x + width, y + height, self._metadata.width, self._metadata.height, self.gamma_format)
        return np.angle(data) if self.gamma_format.lower().startswith("cpx") else data


class HillshadeCompositeRasterSource(RasterImageSource):
    """兼容层：保留旧的晕渲缓存数据源接口。"""

    def __init__(self, base_source: RasterImageSource, hillshade_source: RasterImageSource):
        self.base_source = base_source
        self.hillshade_source = hillshade_source

    def metadata(self) -> ImageSourceMetadata:
        return self.base_source.metadata()

    def read_block(self, request: RenderRequest, style=None) -> RawRasterBlock:
        return self.base_source.read_block(request, style=style)

    def render(self, request: RenderRequest, render_config: RasterRenderConfig) -> RenderTileResult:
        metadata = self.metadata()
        style = legacy_config_to_style(render_config, metadata)
        display_settings = default_display_settings(nodata_value=metadata.nodata)
        return DEFAULT_RENDER_PIPELINE.render_source(self.base_source, request, style, display_settings, layer_id=getattr(request, "layer_id", None))

    def read_pixel(self, x: int, y: int):
        return self.base_source.read_pixel(x, y)

    def read_window_native(self, x: int, y: int, width: int, height: int):
        return self.base_source.read_window_native(x, y, width, height)

    def build_overviews(self, progress_callback=None) -> tuple[bool, list[int]]:
        return self.base_source.build_overviews(progress_callback=progress_callback)

    def band_minmax(self, band_index: int) -> tuple[float, float] | None:
        return self.base_source.band_minmax(band_index)

    def band_value_range(self, band_index: int, settings: dict) -> tuple[float, float] | None:
        return self.base_source.band_value_range(band_index, settings)


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
        self._metadata.custom_properties.update({
            "source_kind": "h5_dataset",
            "dataset_name": dataset_name,
            "frame_index": frame_index,
        })

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


class H5TimeSeriesRasterSource(H5DatasetRasterSource):
    """HDF5 时序二维切片数据源。"""

    def __init__(self, file_path: str, dataset_name: str, time_index: int, pyramid_threshold_mb=None):
        self.time_index = int(time_index)
        super().__init__(file_path, dataset_name, frame_index=self.time_index, pyramid_threshold_mb=pyramid_threshold_mb)
        self._metadata = replace(
            self._metadata,
            custom_properties={
                **self._metadata.custom_properties,
                "source_kind": "h5_timeseries",
                "time_index": self.time_index,
            },
        )


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
            has_color_table=False,
            color_table=None,
            overview_levels=[],
            custom_properties={"source_kind": "standard_image"},
        )

    def metadata(self) -> ImageSourceMetadata:
        return self._metadata

    def read_block(self, request: RenderRequest, style=None) -> RawRasterBlock:
        x0 = max(0, int(np.floor(request.x)))
        y0 = max(0, int(np.floor(request.y)))
        x1 = min(self._array.shape[1], max(x0 + 1, int(np.ceil(request.x + request.width))))
        y1 = min(self._array.shape[0], max(y0 + 1, int(np.ceil(request.y + request.height))))
        data = self._array[y0:y1, x0:x1].copy()
        return RawRasterBlock(
            data=data,
            image_rect=(float(x0), float(y0), float(x1 - x0), float(y1 - y0)),
            source_window=(x0, y0, int(x1 - x0), int(y1 - y0)),
            overview_level=None,
            nodata_value=None,
            metadata=self._metadata,
        )

    def render(self, request: RenderRequest, render_config: RasterRenderConfig) -> RenderTileResult:
        metadata = self.metadata()
        style = legacy_config_to_style(render_config, metadata)
        display_settings = default_display_settings(nodata_value=metadata.nodata)
        return DEFAULT_RENDER_PIPELINE.render_source(self, request, style, display_settings, layer_id=getattr(request, "layer_id", None))

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

    def band_value_range(self, band_index: int, settings: dict) -> tuple[float, float] | None:
        if self._array.ndim == 2:
            data = self._array
        else:
            index = min(max(int(band_index) - 1, 0), self._array.shape[2] - 1)
            data = self._array[:, :, index]
        valid = np.isfinite(data)
        if not np.any(valid):
            return None
        values = data[valid].astype(np.float64)
        stretch_mode = settings.get("stretch_mode", "最大最小")
        if stretch_mode == "百分比截断":
            low, high = settings.get("percent_clip", (2.0, 98.0))
            return float(np.percentile(values, low)), float(np.percentile(values, high))
        if stretch_mode == "标准差":
            n = float(settings.get("std_dev_n", 2.0))
            mean = float(np.mean(values))
            std = float(np.std(values))
            return mean - n * std, mean + n * std
        return float(np.min(values)), float(np.max(values))


def _file_meets_threshold(file_path: str, threshold_mb) -> bool:
    if threshold_mb is None:
        threshold_mb = DEFAULT_PYRAMID_THRESHOLD_MB
    if threshold_mb <= 0:
        return True
    try:
        return Path(file_path).stat().st_size >= float(threshold_mb) * 1024 * 1024
    except OSError:
        return False


def _embed_array_in_request(array: np.ndarray, target_shape: tuple[int, int], x: int, y: int, nodata_value) -> np.ndarray:
    if array is None:
        return np.full(target_shape, np.nan, dtype=np.float32)
    arr = np.asarray(array)
    if arr.shape[:2] == target_shape and x == 0 and y == 0:
        return arr
    fill_value = nodata_value if nodata_value is not None else np.nan
    try:
        fill_is_nan = bool(np.isnan(fill_value))
    except TypeError:
        fill_is_nan = False
    can_store_fill = (nodata_value is not None and not fill_is_nan) or np.issubdtype(arr.dtype, np.floating)
    dtype = arr.dtype if can_store_fill else np.float32
    canvas = np.full(target_shape, fill_value, dtype=dtype)
    src_h = min(arr.shape[0], target_shape[0] - y)
    src_w = min(arr.shape[1], target_shape[1] - x)
    if src_h > 0 and src_w > 0:
        canvas[y:y + src_h, x:x + src_w] = arr[:src_h, :src_w].astype(dtype, copy=False)
    return canvas


def _h5_band_count(file_path: str, dataset_name: str, frame_index: Optional[int]) -> int:
    with h5py.File(file_path, "r") as h5f:
        ds = h5f[dataset_name]
        if ds.ndim == 3 and frame_index is None and ds.shape[0] < ds.shape[1] and ds.shape[0] < ds.shape[2]:
            return int(ds.shape[0])
    return 1
