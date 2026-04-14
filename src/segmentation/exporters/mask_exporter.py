"""
掩膜导出。
"""

from __future__ import annotations

from PIL import Image
from osgeo import gdal

from ..geometry_service import GeometryService
from ..models import SegmentationProject


def export_mask_file(
    project: SegmentationProject,
    output_path: str,
    binary_label_id: int | None = None,
) -> None:
    if project.image_asset is None:
        raise ValueError("缺少图像元信息，无法导出掩膜")
    width = project.image_asset.width
    height = project.image_asset.height
    mask = GeometryService.rasterize_annotations(
        project.annotations,
        width,
        height,
        binary_label_id=binary_label_id,
    )
    lower = output_path.lower()
    if lower.endswith(".png"):
        Image.fromarray(mask).save(output_path)
        return

    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(output_path, width, height, 1, gdal.GDT_UInt16)
    dataset.GetRasterBand(1).WriteArray(mask)
    dataset.GetRasterBand(1).SetNoDataValue(0)
    if project.image_asset.geotransform:
        dataset.SetGeoTransform(project.image_asset.geotransform)
    if project.image_asset.crs_wkt:
        dataset.SetProjection(project.image_asset.crs_wkt)
    dataset.FlushCache()
    dataset = None
