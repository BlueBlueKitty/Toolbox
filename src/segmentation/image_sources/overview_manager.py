"""
GeoTIFF overview 管理。
"""

from __future__ import annotations

from osgeo import gdal

from ..models import OverviewInfo


def detect_overviews(dataset) -> list[OverviewInfo]:
    if dataset is None or dataset.RasterCount == 0:
        return []
    band = dataset.GetRasterBand(1)
    overview_count = band.GetOverviewCount()
    if overview_count <= 0:
        return []

    results = []
    for index in range(overview_count):
        overview = band.GetOverview(index)
        factor = dataset.RasterXSize / max(overview.XSize, 1)
        source_type = "external" if str(dataset.GetDescription()).lower().endswith(".ovr") else "internal"
        results.append(
            OverviewInfo(
                level_index=index,
                downsample_factor=float(factor),
                width=overview.XSize,
                height=overview.YSize,
                source_type=source_type,
            )
        )
    return results


def choose_overview_for_scale(overviews: list[OverviewInfo], target_downsample: float) -> OverviewInfo | None:
    if not overviews:
        return None
    sorted_overviews = sorted(overviews, key=lambda item: item.downsample_factor)
    for overview in sorted_overviews:
        if overview.downsample_factor >= max(target_downsample, 1.0):
            return overview
    return sorted_overviews[-1]


def build_overviews(file_path: str, levels: list[int] | None = None, resample: str = "AVERAGE") -> tuple[bool, list[int]]:
    dataset = gdal.Open(file_path, gdal.GA_Update)
    if dataset is None:
        return False, []
    try:
        width = dataset.RasterXSize
        height = dataset.RasterYSize
        if levels is None:
            levels = []
            factor = 2
            while min(width, height) / factor > 256:
                levels.append(factor)
                factor *= 2
            if factor not in levels:
                levels.append(factor)
        dataset.BuildOverviews(resample, levels)
        return True, levels
    finally:
        dataset = None
