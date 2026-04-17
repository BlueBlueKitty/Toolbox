"""
显示用金字塔缓存与读取。

这些函数只服务于界面渲染预览，不改变各工具已有的按窗口、按像素
读取路径。大数据会被包装成 GDAL 可读取的数据集并建立 overview，
界面再从合适的 overview 读取预览数组。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional, Tuple

import h5py
import numpy as np
from osgeo import gdal
from PIL import Image

from src.utils.gamma_file_process import _parse_format, validate_dimensions
from src.utils.image_io import read_tiff_downsampled, read_image


DEFAULT_PYRAMID_THRESHOLD_MB = 128
DISPLAY_OVERVIEW_MAX_SIZE = 4096


def needs_display_pyramid(file_path: str, threshold_mb: int | float | None) -> bool:
    if not file_path or not os.path.exists(file_path):
        return False
    threshold = DEFAULT_PYRAMID_THRESHOLD_MB if threshold_mb is None else float(threshold_mb)
    if threshold <= 0:
        return True
    return os.path.getsize(file_path) >= threshold * 1024 * 1024


def read_gdal_pyramid_display(
    file_path: str,
    threshold_mb: int | float | None = DEFAULT_PYRAMID_THRESHOLD_MB,
    max_size: int = DISPLAY_OVERVIEW_MAX_SIZE,
) -> Tuple[Optional[np.ndarray], Optional[float], Optional[Tuple[int, int]], int, str]:
    """
    从 GDAL 数据集读取显示预览；大于阈值时优先建立并使用 overview。
    """
    if needs_display_pyramid(file_path, threshold_mb):
        ensure_gdal_overviews(file_path)
    data, nodata, original_size, factor = read_tiff_downsampled(file_path, max_size)
    return data, nodata, original_size, factor, "pyramid" if factor > 1 else "full"


def read_standard_pyramid_display(
    file_path: str,
    threshold_mb: int | float | None = DEFAULT_PYRAMID_THRESHOLD_MB,
) -> Tuple[Optional[np.ndarray], Optional[float], Optional[Tuple[int, int]], int, str]:
    """
    普通图像优先交给 GDAL 读取 overview；不支持更新 overview 的格式回退到原图读取。
    """
    ds = gdal.Open(os.path.normpath(file_path))
    if ds is not None and ds.RasterCount > 0:
        return read_gdal_pyramid_display(file_path, threshold_mb)

    data, original_size = read_image(file_path)
    return data, None, original_size, 1, "full"


def read_gamma_pyramid_display(
    file_path: str,
    width: int,
    height: int,
    gamma_format: str,
    threshold_mb: int | float | None = DEFAULT_PYRAMID_THRESHOLD_MB,
) -> Tuple[Optional[np.ndarray], Optional[float], Optional[Tuple[int, int]], int, str]:
    """
    为 GAMMA raw binary 生成 VRT + .vrt.ovr，再从 overview 读取显示预览。
    """
    if not validate_dimensions(file_path, int(width), int(height), gamma_format):
        raise ValueError(f"GAMMA行列数/数据类型与文件体积不匹配: {width}x{height}, {gamma_format}")

    vrt_path = ensure_gamma_vrt(file_path, int(width), int(height), gamma_format)
    if needs_display_pyramid(file_path, threshold_mb):
        ensure_gdal_overviews(vrt_path)
    data, nodata, original_size, factor = read_tiff_downsampled(vrt_path, DISPLAY_OVERVIEW_MAX_SIZE)
    if data is not None and gamma_format.lower().startswith("cpx"):
        data = np.angle(data).astype(np.float32)
    elif data is not None:
        data = data.astype(np.float32, copy=False)
    return data, 0, original_size, factor, "pyramid" if factor > 1 else "full"


def ensure_gamma_vrt(file_path: str, width: int, height: int, gamma_format: str) -> str:
    dtype, is_complex = _parse_format(gamma_format)
    gdal_type = _gamma_gdal_type(dtype, is_complex)
    bytes_per_pixel = dtype.itemsize * (2 if is_complex else 1)
    vrt_path = f"{file_path}.vrt"
    source_name = os.path.basename(file_path)
    byte_order = "MSB"
    xml = f"""<VRTDataset rasterXSize="{int(width)}" rasterYSize="{int(height)}">
  <VRTRasterBand dataType="{gdal_type}" band="1" subClass="VRTRawRasterBand">
    <SourceFilename relativeToVRT="1">{_xml_escape(source_name)}</SourceFilename>
    <ImageOffset>0</ImageOffset>
    <PixelOffset>{bytes_per_pixel}</PixelOffset>
    <LineOffset>{int(width) * bytes_per_pixel}</LineOffset>
    <ByteOrder>{byte_order}</ByteOrder>
  </VRTRasterBand>
