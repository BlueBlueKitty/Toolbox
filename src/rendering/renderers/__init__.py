"""
按样式类型拆分的栅格渲染器。
"""

from __future__ import annotations

import math
import numpy as np

try:
    import matplotlib.cm as cm
    from matplotlib import colormaps as mpl_colormaps
except Exception:  # pragma: no cover
    cm = None
    mpl_colormaps = None

from src.utils.image_io import calculate_hillshade
from ..styles import (
    HillshadeRenderStyle,
    MultibandRenderStyle,
    PalettedRenderStyle,
    SinglebandGrayRenderStyle,
    SinglebandPseudoColorRenderStyle,
    StretchSettings,
    UniqueValueRenderStyle,
)


class BaseRenderer:
    renderer_type = "base"

    def render(self, raw_block, style, display_settings):
        raise NotImplementedError


def _valid_mask(arr: np.ndarray, nodata_value=None):
    mask = np.isfinite(arr)
    if nodata_value is not None:
        try:
            if np.isnan(nodata_value):
                mask &= ~np.isnan(arr)
            else:
                mask &= arr != nodata_value
        except Exception:
            mask &= arr != nodata_value
    return mask


def _resolve_range(arr: np.ndarray, valid_mask: np.ndarray, stretch: StretchSettings):
    if not np.any(valid_mask):
        return 0.0, 1.0
    valid = arr[valid_mask]
    if not stretch.auto_range and stretch.min_value is not None and stretch.max_value is not None:
        return float(stretch.min_value), float(stretch.max_value)
    if stretch.stretch_type == "百分比截断":
        low, high = stretch.percent_clip
        return float(np.percentile(valid, low)), float(np.percentile(valid, high))
    if stretch.stretch_type == "标准差":
        mean = float(np.mean(valid))
        std = float(np.std(valid))
        return mean - float(stretch.std_dev_n) * std, mean + float(stretch.std_dev_n) * std
    return float(np.min(valid)), float(np.max(valid))


def _apply_brightness_contrast(rgb: np.ndarray, brightness: float = 0.0, contrast: float = 1.0):
    arr = rgb.astype(np.float32) / 255.0
    arr = (arr - 0.5) * max(float(contrast), 0.0) + 0.5 + float(brightness)
    return np.clip(arr * 255.0, 0, 255).astype(np.uint8)


def _apply_saturation(rgb: np.ndarray, saturation: float = 1.0):
    if abs(float(saturation) - 1.0) < 1e-6:
        return rgb
    arr = rgb.astype(np.float32)
    gray = np.sum(arr * np.array([0.299, 0.587, 0.114], dtype=np.float32), axis=2, keepdims=True)
    arr = gray + (arr - gray) * float(saturation)
    return np.clip(arr, 0, 255).astype(np.uint8)


def _normalize_channel(arr: np.ndarray, stretch: StretchSettings, *, gamma: float = 1.0, invert: bool = False, nodata_value=None):
    valid = _valid_mask(arr, nodata_value=nodata_value)
    result = np.zeros(arr.shape, dtype=np.float32)
    if not np.any(valid):
        return result
    vmin, vmax = _resolve_range(arr, valid, stretch)
    if vmax > vmin:
        normalized = np.clip((arr.astype(np.float32) - vmin) / (vmax - vmin), 0.0, 1.0)
    else:
        normalized = np.zeros(arr.shape, dtype=np.float32)
    if gamma not in (0, 1.0):
        normalized = np.power(normalized, 1.0 / float(gamma))
    if invert:
        normalized = 1.0 - normalized
    result[valid] = normalized[valid]
    return result


def _apply_colormap_to_normalized(normalized_arr: np.ndarray, colormap_name: str, reversed_: bool = False, discrete: bool = False) -> np.ndarray:
    normalized = np.clip(normalized_arr.astype(np.float32), 0.0, 1.0)
    if discrete:
        normalized = np.round(normalized * 255.0) / 255.0
    name = f"{colormap_name}_r" if reversed_ and not colormap_name.endswith("_r") else colormap_name
    if colormap_name == "gray" and not reversed_:
        gray_uint8 = np.clip(normalized * 255.0, 0, 255).astype(np.uint8)
        return np.stack([gray_uint8, gray_uint8, gray_uint8], axis=-1)
    if cm is None:
        gray_uint8 = np.clip((1.0 - normalized if reversed_ else normalized) * 255.0, 0, 255).astype(np.uint8)
        return np.stack([gray_uint8, gray_uint8, gray_uint8], axis=-1)
    cmap = mpl_colormaps.get_cmap(name) if mpl_colormaps is not None else cm.get_cmap(name)
    rgba = cmap(normalized)
    return np.clip(rgba[:, :, :3] * 255.0, 0, 255).astype(np.uint8)


class MultibandRenderer(BaseRenderer):
    renderer_type = "multiband"

    def render(self, raw_block, style: MultibandRenderStyle, display_settings):
        arr = np.asarray(raw_block.data)
        if arr.ndim == 2:
            arr = np.repeat(arr[:, :, np.newaxis], 3, axis=2)
        if arr.shape[2] < 3:
            arr = np.repeat(arr[:, :, :1], 3, axis=2)
        channels = []
        nodata_value = raw_block.nodata_value
        channel_gamma = tuple(getattr(style, "channel_gamma", (style.gamma, style.gamma, style.gamma)))
        for band_index in style.band_indices[:3]:
            idx = min(max(int(band_index), 1), arr.shape[2]) - 1
            gamma = float(channel_gamma[len(channels)] if len(channel_gamma) > len(channels) else style.gamma)
            channel = _normalize_channel(arr[:, :, idx], style.stretch, gamma=gamma, nodata_value=nodata_value)
            channels.append(np.clip(channel * 255.0, 0, 255).astype(np.uint8))
        rgb = np.stack(channels, axis=-1)
        rgb = _apply_saturation(rgb, style.saturation)
        return _apply_brightness_contrast(rgb, style.brightness, style.contrast)


