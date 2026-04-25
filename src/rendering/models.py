"""
通用渲染数据模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class OverviewInfo:
    level_index: int
    downsample_factor: float
    width: int
    height: int
    source_type: str


@dataclass
class ImageSourceMetadata:
    id: str
    path: str
    path_mode: str
    width: int
    height: int
    band_count: int
    dtype: str
    nodata: Optional[float]
    crs_wkt: Optional[str]
    geotransform: Optional[tuple]
    resolution: Optional[tuple]
    has_georef: bool
    has_color_table: bool = False
    color_table: Optional[list[tuple[int, int, int, int]]] = None
    overview_levels: list[OverviewInfo] = field(default_factory=list)
    color_interpretations: list[str] = field(default_factory=list)
    custom_properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class ViewportState:
    center_x: float = 0.0
    center_y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    viewport_width: float = 0.0
    viewport_height: float = 0.0


@dataclass
class RenderRequest:
    x: float
    y: float
    width: float
    height: float
    screen_width: int
    screen_height: int
    bands: tuple[int, ...] | None = None
    layer_id: str | None = None
    device_pixel_ratio: float = 1.0


@dataclass
class RawRasterBlock:
    data: Any
    image_rect: tuple[float, float, float, float]
    source_window: tuple[int, int, int, int]
    overview_level: Optional[OverviewInfo] = None
    nodata_value: Any = None
    mask: Any = None
    alpha: Any = None
    metadata: ImageSourceMetadata | None = None
    band_indices: tuple[int, ...] | None = None
    custom_properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class RenderTileResult:
    raw_array: Any
    display_rgb: Any
    image_rect: tuple[float, float, float, float]
    overview_level: Optional[OverviewInfo]
    source_window: tuple[int, int, int, int]
    layer_id: str | None = None
    cache_key: tuple | None = None


@dataclass
class LayerSpec:
    id: str
    name: str
    layer_type: str
    visible: bool = True
    opacity: float = 1.0
    locked: bool = False
    selectable: bool = True
    blend_mode: str = "source_over"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RasterLayer:
    id: str
    name: str
    source: Any
    metadata: ImageSourceMetadata
    render_style: Any
    display_settings: Any
    visible: bool = True
    selected: bool = False
    locked: bool = False
    revision: int = 0
    custom_properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class LayerState:
    spec: LayerSpec
    z_order: int = 0
    item: Any = field(default=None, repr=False, compare=False)
    layer: RasterLayer | None = None
