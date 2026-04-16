"""
分割工具的底图渲染管线。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:
    import matplotlib.cm as cm
    from matplotlib import colormaps as mpl_colormaps
except Exception:  # pragma: no cover
    cm = None
    mpl_colormaps = None

from src.widgets.render_settings_widget import RenderSettingsWidget, apply_render_settings


@dataclass
class SegmentationRenderConfig:
    display_mode: str = "灰度"
    gray_band: int = 1
    rgb_bands: tuple[int, int, int] = (1, 2, 3)
    gamma: float = 1.0
    stretch_mode: str = RenderSettingsWidget.STRETCH_MIN_MAX
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


def default_render_config() -> SegmentationRenderConfig:
    return SegmentationRenderConfig()


def render_base_rgb(
    raw_array: np.ndarray,
    config: SegmentationRenderConfig,
    nodata_value=None,
) -> np.ndarray:
    if (
        config.display_mode == "RGB"
        and raw_array.ndim == 3
        and raw_array.dtype == np.uint8
    ):
        channels = raw_array.shape[2]
        indices = [min(max(index, 1), channels) - 1 for index in config.rgb_bands]
        rgb = raw_array[:, :, indices]
        if config.gamma != 1.0:
            normalized = np.clip(rgb.astype(np.float32) / 255.0, 0.0, 1.0)
            rgb = np.clip(np.power(normalized, 1.0 / config.gamma) * 255.0, 0, 255).astype(np.uint8)
        return rgb
    processed = apply_render_settings(raw_array, config.to_settings(), nodata_value=nodata_value)
    if processed is None:
        raise ValueError("渲染失败：输入数组为空")
    if processed.ndim == 2:
        return _apply_colormap_to_normalized(processed, config.colormap_name)
    return np.clip(processed * 255.0, 0, 255).astype(np.uint8)


def _apply_colormap_to_normalized(normalized_arr: np.ndarray, colormap_name: str) -> np.ndarray:
    normalized = np.clip(normalized_arr.astype(np.float32), 0.0, 1.0)
    if colormap_name == "gray" or cm is None:
        gray_uint8 = np.clip(normalized * 255.0, 0, 255).astype(np.uint8)
        return np.stack([gray_uint8, gray_uint8, gray_uint8], axis=-1)
    cmap = mpl_colormaps.get_cmap(colormap_name) if mpl_colormaps is not None else cm.get_cmap(colormap_name)
    rgba = cmap(normalized)
    return np.clip(rgba[:, :, :3] * 255.0, 0, 255).astype(np.uint8)
