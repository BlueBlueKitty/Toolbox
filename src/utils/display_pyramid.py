"""
显示用金字塔缓存与读取。

这些函数只服务于界面渲染预览，不改变各工具已有的按窗口、按像素
读取路径。大数据会被包装成 GDAL 可读取的数据集并建立 overview，
界面再从合适的 overview 读取预览数组。
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

import h5py
import numpy as np
from osgeo import gdal
from PIL import Image

from src.utils.gamma_file_process import _parse_format, validate_dimensions
from src.utils.image_io import read_tiff_downsampled, read_image

try:
    from PySide6.QtCore import QSettings
except Exception:  # pragma: no cover - allows non-Qt utility imports
    QSettings = None


DEFAULT_PYRAMID_THRESHOLD_MB = 128
DISPLAY_OVERVIEW_MAX_SIZE = 4096
CACHE_MAX_AGE_DAYS = 7
CACHE_SETTINGS_ORG = "Toolbox"
CACHE_SETTINGS_APP = "RemoteSensingToolbox"
CACHE_DIR_SETTING_KEY = "display/cache_dir"
_cleanup_lock = threading.Lock()
_cleanup_thread: threading.Thread | None = None


def default_cache_dir() -> Path:
    """默认显示缓存目录：用户目录下的 .toolbox/pyramids。"""
    return Path.home() / ".toolbox" / "pyramids"


def _normalize_cache_dir(path_value: str | os.PathLike | None) -> Path:
    """_normalize_cache_dir。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        path_value (str | os.PathLike | None): 输入参数。
    返回:
        Path: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    if path_value is None:
        return default_cache_dir()
    path_text = str(path_value).strip()
    if not path_text:
        return default_cache_dir()
    return Path(os.path.expandvars(os.path.expanduser(path_text))).resolve()


def get_cache_dir() -> Path:
    """读取当前显示缓存目录设置；未设置时回退到默认用户目录。"""
    if QSettings is None:
        return default_cache_dir()
    try:
        settings = QSettings(CACHE_SETTINGS_ORG, CACHE_SETTINGS_APP)
        return _normalize_cache_dir(settings.value(CACHE_DIR_SETTING_KEY, "", type=str))
    except Exception:
        return default_cache_dir()


def set_cache_dir(path_value: str | os.PathLike | None) -> Path:
    """保存显示缓存目录设置，并返回规范化后的路径。"""
    cache_dir = _normalize_cache_dir(path_value)
    if QSettings is not None:
        settings = QSettings(CACHE_SETTINGS_ORG, CACHE_SETTINGS_APP)
        settings.setValue(CACHE_DIR_SETTING_KEY, str(cache_dir))
        settings.sync()
    return cache_dir


def _is_inside_cache_dir(path: Path) -> bool:
    """_is_inside_cache_dir。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        path (Path): 输入参数。
    返回:
        bool: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    try:
        path.resolve().relative_to(get_cache_dir())
        return True
    except ValueError:
        return False
    except OSError:
        return False


def needs_display_pyramid(file_path: str, threshold_mb: int | float | None) -> bool:
    """needs_display_pyramid。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        file_path (str): 输入参数。
        threshold_mb (int | float | None): 输入参数。
    返回:
        bool: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    if not file_path or not os.path.exists(file_path):
        return False
    threshold = DEFAULT_PYRAMID_THRESHOLD_MB if threshold_mb is None else float(threshold_mb)
    if threshold <= 0:
        return True
    return os.path.getsize(file_path) >= threshold * 1024 * 1024


def has_gdal_overviews(file_path: str) -> bool:
    """has_gdal_overviews。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        file_path (str): 输入参数。
    返回:
        bool: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    if not file_path or not os.path.exists(file_path):
        return False
    ds = gdal.Open(os.path.normpath(file_path), gdal.GA_ReadOnly)
    if ds is None or ds.RasterCount == 0:
        return False
    try:
        band = ds.GetRasterBand(1)
        return bool(band is not None and band.GetOverviewCount() > 0)
    finally:
        ds = None


def _remove_external_overview(file_path: str) -> None:
    """_remove_external_overview。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        file_path (str): 输入参数。
    返回:
        None: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    try:
        Path(f"{os.path.normpath(file_path)}.ovr").unlink(missing_ok=True)
    except OSError:
        pass


