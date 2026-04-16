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
        Image.fromarray(mask).save(output_path)
        return

    driver = gdal.GetDriverByName("GTiff")
    if colored:
        dataset = driver.Create(output_path, width, height, 1, gdal.GDT_UInt16)
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
        dataset = driver.Create(output_path, width, height, 1, gdal.GDT_UInt16)
        dataset.GetRasterBand(1).WriteArray(mask)
        dataset.GetRasterBand(1).SetNoDataValue(0)
    if project.image_asset.geotransform:
        dataset.SetGeoTransform(project.image_asset.geotransform)
    if project.image_asset.crs_wkt:
        dataset.SetProjection(project.image_asset.crs_wkt)
    dataset.FlushCache()
    dataset = None
