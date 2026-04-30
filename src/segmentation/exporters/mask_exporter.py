"""
掩膜导出。
"""

from __future__ import annotations

import numpy as np
from PIL import Image
from osgeo import gdal

from ..geometry_service import GeometryService
from ..models import SegmentationProject


def export_mask_file(
    project: SegmentationProject,
    output_path: str,
    binary_label_id: int | None = None,
    colored: bool = False,
) -> None:
    """export_mask_file。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        project (SegmentationProject): 输入参数。
        output_path (str): 输入参数。
        binary_label_id (int | None): 输入参数。
        colored (bool): 输入参数。
    返回:
        None: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    if project.image_asset is None:
        raise ValueError("缺少图像元信息，无法导出掩膜")
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
    lower = output_path.lower()
    if lower.endswith(".png"):
        Image.fromarray(_colorize_mask_rgb(mask, project)).save(output_path)
        return

    driver = gdal.GetDriverByName("GTiff")
    creation_options = ["BIGTIFF=IF_SAFER"]
    if colored:
        dataset = driver.Create(output_path, width, height, 1, gdal.GDT_UInt16, options=creation_options)
        band = dataset.GetRasterBand(1)
        band.WriteArray(mask)
        band.SetNoDataValue(0)
        color_table = gdal.ColorTable()
        color_table.SetColorEntry(0, (0, 0, 0, 0))
        for label in project.labels:
            color = label.color.lstrip("#")
            if len(color) != 6:
                continue
            rgb = tuple(int(color[index:index + 2], 16) for index in (0, 2, 4))
            color_table.SetColorEntry(int(label.id), (*rgb, 255))
        band.SetRasterColorTable(color_table)
        band.SetRasterColorInterpretation(gdal.GCI_PaletteIndex)
    else:
        dataset = driver.Create(output_path, width, height, 1, gdal.GDT_UInt16, options=creation_options)
        dataset.GetRasterBand(1).WriteArray(mask)
        dataset.GetRasterBand(1).SetNoDataValue(0)
    if project.image_asset.geotransform:
        dataset.SetGeoTransform(project.image_asset.geotransform)
    if project.image_asset.crs_wkt:
        dataset.SetProjection(project.image_asset.crs_wkt)
    dataset.FlushCache()
    dataset = None


def _colorize_mask_rgb(mask: np.ndarray, project: SegmentationProject) -> np.ndarray:
    """_colorize_mask_rgb。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        mask (np.ndarray): 输入参数。
        project (SegmentationProject): 输入参数。
    返回:
        np.ndarray: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    rgb_mask = np.zeros((mask.shape[0], mask.shape[1], 3), dtype=np.uint8)
    for label in project.labels:
        color = label.color.lstrip("#")
        if len(color) != 6:
            continue
        rgb = [int(color[index:index + 2], 16) for index in (0, 2, 4)]
        rgb_mask[mask == int(label.id)] = rgb
    return rgb_mask
