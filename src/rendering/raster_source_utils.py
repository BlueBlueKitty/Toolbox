"""
通用栅格 source 打开辅助。
"""

from __future__ import annotations

from pathlib import Path

from src.rendering.sources import GdalRasterSource, StandardImageSource


# 与图像局部查看器保持一致的普通栅格格式。分割窗口的文件选择、
# 主画布拖拽和辅助栅格导入均应使用这一份定义。
SEGMENTATION_RASTER_EXTENSIONS = (
    ".tif",
    ".tiff",
    ".grd",
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
)
SEGMENTATION_RASTER_FILE_FILTER = "图像文件 (" + " ".join(f"*{ext}" for ext in SEGMENTATION_RASTER_EXTENSIONS) + ")"


def is_segmentation_raster_file(file_path: str) -> bool:
    """判断路径是否为图像分割工具支持的普通栅格格式（大小写无关）。"""
    return Path(file_path).suffix.lower() in SEGMENTATION_RASTER_EXTENSIONS


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
