"""
图像数据源基类。
"""

from __future__ import annotations

from ..models import ImageAsset, RenderTileResult
from .render_request import RenderRequest


class BaseImageSource:
    def metadata(self) -> ImageAsset:
        raise NotImplementedError

    def render(self, request: RenderRequest) -> RenderTileResult:
        raise NotImplementedError

    def build_overviews(self) -> tuple[bool, list[int]]:
        return False, []