def read_gdal_pyramid_display(
    file_path: str,
    threshold_mb: int | float | None = DEFAULT_PYRAMID_THRESHOLD_MB,
    max_size: int = DISPLAY_OVERVIEW_MAX_SIZE,
) -> Tuple[Optional[np.ndarray], Optional[float], Optional[Tuple[int, int]], int, str]:
    """
    从 GDAL 数据集读取显示预览；大于阈值时优先建立并使用 overview。
    """
    display_path = file_path
    if has_gdal_overviews(file_path):
        display_path = file_path
    elif needs_display_pyramid(file_path, threshold_mb):
        display_path = cached_gdal_dataset_path(file_path)
        ensure_gdal_overviews(display_path)
    data, nodata, original_size, factor = read_tiff_downsampled(display_path, max_size)
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
    """ensure_gamma_vrt。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        file_path (str): 输入参数。
        width (int): 输入参数。
        height (int): 输入参数。
        gamma_format (str): 输入参数。
    返回:
        str: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    dtype, is_complex = _parse_format(gamma_format)
    gdal_type = _gamma_gdal_type(dtype, is_complex)
    bytes_per_pixel = dtype.itemsize * (2 if is_complex else 1)
    source_path = Path(file_path).resolve()
    source_stat = source_path.stat()
    key = (
        f"{source_path}::{source_stat.st_size}::{source_stat.st_mtime_ns}::"
        f"{int(width)}::{int(height)}::{gamma_format}"
    )
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    path = get_cache_dir() / f"gamma_{digest}.vrt"
    source_name = str(source_path)
    byte_order = "MSB"
    xml = f"""<VRTDataset rasterXSize="{int(width)}" rasterYSize="{int(height)}">
  <VRTRasterBand dataType="{gdal_type}" band="1" subClass="VRTRawRasterBand">
    <SourceFilename relativeToVRT="0">{_xml_escape(source_name)}</SourceFilename>
    <ImageOffset>0</ImageOffset>
    <PixelOffset>{bytes_per_pixel}</PixelOffset>
    <LineOffset>{int(width) * bytes_per_pixel}</LineOffset>
    <ByteOrder>{byte_order}</ByteOrder>
  </VRTRasterBand>
</VRTDataset>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text(encoding="utf-8", errors="ignore") != xml:
        path.write_text(xml, encoding="utf-8")
    return str(path)


def cached_gdal_dataset_path(file_path: str) -> str:
    """
    为普通 GDAL 栅格创建缓存目录内的 VRT。

    overview 会建立在 VRT 旁边，避免把显示缓存写到用户原始影像目录。
    已经位于缓存目录内的派生 GeoTIFF 或 VRT 会直接返回。
    """
    source_path = Path(file_path).resolve()
    if source_path.suffix.lower() == ".vrt" or _is_inside_cache_dir(source_path):
        return str(source_path)

    source_stat = source_path.stat()
    key = f"{source_path}::{source_stat.st_size}::{source_stat.st_mtime_ns}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    vrt_path = get_cache_dir() / f"gdal_{digest}.vrt"
    vrt_path.parent.mkdir(parents=True, exist_ok=True)
    if not vrt_path.exists():
        dataset = gdal.Translate(str(vrt_path), str(source_path), format="VRT")
        if dataset is None:
            return str(source_path)
        dataset.FlushCache()
        dataset = None
    return str(vrt_path)


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
        cleanup_pyramid_cache_async(CACHE_MAX_AGE_DAYS)
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
    """read_h5_timeseries_frame_pyramid_display。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        file_path (str): 输入参数。
        frame_index (int): 输入参数。
        threshold_mb (int | float | None): 输入参数。
    返回:
        Tuple[Optional[np.ndarray], Optional[Tuple[int, int]], int, str]: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    return read_h5_dataset_pyramid_display(file_path, "timeseries", frame_index, threshold_mb)


def ensure_gdal_overviews(
    file_path: str,
    levels: Optional[list[int]] = None,
    force_external: bool = True,
) -> tuple[bool, list[int]]:
    """ensure_gdal_overviews。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        file_path (str): 输入参数。
        levels (Optional[list[int]]): 输入参数。
        force_external (bool): 输入参数。
    返回:
        tuple[bool, list[int]]: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    normalized = os.path.normpath(file_path)
    external_ovr = f"{normalized}.ovr"
    ds = gdal.Open(normalized, gdal.GA_ReadOnly)
    if ds is None or ds.RasterCount == 0:
        return False, []
    try:
        band = ds.GetRasterBand(1)
        if band.GetOverviewCount() > 0 and (not force_external or os.path.exists(external_ovr)):
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
        try:
            result = ds.BuildOverviews("AVERAGE", levels)
        except RuntimeError:
            result = 1
    finally:
        ds = None
    if result == 0 and (not force_external or os.path.exists(external_ovr)):
        return True, levels

    ds = gdal.Open(normalized, gdal.GA_Update)
    if ds is None or ds.RasterCount == 0:
        return False, []
    try:
        result = ds.BuildOverviews("AVERAGE", levels)
        ds.FlushCache()
        return (result == 0), (levels if result == 0 else [])
    finally:
        ds = None


