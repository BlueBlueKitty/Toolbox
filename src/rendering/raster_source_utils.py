"""
通用栅格 source 打开辅助。
"""

from __future__ import annotations

from src.rendering.sources import GdalRasterSource, StandardImageSource


def open_raster_source(file_path: str, *, pyramid_threshold_mb: int | float, source_path: str | None = None):
    """优先按 GDAL 栅格打开，失败时回退到普通图像 source。"""
    try:
        return GdalRasterSource(
            file_path,
            source_path=source_path,
            pyramid_threshold_mb=pyramid_threshold_mb,
        )
    except Exception:
        return StandardImageSource(file_path)
