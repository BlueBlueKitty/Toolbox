"""
通用栅格渲染与图层底层。
"""

from .config import RasterRenderConfig, default_raster_render_config, render_raster_rgb
from .canvas import LayeredRasterCanvas, RasterCanvasSynchronizer
from .layers import LayerManager
from .layer_panel_controller import LayerPanelController
from .pipeline import DEFAULT_RENDER_PIPELINE, RasterRenderPipeline
from .style_auto_selector import DefaultRenderStyleFactory, RasterStyleAutoSelector
from .statistics import RasterStatisticsService
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
    RasterLayer,
    RawRasterBlock,
    RenderRequest,
    RenderTileResult,
    ViewportState,
)
from .styles import (
    BaseRenderStyle,
    ColorRampSettings,
    HillshadeRenderStyle,
    LayerDisplaySettings,
    MultibandRenderStyle,
    NodataPolicy,
    PalettedRenderStyle,
    ResamplingPolicy,
    SinglebandGrayRenderStyle,
    SinglebandPseudoColorRenderStyle,
    StretchSettings,
    UniqueValueItem,
    UniqueValueRenderStyle,
    default_display_settings,
    legacy_config_to_style,
    style_to_legacy_config,
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
    "RasterLayer",
    "RawRasterBlock",
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
    "RasterRenderPipeline",
    "DEFAULT_RENDER_PIPELINE",
    "StandardImageSource",
    "ViewportState",
    "RasterStatisticsService",
    "DefaultRenderStyleFactory",
    "RasterStyleAutoSelector",
    "BaseRenderStyle",
    "MultibandRenderStyle",
    "SinglebandGrayRenderStyle",
    "SinglebandPseudoColorRenderStyle",
    "UniqueValueRenderStyle",
    "PalettedRenderStyle",
    "HillshadeRenderStyle",
    "LayerDisplaySettings",
    "StretchSettings",
    "ColorRampSettings",
    "UniqueValueItem",
    "NodataPolicy",
    "ResamplingPolicy",
    "default_display_settings",
    "legacy_config_to_style",
    "style_to_legacy_config",
    "default_raster_render_config",
    "NON_REMOVABLE_LAYER_IDS",
    "is_layer_removable",
    "ui_index_to_z_index",
    "z_index_to_ui_index",
    "nodata_to_text",
    "render_raster_rgb",
]