class SinglebandGrayRenderer(BaseRenderer):
    renderer_type = "singleband_gray"

    def render(self, raw_block, style: SinglebandGrayRenderStyle, display_settings):
        arr = np.asarray(raw_block.data)
        if arr.ndim == 3:
            arr = arr[:, :, min(max(int(style.band_indices[0]), 1), arr.shape[2]) - 1]
        normalized = _normalize_channel(arr, style.stretch, gamma=style.gamma, invert=style.invert, nodata_value=raw_block.nodata_value)
        gray = np.clip(normalized * 255.0, 0, 255).astype(np.uint8)
        rgb = np.stack([gray, gray, gray], axis=-1)
        return _apply_brightness_contrast(rgb, style.brightness, style.contrast)


class SinglebandPseudoColorRenderer(BaseRenderer):
    renderer_type = "singleband_pseudocolor"

    def render(self, raw_block, style: SinglebandPseudoColorRenderStyle, display_settings):
        arr = np.asarray(raw_block.data)
        if arr.ndim == 3:
            arr = arr[:, :, min(max(int(style.band_indices[0]), 1), arr.shape[2]) - 1]
        normalized = _normalize_channel(arr, style.stretch, gamma=style.gamma, nodata_value=raw_block.nodata_value)
        return _apply_colormap_to_normalized(
            normalized,
            style.color_ramp.name,
            reversed_=style.color_ramp.reversed,
            discrete=style.color_ramp.discrete,
        )


class UniqueValueRenderer(BaseRenderer):
    renderer_type = "unique_value"

    def render(self, raw_block, style: UniqueValueRenderStyle, display_settings):
        arr = np.asarray(raw_block.data)
        if arr.ndim == 3:
            arr = arr[:, :, min(max(int(style.band_indices[0]), 1), arr.shape[2]) - 1]
        rgba = np.zeros(arr.shape + (4,), dtype=np.uint8)
        rgba[:] = np.asarray(style.undefined_color, dtype=np.uint8)
        for item in style.items:
            if not item.visible:
                continue
            rgba[arr == item.value] = np.asarray(item.color, dtype=np.uint8)
        return rgba


class PalettedRenderer(BaseRenderer):
    renderer_type = "paletted"

    def render(self, raw_block, style: PalettedRenderStyle, display_settings):
        arr = np.asarray(raw_block.data)
        if arr.ndim == 3:
            arr = arr[:, :, 0]
        rgba = np.zeros(arr.shape + (4,), dtype=np.uint8)
        rgba[:] = np.asarray(style.default_color, dtype=np.uint8)
        if not style.palette:
            return rgba
        table = np.asarray(style.palette, dtype=np.uint8)
        valid = np.isfinite(arr)
        arr_int = np.zeros(arr.shape, dtype=np.int64)
        if np.any(valid):
            arr_int[valid] = arr[valid].astype(np.int64)
        in_range = valid & (arr_int >= 0) & (arr_int < len(table))
        if np.any(in_range):
            rgba[in_range] = table[arr_int[in_range]]
        if style.palette_visibility:
            visibility = np.asarray(style.palette_visibility, dtype=bool)
            visible_mask = np.zeros(arr.shape, dtype=bool)
            visible_indices = in_range & (arr_int < len(visibility))
            if np.any(visible_indices):
                visible_mask[visible_indices] = visibility[arr_int[visible_indices]]
            rgba[in_range & ~visible_mask] = np.asarray(style.default_color, dtype=np.uint8)
        return rgba


class HillshadeRenderer(BaseRenderer):
    renderer_type = "hillshade"

    def render(self, raw_block, style: HillshadeRenderStyle, display_settings):
        arr = np.asarray(raw_block.data)
        if arr.ndim == 3:
            arr = arr[:, :, min(max(int(style.band_indices[0]), 1), arr.shape[2]) - 1]
        normalized = _normalize_channel(arr, style.stretch, gamma=style.gamma, nodata_value=raw_block.nodata_value)
        gt = getattr(raw_block.metadata, "geotransform", None) if raw_block.metadata is not None else None
        proj = getattr(raw_block.metadata, "crs_wkt", None) if raw_block.metadata is not None else None
        hillshade = calculate_hillshade(
            arr.astype(np.float32),
            azimuth=float(style.azimuth),
            altitude=float(style.altitude),
            z_factor=float(style.z_factor),
            nodata_value=raw_block.nodata_value,
            geotransform=gt,
            projection=proj,
        )
        shaded = np.clip(normalized * hillshade.astype(np.float32), 0.0, 1.0)
        return _apply_colormap_to_normalized(
            shaded,
            style.color_ramp.name,
            reversed_=style.color_ramp.reversed,
            discrete=style.color_ramp.discrete,
        )


RENDERER_REGISTRY = {
    "multiband": MultibandRenderer(),
    "singleband_gray": SinglebandGrayRenderer(),
    "singleband_pseudocolor": SinglebandPseudoColorRenderer(),
    "unique_value": UniqueValueRenderer(),
    "paletted": PalettedRenderer(),
    "hillshade": HillshadeRenderer(),
}


def renderer_for_style(style):
    return RENDERER_REGISTRY.get(getattr(style, "renderer_type", "singleband_gray"), SinglebandGrayRenderer())
