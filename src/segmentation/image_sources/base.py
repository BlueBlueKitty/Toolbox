"""
图像数据源基类。
"""

from __future__ import annotations

from ..models import ImageAsset, RenderTileResult
from ..rendering import SegmentationRenderConfig
from .render_request import RenderRequest


class BaseImageSource:
    def metadata(self) -> ImageAsset:
        raise NotImplementedError

    def render(self, request: RenderRequest, render_config: SegmentationRenderConfig) -> RenderTileResult:
        raise NotImplementedError

    def read_pixel(self, x: int, y: int):
        raise NotImplementedError

    def build_overviews(self) -> tuple[bool, list[int]]:
        return False, []