def _read_h5_display_source(file_path: str, dataset_name: str, frame_index: Optional[int]):
    """_read_h5_display_source。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        file_path (str): 输入参数。
        dataset_name (str): 输入参数。
        frame_index (Optional[int]): 输入参数。
    返回:
        None: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
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
    """_h5_dataset_layout。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        file_path (str): 输入参数。
        dataset_name (str): 输入参数。
        frame_index (Optional[int]): 输入参数。
    返回:
        None: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
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
    """_write_h5_dataset_geotiff_cache。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        path (Path): 输入参数。
        file_path (str): 输入参数。
        dataset_name (str): 输入参数。
        frame_index (Optional[int]): 输入参数。
        band_count (int): 输入参数。
    返回:
        None: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
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
    """_write_array_geotiff。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        path (Path): 输入参数。
        data (np.ndarray): 输入参数。
    返回:
        None: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
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
    """_cache_is_stale。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        cache_path (Path): 输入参数。
        source_path (str): 输入参数。
    返回:
        bool: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    if not cache_path.exists():
        return True
    return cache_path.stat().st_mtime < Path(source_path).stat().st_mtime


def _h5_cache_path(file_path: str, dataset_name: str, frame_index: Optional[int]) -> Path:
    """_h5_cache_path。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        file_path (str): 输入参数。
        dataset_name (str): 输入参数。
        frame_index (Optional[int]): 输入参数。
    返回:
        Path: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    key = f"{Path(file_path).resolve()}::{dataset_name}::{frame_index}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return get_cache_dir() / f"{digest}.tif"


def cleanup_pyramid_cache(max_age_days: int = CACHE_MAX_AGE_DAYS) -> None:
    """cleanup_pyramid_cache。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        max_age_days (int): 输入参数。
    返回:
        None: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    cache_dir = get_cache_dir()
    if not cache_dir.exists():
        return
    cutoff = time.time() - max(1, int(max_age_days)) * 86400
    for path in cache_dir.glob("*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def cleanup_pyramid_cache_async(max_age_days: int = CACHE_MAX_AGE_DAYS) -> None:
    """在后台线程清理过期显示/派生缓存，避免阻塞界面启动和渲染。"""
    global _cleanup_thread
    if _cleanup_thread is not None and _cleanup_thread.is_alive():
        return

    def _worker() -> None:
        """_worker。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        with _cleanup_lock:
            cleanup_pyramid_cache(max_age_days)

    _cleanup_thread = threading.Thread(target=_worker, name="toolbox-pyramid-cache-cleanup", daemon=True)
    _cleanup_thread.start()


def derived_cache_path(source_path: str, operation_key: str) -> Path:
    """derived_cache_path。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        source_path (str): 输入参数。
        operation_key (str): 输入参数。
    返回:
        Path: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    key = f"{Path(source_path).resolve()}::{Path(source_path).stat().st_mtime_ns if Path(source_path).exists() else 0}::{operation_key}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return get_cache_dir() / f"derived_{digest}.tif"


def stable_derived_cache_path(source_path: str, operation_key: str) -> Path:
    """stable_derived_cache_path。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        source_path (str): 输入参数。
        operation_key (str): 输入参数。
    返回:
        Path: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    key = f"{Path(source_path).resolve()}::{operation_key}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return get_cache_dir() / f"derived_{digest}.tif"


