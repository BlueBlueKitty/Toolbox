"""
GDAL overview 检测、选择与创建。
"""

from __future__ import annotations

from osgeo import gdal

from .models import OverviewInfo


def detect_overviews(dataset) -> list[OverviewInfo]:
    """detect_overviews。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        dataset (Any): 输入参数。
    返回:
        list[OverviewInfo]: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
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
    """choose_overview_for_scale。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        overviews (list[OverviewInfo]): 输入参数。
        target_downsample (float): 输入参数。
    返回:
        OverviewInfo | None: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    candidates = [overview for overview in overviews if overview.downsample_factor <= target_downsample]
    if not candidates:
        return None
    return max(candidates, key=lambda overview: overview.downsample_factor)


def build_overviews(file_path: str, levels=None, progress_callback=None) -> tuple[bool, list[int]]:
    """build_overviews。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        file_path (str): 输入参数。
        levels (Any): 输入参数。
        progress_callback (Any): 输入参数。
    返回:
        tuple[bool, list[int]]: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
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
        """_callback。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            complete (Any): 输入参数。
            _message (Any): 输入参数。
            _data (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
