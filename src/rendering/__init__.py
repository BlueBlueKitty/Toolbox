"""
通用栅格渲染与图层底层。
"""

from .config import RasterRenderConfig, default_raster_render_config, render_raster_rgb
from .canvas import LayeredRasterCanvas, RasterCanvasSynchronizer
from .layers import LayerManager
from .layer_panel_controller import LayerPanelController
from .layer_operations import (
    NON_REMOVABLE_LAYER_IDS,
    is_layer_removable,
    nodata_to_text,
    ui_index_to_z_index,
    z_index_to_ui_index,
)
from .models import (
    ImageSourceMetadata,
    LayerSpec,
    LayerState,
    OverviewInfo,
    RenderRequest,
    RenderTileResult,
    ViewportState,
)
from .sources import (
    GammaVrtRasterSource,
    GdalRasterSource,
    H5DatasetRasterSource,
    RasterImageSource,
    StandardImageSource,
)

__all__ = [
    "GammaVrtRasterSource",
    "GdalRasterSource",
    "H5DatasetRasterSource",
    "ImageSourceMetadata",
    "LayerManager",
    "LayerPanelController",
    "LayeredRasterCanvas",
    "LayerSpec",
    "LayerState",
    "OverviewInfo",
    "RasterImageSource",
    "RasterRenderConfig",
    "RasterCanvasSynchronizer",
    "RenderRequest",
    "RenderTileResult",
    "StandardImageSource",
    "ViewportState",
    "default_raster_render_config",
    "NON_REMOVABLE_LAYER_IDS",
    "is_layer_removable",
    "ui_index_to_z_index",
    "z_index_to_ui_index",
    "nodata_to_text",
    "render_raster_rgb",
]