def write_derived_raster_cache(
    source,
    operation_key: str,
    transform: Callable[[np.ndarray], np.ndarray],
    block_rows: int = 512,
    output_band_count: int | None = None,
    pyramid_threshold_mb: int | float | None = DEFAULT_PYRAMID_THRESHOLD_MB,
) -> Path:
    """按块生成派生 GeoTIFF 缓存，并按缓存文件体积决定是否建立 overview。"""
    meta = source.metadata()
    cache_path = derived_cache_path(meta.path, operation_key)
    if cache_path.exists() and not _cache_is_stale(cache_path, meta.path):
        try:
            cache_path.touch()
        except OSError:
            pass
        cleanup_pyramid_cache_async(CACHE_MAX_AGE_DAYS)
        if needs_display_pyramid(str(cache_path), pyramid_threshold_mb):
            ensure_gdal_overviews(str(cache_path))
        else:
            _remove_external_overview(str(cache_path))
        return cache_path

    cleanup_pyramid_cache_async(CACHE_MAX_AGE_DAYS)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(
        str(cache_path),
        int(meta.width),
        int(meta.height),
        int(output_band_count or meta.band_count),
        gdal.GDT_Float32,
        options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"],
    )
    if dataset is None:
        raise RuntimeError(f"无法创建派生图像缓存: {cache_path}")
    if meta.geotransform is not None:
        dataset.SetGeoTransform(meta.geotransform)
    if meta.crs_wkt:
        dataset.SetProjection(meta.crs_wkt)
    if meta.nodata is not None:
        for index in range(int(output_band_count or meta.band_count)):
            dataset.GetRasterBand(index + 1).SetNoDataValue(float(meta.nodata))

    for y0 in range(0, int(meta.height), max(1, int(block_rows))):
        height = min(max(1, int(block_rows)), int(meta.height) - y0)
        raw = source.read_window_native(0, y0, int(meta.width), height)
        try:
            derived = np.asarray(transform(raw, y0), dtype=np.float32)
        except TypeError:
            derived = np.asarray(transform(raw), dtype=np.float32)
        if derived.ndim == 2:
            dataset.GetRasterBand(1).WriteArray(derived, 0, y0)
        else:
            for band_index in range(min(int(output_band_count or meta.band_count), derived.shape[2])):
                dataset.GetRasterBand(band_index + 1).WriteArray(derived[:, :, band_index], 0, y0)

    dataset.FlushCache()
    dataset = None
    if needs_display_pyramid(str(cache_path), pyramid_threshold_mb):
        ensure_gdal_overviews(str(cache_path))
    else:
        _remove_external_overview(str(cache_path))
    return cache_path


def write_full_derived_raster_cache(
    source,
    operation_key: str,
    transform: Callable[[np.ndarray], np.ndarray],
    output_band_count: int | None = None,
    nodata_value: float | None = None,
    invalidate_on_source_mtime: bool = True,
    stable_cache_key: bool = False,
    pyramid_threshold_mb: int | float | None = DEFAULT_PYRAMID_THRESHOLD_MB,
) -> Path:
    """一次性读取整幅图生成派生 GeoTIFF 缓存，并按缓存文件体积决定是否建立 overview。"""
    meta = source.metadata()
    cache_path = (
        stable_derived_cache_path(meta.path, operation_key)
        if stable_cache_key
        else derived_cache_path(meta.path, operation_key)
    )
    cache_is_usable = cache_path.exists() and (
        not invalidate_on_source_mtime or not _cache_is_stale(cache_path, meta.path)
    )
    if cache_is_usable:
        try:
            cache_path.touch()
        except OSError:
            pass
        cleanup_pyramid_cache_async(CACHE_MAX_AGE_DAYS)
        if needs_display_pyramid(str(cache_path), pyramid_threshold_mb):
            ensure_gdal_overviews(str(cache_path))
        else:
            _remove_external_overview(str(cache_path))
        return cache_path

    cleanup_pyramid_cache_async(CACHE_MAX_AGE_DAYS)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    raw = source.read_window_native(0, 0, int(meta.width), int(meta.height))
    derived = np.asarray(transform(raw), dtype=np.float32)
    if derived.ndim == 2:
        band_count = int(output_band_count or 1)
    else:
        band_count = int(output_band_count or derived.shape[2])

    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(
        str(cache_path),
        int(meta.width),
        int(meta.height),
        band_count,
        gdal.GDT_Float32,
        options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"],
    )
    if dataset is None:
        raise RuntimeError(f"无法创建派生图像缓存: {cache_path}")
    if meta.geotransform is not None:
        dataset.SetGeoTransform(meta.geotransform)
    if meta.crs_wkt:
        dataset.SetProjection(meta.crs_wkt)
    if nodata_value is not None:
        for index in range(band_count):
            dataset.GetRasterBand(index + 1).SetNoDataValue(float(nodata_value))

    if derived.ndim == 2:
        dataset.GetRasterBand(1).WriteArray(derived)
    else:
        for band_index in range(min(band_count, derived.shape[2])):
            dataset.GetRasterBand(band_index + 1).WriteArray(derived[:, :, band_index])
    dataset.FlushCache()
    dataset = None
    if needs_display_pyramid(str(cache_path), pyramid_threshold_mb):
        ensure_gdal_overviews(str(cache_path))
    else:
        _remove_external_overview(str(cache_path))
    return cache_path


def _gamma_gdal_type(dtype: np.dtype, is_complex: bool) -> str:
    """_gamma_gdal_type。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        dtype (np.dtype): 输入参数。
        is_complex (bool): 输入参数。
    返回:
        str: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
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
    """_xml_escape。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        value (str): 输入参数。
    返回:
        str: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
