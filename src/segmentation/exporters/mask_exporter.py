"""
掩膜导出。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
from osgeo import gdal

from ..geometry_service import GeometryService
from ..models import SegmentationProject


def export_mask_file(
    project: SegmentationProject,
    output_path: str,
    binary_label_id: int | None = None,
    *,
    encoding: str = "colored",
    colored: bool | None = None,
) -> None:
    """导出分割 Mask。

    ``indexed`` 模式直接输出单通道 Mask 类别值：0 为背景，其余值保持
    用户在标签面板中设定的值。``colored`` 模式用于可视化输出。
    ``colored`` 参数保留为旧调用方的兼容入口。
    """
    if project.image_asset is None:
        raise ValueError("缺少图像元信息，无法导出掩膜")
    if colored is not None:
        encoding = "colored" if colored else "indexed"
    if encoding not in {"indexed", "colored"}:
        raise ValueError(f"不支持的 Mask 编码：{encoding}")

    width = project.image_asset.width
    height = project.image_asset.height
    mask = project.mask_data
    if mask is None:
        mask = GeometryService.rasterize_annotations(
            project.annotations,
            width,
            height,
            binary_label_id=binary_label_id,
        )
    elif binary_label_id is not None:
        mask = np.where(mask == binary_label_id, binary_label_id, 0).astype(mask.dtype)

    suffix = Path(output_path).suffix.lower()
    if suffix not in {".png", ".bmp", ".tif", ".tiff"}:
        raise ValueError("Mask 仅支持导出为 PNG、BMP 或 GeoTIFF 格式")

    if encoding == "indexed":
        indexed_mask = _preserve_mask_values(mask)
        if suffix == ".bmp" and indexed_mask.dtype != np.uint8:
            raise ValueError("BMP 单通道标签导出最多支持 255 个标签，请改用 PNG 或 GeoTIFF。")
        if suffix in {".png", ".bmp"}:
            Image.fromarray(indexed_mask).save(output_path)
            return
        _write_geotiff_mask(project, output_path, indexed_mask)
        return

    if suffix in {".png", ".bmp"}:
        Image.fromarray(_colorize_mask_rgb(mask, project)).save(output_path)
        return

    _write_geotiff_mask(project, output_path, mask, colored=True)


def _preserve_mask_values(mask: np.ndarray) -> np.ndarray:
    """保留当前 Mask 的类别值，包括已删除标签留下的未定义值。"""
    values = np.asarray(mask)
    if np.any(values < 0) or np.any(values > np.iinfo(np.uint16).max):
        raise ValueError("单通道 Mask 类别值必须在 0 到 65535 之间")
    return values.astype(np.uint8 if int(values.max(initial=0)) <= 255 else np.uint16, copy=False)


def _write_geotiff_mask(
    project: SegmentationProject,
    output_path: str,
    mask: np.ndarray,
    *,
    colored: bool = False,
) -> None:
    """写出单波段 GeoTIFF；颜色模式额外写入调色板。"""
    height, width = mask.shape[:2]
    if mask.dtype == np.uint8:
        data_type = gdal.GDT_Byte
    elif mask.dtype == np.uint16:
        data_type = gdal.GDT_UInt16
    else:
        mask = mask.astype(np.uint16)
        data_type = gdal.GDT_UInt16

    driver = gdal.GetDriverByName("GTiff")
    creation_options = ["BIGTIFF=IF_SAFER"]
    if colored:
        creation_options.append("PHOTOMETRIC=PALETTE")

    dataset = driver.Create(output_path, width, height, 1, data_type, options=creation_options)
    if dataset is None:
        raise RuntimeError(f"无法创建 GeoTIFF 文件：{output_path}")
    band = dataset.GetRasterBand(1)
    if colored:
        color_table = gdal.ColorTable()
        color_table.SetColorEntry(0, (0, 0, 0, 0))
        for label in project.labels:
            color = label.color.lstrip("#")
            if len(color) != 6:
                continue
            rgb = tuple(int(color[index:index + 2], 16) for index in (0, 2, 4))
            color_table.SetColorEntry(int(label.id), (*rgb, 255))
        known_values = {int(label.id) for label in project.labels}
        for value in np.unique(mask):
            value = int(value)
            if value != 0 and value not in known_values:
                color_table.SetColorEntry(value, (148, 163, 184, 255))
        band.SetRasterColorTable(color_table)
        band.SetRasterColorInterpretation(gdal.GCI_PaletteIndex)
    band.WriteArray(mask)
    band.SetNoDataValue(0)
    if project.image_asset.geotransform:
        dataset.SetGeoTransform(project.image_asset.geotransform)
    if project.image_asset.crs_wkt:
        dataset.SetProjection(project.image_asset.crs_wkt)
    dataset.FlushCache()
    dataset = None


def _colorize_mask_rgb(mask: np.ndarray, project: SegmentationProject) -> np.ndarray:
    rgb_mask = np.zeros((mask.shape[0], mask.shape[1], 3), dtype=np.uint8)
    for label in project.labels:
        color = label.color.lstrip("#")
        if len(color) != 6:
            continue
        rgb = [int(color[index:index + 2], 16) for index in (0, 2, 4)]
        rgb_mask[mask == int(label.id)] = rgb
    known_values = [int(label.id) for label in project.labels]
    unknown = (mask != 0) & ~np.isin(mask, known_values)
    rgb_mask[unknown] = [148, 163, 184]
    return rgb_mask