</VRTDataset>
"""
    path = Path(vrt_path)
    if not path.exists() or path.read_text(encoding="utf-8", errors="ignore") != xml:
        path.write_text(xml, encoding="utf-8")
    return vrt_path


def read_h5_dataset_pyramid_display(
    file_path: str,
    dataset_name: str,
    frame_index: Optional[int] = None,
    threshold_mb: int | float | None = DEFAULT_PYRAMID_THRESHOLD_MB,
) -> Tuple[Optional[np.ndarray], Optional[Tuple[int, int]], int, str]:
    """
    HDF5/NC 显示路径：大数据集写入显示缓存 GeoTIFF 并建立 overview。
    """
    layout = _h5_dataset_layout(file_path, dataset_name, frame_index)
    if layout is None:
        return None, None, 1, "full"
    original_size, estimated_bytes, band_count = layout

    threshold = DEFAULT_PYRAMID_THRESHOLD_MB if threshold_mb is None else float(threshold_mb)
    if threshold > 0 and estimated_bytes < threshold * 1024 * 1024:
        data, original_size, _estimated_bytes = _read_h5_display_source(file_path, dataset_name, frame_index)
        return data, original_size, 1, "full"

    cache_path = _h5_cache_path(file_path, dataset_name, frame_index)
    if _cache_is_stale(cache_path, file_path):
        _write_h5_dataset_geotiff_cache(cache_path, file_path, dataset_name, frame_index, band_count)
    ensure_gdal_overviews(str(cache_path))
    display, _nodata, original_size, factor = read_tiff_downsampled(str(cache_path), DISPLAY_OVERVIEW_MAX_SIZE)
    if display is not None:
        display = display.astype(np.float32, copy=False)
    return display, original_size, factor, "pyramid" if factor > 1 else "full"


def read_h5_timeseries_frame_pyramid_display(
    file_path: str,
    frame_index: int,
    threshold_mb: int | float | None = DEFAULT_PYRAMID_THRESHOLD_MB,
) -> Tuple[Optional[np.ndarray], Optional[Tuple[int, int]], int, str]:
    return read_h5_dataset_pyramid_display(file_path, "timeseries", frame_index, threshold_mb)


def ensure_gdal_overviews(file_path: str, levels: Optional[list[int]] = None) -> tuple[bool, list[int]]:
    normalized = os.path.normpath(file_path)
    ds = gdal.Open(normalized, gdal.GA_Update)
    if ds is None:
        ds = gdal.Open(normalized, gdal.GA_ReadOnly)
    if ds is None or ds.RasterCount == 0:
        return False, []
    try:
        band = ds.GetRasterBand(1)
        if band.GetOverviewCount() > 0:
            return True, []
        width = ds.RasterXSize
        height = ds.RasterYSize
        if levels is None:
            levels = []
            factor = 2
            while min(width, height) / factor > 256:
                levels.append(factor)
                factor *= 2
            if factor not in levels:
                levels.append(factor)
        ds.BuildOverviews("AVERAGE", levels)
        return True, levels
    finally:
        ds = None


def _read_h5_display_source(file_path: str, dataset_name: str, frame_index: Optional[int]):
    with h5py.File(file_path, "r") as h5f:
        if dataset_name not in h5f:
            return None, None, 0
        ds = h5f[dataset_name]
        estimated_bytes = int(np.prod(ds.shape) * ds.dtype.itemsize)
        if ds.ndim == 2:
            data = ds[:].astype(np.float32)
            height, width = data.shape
        elif ds.ndim == 3:
            if frame_index is not None:
                data = ds[int(frame_index), :, :].astype(np.float32)
                height, width = data.shape
            elif ds.shape[0] < ds.shape[1] and ds.shape[0] < ds.shape[2]:
                data = np.moveaxis(ds[:], 0, -1).astype(np.float32)
                height, width = data.shape[:2]
            else:
                data = ds[0, :, :].astype(np.float32)
                height, width = data.shape
        else:
            return None, None, 0
    return data, (width, height), estimated_bytes


def _h5_dataset_layout(file_path: str, dataset_name: str, frame_index: Optional[int]):
    with h5py.File(file_path, "r") as h5f:
        if dataset_name not in h5f:
            return None
        ds = h5f[dataset_name]
        estimated_bytes = int(np.prod(ds.shape) * ds.dtype.itemsize)
        if ds.ndim == 2:
            height, width = ds.shape
            band_count = 1
        elif ds.ndim == 3:
            if frame_index is not None:
                height, width = ds.shape[1], ds.shape[2]
                band_count = 1
                estimated_bytes = int(height * width * ds.dtype.itemsize)
            elif ds.shape[0] < ds.shape[1] and ds.shape[0] < ds.shape[2]:
                band_count = int(ds.shape[0])
                height, width = ds.shape[1], ds.shape[2]
            else:
                height, width = ds.shape[1], ds.shape[2]
                band_count = 1
                estimated_bytes = int(height * width * ds.dtype.itemsize)
        else:
            return None
    return (int(width), int(height)), estimated_bytes, band_count


def _write_h5_dataset_geotiff_cache(
    path: Path,
    file_path: str,
    dataset_name: str,
    frame_index: Optional[int],
    band_count: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(file_path, "r") as h5f:
        ds = h5f[dataset_name]
        if ds.ndim == 2:
            height, width = ds.shape
        else:
            height, width = ds.shape[1], ds.shape[2]

        driver = gdal.GetDriverByName("GTiff")
        dataset = driver.Create(
            str(path),
            int(width),
            int(height),
            int(band_count),
            gdal.GDT_Float32,
            options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"],
        )
        if dataset is None:
            raise RuntimeError(f"无法创建显示金字塔缓存: {path}")

        block_rows = 512
        for y0 in range(0, int(height), block_rows):
            y1 = min(int(height), y0 + block_rows)
            if ds.ndim == 2:
                chunk = ds[y0:y1, :].astype(np.float32)
                dataset.GetRasterBand(1).WriteArray(chunk, 0, y0)
            elif frame_index is not None:
                chunk = ds[int(frame_index), y0:y1, :].astype(np.float32)
                dataset.GetRasterBand(1).WriteArray(chunk, 0, y0)
            elif ds.shape[0] < ds.shape[1] and ds.shape[0] < ds.shape[2]:
                for band_index in range(int(band_count)):
                    chunk = ds[band_index, y0:y1, :].astype(np.float32)
                    dataset.GetRasterBand(band_index + 1).WriteArray(chunk, 0, y0)
            else:
                chunk = ds[0, y0:y1, :].astype(np.float32)
                dataset.GetRasterBand(1).WriteArray(chunk, 0, y0)

        dataset.FlushCache()
        dataset = None


def _write_array_geotiff(path: Path, data: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if data.ndim == 2:
        height, width = data.shape
        band_count = 1
    else:
        height, width, band_count = data.shape
    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(
        str(path),
        int(width),
        int(height),
        int(band_count),
        gdal.GDT_Float32 if np.issubdtype(data.dtype, np.floating) else gdal.GDT_Int32,
        options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"],
    )
    if dataset is None:
        raise RuntimeError(f"无法创建显示金字塔缓存: {path}")
    if data.ndim == 2:
        dataset.GetRasterBand(1).WriteArray(data)
    else:
        for index in range(band_count):
            dataset.GetRasterBand(index + 1).WriteArray(data[:, :, index])
    dataset.FlushCache()
    dataset = None


def _cache_is_stale(cache_path: Path, source_path: str) -> bool:
    if not cache_path.exists():
        return True
    return cache_path.stat().st_mtime < Path(source_path).stat().st_mtime


def _h5_cache_path(file_path: str, dataset_name: str, frame_index: Optional[int]) -> Path:
    key = f"{Path(file_path).resolve()}::{dataset_name}::{frame_index}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return Path.home() / ".toolbox" / "pyramids" / f"{digest}.tif"


def _gamma_gdal_type(dtype: np.dtype, is_complex: bool) -> str:
    base = np.dtype(dtype).name.replace(">", "").replace("<", "")
    if is_complex:
        if base == "float64":
            return "CFloat64"
        return "CFloat32"
    mapping = {
        "uint8": "Byte",
        "int16": "Int16",
        "int32": "Int32",
        "float32": "Float32",
        "float64": "Float64",
    }
    return mapping.get(base, "Float32")


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
