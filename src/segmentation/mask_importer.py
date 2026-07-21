"""外部分类 Mask 的读取、校验与网格对齐。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from osgeo import gdal, osr

from src.rendering.models import ImageSourceMetadata


SUPPORTED_MASK_SUFFIXES = {".tif", ".tiff", ".png", ".bmp"}


def import_mask_for_image(file_path: str, image: ImageSourceMetadata) -> tuple[np.ndarray, dict]:
    """读取单波段分类 Mask，并对齐到 ``image`` 的像素网格。

    只有源与目标均带完整空间参考时才允许重投影；其他情况必须同尺寸。
    """
    path = Path(file_path)
    if path.suffix.lower() not in SUPPORTED_MASK_SUFFIXES:
        raise ValueError("Mask 仅支持 GeoTIFF、PNG 或 BMP 格式")
    source = gdal.Open(str(path), gdal.GA_ReadOnly)
    if source is None:
        raise ValueError(f"无法打开 Mask 文件：{path}")
    if source.RasterCount != 1:
        raise ValueError("仅支持单波段分类 Mask，不支持 RGB 或多波段影像")
    source_band = source.GetRasterBand(1)
    nodata = source_band.GetNoDataValue()
    # 先验证原始范围，避免写入 UInt16 重投影目标时静默截断非法值。
    _validate_mask_values(source_band.ReadAsArray(), nodata)

    same_grid = _same_grid(source, image)
    aligned = False
    if same_grid:
        array = source_band.ReadAsArray()
    elif _has_complete_georef(source) and _image_has_complete_georef(image):
        array = _warp_to_image_grid(source, image)
        aligned = True
    elif source.RasterXSize == image.width and source.RasterYSize == image.height:
        array = source_band.ReadAsArray()
    else:
        raise ValueError("Mask 与当前影像尺寸不一致，且缺少可用于最近邻重投影的完整空间参考")

    array = _validate_mask_values(array, nodata)
    values = [int(value) for value in np.unique(array) if int(value) != 0]
    return array, {
        "source_path": str(path.resolve()),
        "source_kind": "imported",
        "background_value": 0,
        "aligned_to_image": aligned,
        "source_nodata": None if nodata is None else float(nodata),
        "values": values,
    }


def _same_grid(source, image: ImageSourceMetadata) -> bool:
    if source.RasterXSize != image.width or source.RasterYSize != image.height:
        return False
    source_gt = source.GetGeoTransform(can_return_null=True)
    if source_gt is None and image.geotransform is None:
        return True
    if source_gt is None or image.geotransform is None:
        return False
    if not np.allclose(source_gt, image.geotransform, rtol=0.0, atol=1e-8):
        return False
    return _same_projection(source.GetProjection(), image.crs_wkt)


def _same_projection(first: str | None, second: str | None) -> bool:
    if not first and not second:
        return True
    if not first or not second:
        return False
    left, right = osr.SpatialReference(), osr.SpatialReference()
    if left.ImportFromWkt(first) != 0 or right.ImportFromWkt(second) != 0:
        return first == second
    return bool(left.IsSame(right))


def _has_complete_georef(dataset) -> bool:
    return bool(dataset.GetProjection() and dataset.GetGeoTransform(can_return_null=True))


def _image_has_complete_georef(image: ImageSourceMetadata) -> bool:
    return bool(image.crs_wkt and image.geotransform)


def _warp_to_image_grid(source, image: ImageSourceMetadata) -> np.ndarray:
    driver = gdal.GetDriverByName("MEM")
    target = driver.Create("", int(image.width), int(image.height), 1, gdal.GDT_UInt16)
    target.SetGeoTransform(tuple(image.geotransform))
    target.SetProjection(str(image.crs_wkt))
    result = gdal.ReprojectImage(
        source,
        target,
        source.GetProjection(),
        str(image.crs_wkt),
        gdal.GRA_NearestNeighbour,
    )
    if result != gdal.CE_None:
        raise RuntimeError("无法使用最近邻方式将 Mask 对齐到当前影像")
    array = target.GetRasterBand(1).ReadAsArray()
    target = None
    return array


def _validate_mask_values(array: np.ndarray, nodata) -> np.ndarray:
    if array is None or array.ndim != 2:
        raise ValueError("无法读取有效的二维 Mask 数据")
    values = np.asarray(array)
    if np.issubdtype(values.dtype, np.floating):
        if not np.all(np.isfinite(values)) or not np.allclose(values, np.rint(values)):
            raise ValueError("Mask 必须为整数类别值")
    if nodata is not None:
        values = values.copy()
        values[values == nodata] = 0
    if np.any(values < 0) or np.any(values > np.iinfo(np.uint16).max):
        raise ValueError("Mask 分类值必须在 0 到 65535 之间")
    return values.astype(np.uint16, copy=False)
