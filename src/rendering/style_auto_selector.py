"""
默认渲染样式选择器。
"""

from __future__ import annotations

from .styles import (
    ColorRampSettings,
    MultibandRenderStyle,
    PalettedRenderStyle,
    SinglebandPseudoColorRenderStyle,
    StretchSettings,
    UniqueValueRenderStyle,
    default_display_settings,
)


class DefaultRenderStyleFactory:
    @classmethod
    def create(cls, metadata):
        """create。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            metadata (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        source_kind = str((getattr(metadata, "custom_properties", {}) or {}).get("source_kind", "")).lower()
        if getattr(metadata, "has_color_table", False) and getattr(metadata, "color_table", None):
            return PalettedRenderStyle(
                palette=tuple(tuple(int(v) for v in entry) for entry in (metadata.color_table or [])),
            )
        color_interps = [str(item).lower() for item in (getattr(metadata, "color_interpretations", None) or [])]
        if {"red", "green", "blue"}.issubset(set(color_interps)):
            rgb = []
            for target in ("red", "green", "blue"):
                rgb.append(color_interps.index(target) + 1)
            return MultibandRenderStyle(band_indices=tuple(rgb), stretch=StretchSettings(stretch_type="最大最小", auto_range=True))
        if int(getattr(metadata, "band_count", 1) or 1) >= 3:
            return MultibandRenderStyle(band_indices=(1, 2, 3), stretch=StretchSettings(stretch_type="最大最小", auto_range=True))
        if source_kind in {"h5_dataset", "h5_timeseries"}:
            return SinglebandPseudoColorRenderStyle(
                band_indices=(1,),
                stretch=StretchSettings(stretch_type="最大最小", auto_range=True),
                color_ramp=ColorRampSettings(name="jet"),
            )
        if source_kind.startswith("gamma"):
            return SinglebandPseudoColorRenderStyle(
                band_indices=(1,),
                stretch=StretchSettings(stretch_type="最大最小", auto_range=True),
                color_ramp=ColorRampSettings(name="gray"),
            )
        unique_hint = (getattr(metadata, "custom_properties", {}) or {}).get("unique_value_candidate_count")
        if unique_hint is not None and int(unique_hint) <= 32:
            return UniqueValueRenderStyle(band_indices=(1,))
        return SinglebandPseudoColorRenderStyle(
            band_indices=(1,),
            stretch=StretchSettings(stretch_type="最大最小", auto_range=True),
            color_ramp=ColorRampSettings(name="gray"),
        )

    @classmethod
    def create_display_settings(cls, metadata):
        """create_display_settings。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            metadata (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        source_kind = str((getattr(metadata, "custom_properties", {}) or {}).get("source_kind", "")).lower()
        is_categorical = bool((getattr(metadata, "custom_properties", {}) or {}).get("categorical", False))
        resampling = "nearest" if getattr(metadata, "has_color_table", False) or is_categorical or source_kind.startswith("mask") else "bilinear"
        return default_display_settings(nodata_value=getattr(metadata, "nodata", None)).__class__(
            visible=True,
            opacity=1.0,
            blend_mode="source_over",
            nodata_policy=default_display_settings(nodata_value=getattr(metadata, "nodata", None)).nodata_policy,
            background_color=None,
            alpha_band=None,
            mask_enabled=False,
            resampling=default_display_settings().resampling.__class__(zoomed_in="nearest", zoomed_out=resampling),
        )


RasterStyleAutoSelector = DefaultRenderStyleFactory
