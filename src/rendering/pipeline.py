"""
统一栅格渲染管线。
"""

from __future__ import annotations

from dataclasses import replace
import os
import numpy as np

from .models import RenderTileResult
from .renderers import renderer_for_style
from .styles import LayerDisplaySettings, stable_style_hash


class RasterRenderPipeline:
    def __init__(self):
        """__init__。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self._render_cache: dict[tuple, RenderTileResult] = {}

    def build_cache_key(self, source, request, style, display_settings: LayerDisplaySettings, layer_id: str | None = None, layer_revision: int = 0):
        """build_cache_key。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            source (Any): 输入参数。
            request (Any): 输入参数。
            style (Any): 输入参数。
            display_settings (LayerDisplaySettings): 输入参数。
            layer_id (str | None): 输入参数。
            layer_revision (int): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        metadata = source.metadata()
        try:
            mtime = os.path.getmtime(metadata.path)
        except OSError:
            mtime = None
        source_id = getattr(metadata, "id", metadata.path)
        display_hash = stable_style_hash(display_settings)
        style_hash = stable_style_hash(style)
        return (
            source_id,
            metadata.path,
            mtime,
            layer_id,
            int(layer_revision),
            getattr(style, "renderer_type", "singleband_gray"),
            style_hash,
            display_hash,
            getattr(request, "x", 0.0),
            getattr(request, "y", 0.0),
            getattr(request, "width", 0.0),
            getattr(request, "height", 0.0),
            getattr(request, "screen_width", 0),
            getattr(request, "screen_height", 0),
            getattr(request, "device_pixel_ratio", 1.0),
        )

    def render_source(self, source, request, style, display_settings: LayerDisplaySettings, *, layer_id: str | None = None, layer_revision: int = 0):
        """render_source。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            source (Any): 输入参数。
            request (Any): 输入参数。
            style (Any): 输入参数。
            display_settings (LayerDisplaySettings): 输入参数。
            layer_id (str | None): 输入参数。
            layer_revision (int): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        style = self._resolve_stable_style_ranges(source, style)
        cache_key = self.build_cache_key(source, request, style, display_settings, layer_id=layer_id, layer_revision=layer_revision)
        cached = self._render_cache.get(cache_key)
        if cached is not None:
            return cached
        raw_block = source.read_block(request, style=style)
        renderer = renderer_for_style(style)
        rgba_or_rgb = renderer.render(raw_block, style, display_settings)
        display = self._apply_display_settings(rgba_or_rgb, raw_block, display_settings)
        result = RenderTileResult(
            raw_array=raw_block.data,
            display_rgb=display,
            image_rect=raw_block.image_rect,
            overview_level=raw_block.overview_level,
            source_window=raw_block.source_window,
            layer_id=layer_id,
            cache_key=cache_key,
        )
        self._render_cache[cache_key] = result
        return result

    def invalidate_layer(self, layer_id: str | None = None):
        """invalidate_layer。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str | None): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if layer_id is None:
            self._render_cache.clear()
            return
        remove_keys = [key for key in self._render_cache if len(key) > 3 and key[3] == layer_id]
        for key in remove_keys:
            self._render_cache.pop(key, None)

    def _resolve_stable_style_ranges(self, source, style):
        """_resolve_stable_style_ranges。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            source (Any): 输入参数。
            style (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        复杂度:
            时间和空间复杂度与输入规模线性或近线性相关。
        """
        stretch = getattr(style, "stretch", None)
        band_indices = tuple(getattr(style, "band_indices", ()) or ())
        if stretch is None or not getattr(stretch, "auto_range", False):
            return style
        if not band_indices or not hasattr(source, "band_value_range"):
            return style
        ranges = []
        settings = {
            "stretch_mode": getattr(stretch, "stretch_type", "最大最小"),
            "percent_clip": tuple(getattr(stretch, "percent_clip", (2.0, 98.0))),
            "std_dev_n": float(getattr(stretch, "std_dev_n", 2.0)),
        }
        for band_index in band_indices[:3]:
            try:
                value_range = source.band_value_range(int(band_index), settings)
            except Exception:
                value_range = None
            if value_range is not None:
                ranges.append((float(value_range[0]), float(value_range[1])))
        if not ranges:
            return style
        min_value = min(item[0] for item in ranges)
        max_value = max(item[1] for item in ranges)
        resolved_stretch = replace(
            stretch,
            stretch_type=str(getattr(stretch, "stretch_type", "最大最小")).replace("无拉伸", "最大最小"),
            auto_range=False,
            min_value=min_value,
            max_value=max_value,
        )
        return replace(style, stretch=resolved_stretch)

    def _apply_display_settings(self, arr: np.ndarray, raw_block, display_settings: LayerDisplaySettings):
        """_apply_display_settings。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            arr (np.ndarray): 输入参数。
            raw_block (Any): 输入参数。
            display_settings (LayerDisplaySettings): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        display = np.asarray(arr)
        if display.ndim == 3 and display.shape[2] == 4:
            rgba = display.astype(np.uint8, copy=True)
        else:
            rgba = np.dstack([display.astype(np.uint8), np.full(display.shape[:2], 255, dtype=np.uint8)])
        if raw_block.alpha is not None:
            rgba[:, :, 3] = np.minimum(rgba[:, :, 3], np.asarray(raw_block.alpha, dtype=np.uint8))
        nodata_policy = display_settings.nodata_policy
        nodata_value = raw_block.nodata_value if nodata_policy.use_source_nodata else nodata_policy.value
        if nodata_policy.enabled and nodata_value is not None:
            data = np.asarray(raw_block.data)
            if data.ndim == 3:
                try:
                    if np.isnan(nodata_value):
                        mask = np.all(np.isnan(data), axis=2)
                    else:
                        mask = np.all(data == nodata_value, axis=2)
                except Exception:
                    mask = np.all(data == nodata_value, axis=2)
            else:
                try:
                    mask = np.isnan(data) if np.isnan(nodata_value) else (data == nodata_value)
                except Exception:
                    mask = data == nodata_value
            rgba[mask, 3] = 0
        opacity = max(0.0, min(float(display_settings.opacity), 1.0))
        if opacity < 1.0:
            rgba[:, :, 3] = np.clip(rgba[:, :, 3].astype(np.float32) * opacity, 0, 255).astype(np.uint8)
        return rgba if np.any(rgba[:, :, 3] < 255) else rgba[:, :, :3]


DEFAULT_RENDER_PIPELINE = RasterRenderPipeline()
