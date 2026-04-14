"""
渲染请求。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RenderRequest:
    x: float
    y: float
    width: float
    height: float
    screen_width: int
    screen_height: int
    bands: tuple[int, ...] | None = None
