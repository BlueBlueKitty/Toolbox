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
) -> None:
    """导出单通道分类 Mask，并按标签面板顺序编号类别值。"""
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

    suffix = Path(output_path).suffix.lower()
    if suffix not in {".png", ".bmp", ".tif", ".tiff"}:
        raise ValueError("Mask 仅支持导出为 PNG、BMP 或 GeoTIFF 格式")

    indexed_mask, palette = _build_indexed_mask_and_palette(mask, project, binary_label_id)
    if suffix in {".png", ".bmp"}:
        if len(palette) > 256:
            raise ValueError("PNG/BMP 最多支持 255 个标签，请改用 GeoTIFF。")
        _write_paletted_image(output_path, indexed_mask, palette)
        return
    _write_geotiff_mask(project, output_path, indexed_mask, palette)


def _build_indexed_mask_and_palette(
    mask: np.ndarray,
    project: SegmentationProject,
    binary_label_id: int | None,
) -> tuple[np.ndarray, list[tuple[int, int, int, int]]]:
    """Map project label IDs to stable exported indices and their display colours."""
    raw = np.asarray(mask)
    labels = list(project.labels)
    label_ids = [int(label.id) for label in labels]
    known_values = set(label_ids)
    unknown_values = sorted(int(value) for value in np.unique(raw) if int(value) != 0 and int(value) not in known_values)
    if unknown_values:
        values = "、".join(str(value) for value in unknown_values)
        raise ValueError(f"Mask 中存在未定义的标签 ID：{values}")

    if binary_label_id is not None:
        if int(binary_label_id) not in known_values:
            raise ValueError(f"未定义的标签 ID：{int(binary_label_id)}")
        indexed = np.where(raw == int(binary_label_id), 1, 0).astype(np.uint8)
        label = next(item for item in labels if int(item.id) == int(binary_label_id))
        return indexed, [(0, 0, 0, 0), _label_rgba(label.color)]

    dtype = np.uint8 if len(labels) <= 255 else np.uint16
    indexed = np.zeros(raw.shape, dtype=dtype)
    palette = [(0, 0, 0, 0)]
    for export_value, label in enumerate(labels, start=1):
        indexed[raw == int(label.id)] = export_value
        palette.append(_label_rgba(label.color))
    return indexed, palette


def _label_rgba(color: str) -> tuple[int, int, int, int]:
    value = str(color or "").lstrip("#")
    if len(value) != 6:
        return (148, 163, 184, 255)
    try:
        return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4)) + (255,)
    except ValueError:
        return (148, 163, 184, 255)


def _write_paletted_image(output_path: str, mask: np.ndarray, palette: list[tuple[int, int, int, int]]) -> None:
    image = Image.fromarray(np.asarray(mask, dtype=np.uint8), mode="P")
    rgb_palette = [component for rgba in palette for component in rgba[:3]]
    image.putpalette((rgb_palette + [0] * 768)[:768])
    image.save(output_path)


def _write_geotiff_mask(
    project: SegmentationProject,
    output_path: str,
    mask: np.ndarray,
    palette: list[tuple[int, int, int, int]],
) -> None:
    """写出单波段 GeoTIFF。"""
    height, width = mask.shape[:2]
    if mask.dtype == np.uint8:
        data_type = gdal.GDT_Byte
    elif mask.dtype == np.uint16:
        data_type = gdal.GDT_UInt16
    else:
        mask = mask.astype(np.uint16)
        data_type = gdal.GDT_UInt16

    driver = gdal.GetDriverByName("GTiff")
    creation_options = ["BIGTIFF=IF_SAFER", "PHOTOMETRIC=PALETTE"]

    dataset = driver.Create(output_path, width, height, 1, data_type, options=creation_options)
    if dataset is None:
        raise RuntimeError(f"无法创建 GeoTIFF 文件：{output_path}")
    band = dataset.GetRasterBand(1)
    color_table = gdal.ColorTable()
    for index, rgba in enumerate(palette):
        color_table.SetColorEntry(index, rgba)
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
