"""
分割工具的底图渲染管线。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class SegmentationRenderConfig:
    display_mode: str = "rgb"
    gray_band: int = 1
    rgb_bands: tuple[int, int, int] = (1, 2, 3)
    gamma: float = 1.0
    segmentation_source: str = "display_rgb"


def default_render_config() -> SegmentationRenderConfig:
    return SegmentationRenderConfig()


def render_base_rgb(raw_array: np.ndarray, config: SegmentationRenderConfig) -> np.ndarray:
    if raw_array.ndim == 2:
        return _gray_to_rgb(raw_array, gamma=config.gamma)

    if raw_array.ndim == 3:
        if raw_array.shape[2] == 1:
            return _gray_to_rgb(raw_array[:, :, 0], gamma=config.gamma)

        channels = raw_array.shape[2]
        if config.display_mode == "gray":
            band = min(max(config.gray_band, 1), channels) - 1
            return _gray_to_rgb(raw_array[:, :, band], gamma=config.gamma)

        indices = [min(max(index, 1), channels) - 1 for index in config.rgb_bands]
        rgb = raw_array[:, :, indices]
        if rgb.dtype != np.uint8:
            rgb = np.clip(rgb, 0, 255).astype(np.uint8)
        if config.gamma != 1.0:
            rgb = _apply_gamma(rgb, config.gamma)
        return rgb

    raise ValueError(f"不支持的数组形状: {raw_array.shape}")


def _gray_to_rgb(gray: np.ndarray, gamma: float = 1.0) -> np.ndarray:
    if gray.dtype == np.uint8:
        normalized = gray.astype(np.float32) / 255.0
    else:
        arr = gray.astype(np.float32)
        min_val = float(np.nanmin(arr))
        max_val = float(np.nanmax(arr))
        if max_val - min_val < 1e-6:
            normalized = np.zeros_like(arr, dtype=np.float32)
        else:
            normalized = (arr - min_val) / (max_val - min_val)
    if gamma != 1.0:
        normalized = np.power(np.clip(normalized, 0.0, 1.0), 1.0 / gamma)
    uint8 = np.clip(normalized * 255.0, 0, 255).astype(np.uint8)
    return np.stack([uint8, uint8, uint8], axis=-1)


def _apply_gamma(rgb: np.ndarray, gamma: float) -> np.ndarray:
    normalized = np.clip(rgb.astype(np.float32) / 255.0, 0.0, 1.0)
    corrected = np.power(normalized, 1.0 / gamma)
    return np.clip(corrected * 255.0, 0, 255).astype(np.uint8)
