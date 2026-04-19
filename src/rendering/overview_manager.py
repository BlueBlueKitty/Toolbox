"""
GDAL overview 检测、选择与创建。
"""

from __future__ import annotations

from osgeo import gdal

from .models import OverviewInfo


def detect_overviews(dataset) -> list[OverviewInfo]:
    if dataset is None or dataset.RasterCount <= 0:
        return []
    band = dataset.GetRasterBand(1)
    if band is None:
        return []
    levels: list[OverviewInfo] = []
    for index in range(band.GetOverviewCount()):
        overview = band.GetOverview(index)
        if overview is None:
            continue
        width = int(overview.XSize)
        height = int(overview.YSize)
        factor = max(dataset.RasterXSize / max(width, 1), dataset.RasterYSize / max(height, 1))
        levels.append(
            OverviewInfo(
                level_index=index,
                downsample_factor=float(factor),
                width=width,
                height=height,
                source_type="gdal",
            )
        )
    return levels


def choose_overview_for_scale(overviews: list[OverviewInfo], target_downsample: float) -> OverviewInfo | None:
    candidates = [overview for overview in overviews if overview.downsample_factor <= target_downsample]
    if not candidates:
        return None
    return max(candidates, key=lambda overview: overview.downsample_factor)


def build_overviews(file_path: str, levels=None, progress_callback=None) -> tuple[bool, list[int]]:
    dataset = gdal.Open(str(file_path), gdal.GA_Update)
    if dataset is None:
        return False, []
    if levels is None:
        levels = []
        factor = 2
        while min(dataset.RasterXSize // factor, dataset.RasterYSize // factor) >= 256:
            levels.append(factor)
            factor *= 2
    if not levels:
        return True, []

    def _callback(complete, _message, _data):
        if progress_callback is not None:
            progress_callback(int(max(0.0, min(1.0, complete)) * 100), "正在创建金字塔...")
        return 1

    result = dataset.BuildOverviews("AVERAGE", list(levels), callback=_callback)
    dataset.FlushCache()
    dataset = None
    if result == 0:
        if progress_callback is not None:
            progress_callback(100, "金字塔创建完成")
        return True, list(levels)
    return False, []
