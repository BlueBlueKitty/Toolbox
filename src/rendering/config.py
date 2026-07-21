"""
通用栅格渲染配置与兼容渲染函数。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .models import ImageSourceMetadata, RawRasterBlock
from .renderers import _apply_colormap_to_normalized
from .styles import default_display_settings, legacy_config_to_style


@dataclass
class RasterRenderConfig:
    display_mode: str = "灰度"
    gray_band: int = 1
    rgb_bands: tuple[int, int, int] = (1, 2, 3)
    gamma: float = 1.0
    stretch_mode: str = "最大最小"
    percent_clip: tuple[float, float] = (2.0, 98.0)
    std_dev_n: float = 2.0
    auto_range: bool = True
    value_range: tuple[float, float] = (0.0, 1.0)
    global_value_range: tuple[float, float] | None = None
    colormap_name: str = "gray"
    colormap_reversed: bool = False
    smooth_display: bool = False
    segmentation_source: str = "display_rgb"

    def to_settings(self) -> dict:
        value_range = self.global_value_range if self.auto_range and self.global_value_range else self.value_range
        return {
            "stretch_mode": self.stretch_mode,
            "percent_clip": self.percent_clip,
            "std_dev_n": self.std_dev_n,
            "gamma": self.gamma,
            "auto_range": False if self.auto_range and self.global_value_range else self.auto_range,
            "value_range": value_range,
            "value_min": value_range[0],
            "value_max": value_range[1],
            "colormap_reversed": self.colormap_reversed,
            "display_mode": self.display_mode,
            "gray_band": self.gray_band,
            "rgb_bands": self.rgb_bands,
            "hillshade_params": {"azimuth": 315.0, "altitude": 45.0, "z_factor": 1.0},
            "smooth_display": self.smooth_display,
        }


def default_raster_render_config(band_count: int = 1, has_color_table: bool = False) -> RasterRenderConfig:
    config = RasterRenderConfig()
    if has_color_table:
        config.display_mode = "灰度"
        config.colormap_name = "gray"
        return config
    if int(band_count or 1) >= 3:
        config.display_mode = "RGB"
        config.rgb_bands = (1, 2, 3)
        config.stretch_mode = "最大最小"
        # RGB samples are not universally normalized to [0, 1] (PNG/JPEG are
        # normally uint8).  Keeping the dataclass fallback range while turning
        # auto range off clips every channel identically and destroys colour.
        config.auto_range = True
    else:
        config.display_mode = "灰度"
        config.gray_band = 1
    return config


def render_raster_rgb(
    raw_array: np.ndarray,
    config: RasterRenderConfig,
    nodata_value=None,
    color_table: list[tuple[int, int, int, int]] | None = None,
    geotransform=None,
    projection=None,
    downsample_factor=1,
) -> np.ndarray:
    metadata = ImageSourceMetadata(
        id="legacy",
        path="",
        path_mode="memory",
        width=int(raw_array.shape[1]),
        height=int(raw_array.shape[0]),
        band_count=1 if raw_array.ndim == 2 else int(raw_array.shape[2]),
        dtype=str(raw_array.dtype),
        nodata=nodata_value,
        crs_wkt=projection,
        geotransform=geotransform,
        resolution=None,
        has_georef=bool(geotransform or projection),
        has_color_table=bool(color_table),
        color_table=color_table,
    )
    style = legacy_config_to_style(config, metadata)
    display_settings = default_display_settings(nodata_value=nodata_value)
    from .pipeline import DEFAULT_RENDER_PIPELINE
    from .renderers import renderer_for_style

    raw_block = RawRasterBlock(
        data=raw_array,
        image_rect=(0.0, 0.0, float(raw_array.shape[1]), float(raw_array.shape[0])),
        source_window=(0, 0, int(raw_array.shape[1]), int(raw_array.shape[0])),
        overview_level=None,
        nodata_value=nodata_value,
        metadata=metadata,
        custom_properties={"downsample_factor": downsample_factor},
    )
    renderer = renderer_for_style(style)
    rendered = renderer.render(raw_block, style, display_settings)
    result = DEFAULT_RENDER_PIPELINE._apply_display_settings(rendered, raw_block, display_settings)  # noqa: SLF001
    if result.ndim == 3 and result.shape[2] == 4 and np.all(result[:, :, 3] == 255):
        return result[:, :, :3]
    return result


__all__ = [
    "RasterRenderConfig",
    "default_raster_render_config",
    "render_raster_rgb",
    "_apply_colormap_to_normalized",
]
