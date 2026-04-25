"""
渲染样式与图层显示设置。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any
import hashlib
import json


@dataclass(frozen=True)
class StretchSettings:
    stretch_type: str = "最大最小"
    percent_clip: tuple[float, float] = (2.0, 98.0)
    std_dev_n: float = 2.0
    auto_range: bool = True
    min_value: float | None = None
    max_value: float | None = None


@dataclass(frozen=True)
class ColorRampSettings:
    name: str = "gray"
    reversed: bool = False
    discrete: bool = False
    class_breaks: tuple[float, ...] = ()


@dataclass(frozen=True)
class UniqueValueItem:
    value: int | float | str
    color: tuple[int, int, int, int]
    label: str = ""
    alpha: float = 1.0
    visible: bool = True


@dataclass(frozen=True)
class NodataPolicy:
    enabled: bool = True
    value: Any = None
    use_source_nodata: bool = True


@dataclass(frozen=True)
class ResamplingPolicy:
    zoomed_in: str = "nearest"
    zoomed_out: str = "nearest"


@dataclass(frozen=True)
class LayerDisplaySettings:
    visible: bool = True
    opacity: float = 1.0
    blend_mode: str = "source_over"
    nodata_policy: NodataPolicy = field(default_factory=NodataPolicy)
    alpha_band: int | None = None
    mask_enabled: bool = False
    resampling: ResamplingPolicy = field(default_factory=ResamplingPolicy)


@dataclass(frozen=True)
class BaseRenderStyle:
    renderer_type: str
    band_indices: tuple[int, ...] = ()
    gamma: float = 1.0
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    invert: bool = False


@dataclass(frozen=True)
class MultibandRenderStyle(BaseRenderStyle):
    renderer_type: str = "multiband"
    band_indices: tuple[int, ...] = (1, 2, 3)
    channel_gamma: tuple[float, float, float] = (1.0, 1.0, 1.0)
    stretch: StretchSettings = field(default_factory=lambda: StretchSettings(stretch_type="最大最小", auto_range=False))


@dataclass(frozen=True)
class SinglebandGrayRenderStyle(BaseRenderStyle):
    renderer_type: str = "singleband_gray"
    band_indices: tuple[int, ...] = (1,)
    stretch: StretchSettings = field(default_factory=StretchSettings)


@dataclass(frozen=True)
class SinglebandPseudoColorRenderStyle(BaseRenderStyle):
    renderer_type: str = "singleband_pseudocolor"
    band_indices: tuple[int, ...] = (1,)
    stretch: StretchSettings = field(default_factory=StretchSettings)
    color_ramp: ColorRampSettings = field(default_factory=ColorRampSettings)


@dataclass(frozen=True)
class UniqueValueRenderStyle(BaseRenderStyle):
    renderer_type: str = "unique_value"
    band_indices: tuple[int, ...] = (1,)
    items: tuple[UniqueValueItem, ...] = ()
    undefined_color: tuple[int, int, int, int] = (0, 0, 0, 0)


@dataclass(frozen=True)
class PalettedRenderStyle(BaseRenderStyle):
    renderer_type: str = "paletted"
    band_indices: tuple[int, ...] = (1,)
    palette: tuple[tuple[int, int, int, int], ...] = ()
    default_color: tuple[int, int, int, int] = (0, 0, 0, 0)


@dataclass(frozen=True)
class HillshadeRenderStyle(BaseRenderStyle):
    renderer_type: str = "hillshade"
    band_indices: tuple[int, ...] = (1,)
    stretch: StretchSettings = field(default_factory=StretchSettings)
    color_ramp: ColorRampSettings = field(default_factory=lambda: ColorRampSettings(name="terrain"))
    azimuth: float = 315.0
    altitude: float = 45.0
    z_factor: float = 1.0
    relief_blend_mode: str = "multiply"


def stable_style_hash(value: Any) -> str:
    payload = json.dumps(asdict(value), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def style_to_dict(style: Any) -> dict[str, Any]:
    payload = asdict(style)
    payload["renderer_type"] = getattr(style, "renderer_type", payload.get("renderer_type"))
    return payload


def display_settings_to_dict(display_settings: LayerDisplaySettings) -> dict[str, Any]:
    return asdict(display_settings)


def style_from_dict(payload: dict[str, Any]) -> BaseRenderStyle:
    renderer_type = payload.get("renderer_type", "singleband_gray")
    if renderer_type == "multiband":
        return MultibandRenderStyle(
            renderer_type=renderer_type,
            band_indices=tuple(payload.get("band_indices", (1, 2, 3))),
            channel_gamma=tuple(payload.get("channel_gamma", (1.0, 1.0, 1.0))),
            gamma=float(payload.get("gamma", 1.0)),
            brightness=float(payload.get("brightness", 0.0)),
            contrast=float(payload.get("contrast", 1.0)),
            saturation=float(payload.get("saturation", 1.0)),
            invert=bool(payload.get("invert", False)),
            stretch=StretchSettings(**payload.get("stretch", {})),
        )
    if renderer_type == "singleband_pseudocolor":
        color_ramp = ColorRampSettings(**payload.get("color_ramp", {}))
        stretch = StretchSettings(**payload.get("stretch", {}))
        return SinglebandPseudoColorRenderStyle(
            renderer_type=renderer_type,
            band_indices=tuple(payload.get("band_indices", (1,))),
            gamma=float(payload.get("gamma", 1.0)),
            brightness=float(payload.get("brightness", 0.0)),
            contrast=float(payload.get("contrast", 1.0)),
            saturation=float(payload.get("saturation", 1.0)),
            invert=bool(payload.get("invert", False)),
            stretch=stretch,
            color_ramp=color_ramp,
        )
    if renderer_type == "unique_value":
        items = tuple(UniqueValueItem(**item) for item in payload.get("items", []))
        return UniqueValueRenderStyle(
            renderer_type=renderer_type,
            band_indices=tuple(payload.get("band_indices", (1,))),
            gamma=float(payload.get("gamma", 1.0)),
            brightness=float(payload.get("brightness", 0.0)),
            contrast=float(payload.get("contrast", 1.0)),
            saturation=float(payload.get("saturation", 1.0)),
            invert=bool(payload.get("invert", False)),
            items=items,
            undefined_color=tuple(payload.get("undefined_color", (0, 0, 0, 0))),
        )
    if renderer_type == "paletted":
        return PalettedRenderStyle(
            renderer_type=renderer_type,
            band_indices=tuple(payload.get("band_indices", (1,))),
            gamma=float(payload.get("gamma", 1.0)),
            brightness=float(payload.get("brightness", 0.0)),
            contrast=float(payload.get("contrast", 1.0)),
            saturation=float(payload.get("saturation", 1.0)),
            invert=bool(payload.get("invert", False)),
            palette=tuple(tuple(item) for item in payload.get("palette", [])),
            default_color=tuple(payload.get("default_color", (0, 0, 0, 0))),
        )
    if renderer_type == "hillshade":
        return HillshadeRenderStyle(
            renderer_type=renderer_type,
            band_indices=tuple(payload.get("band_indices", (1,))),
            gamma=float(payload.get("gamma", 1.0)),
            brightness=float(payload.get("brightness", 0.0)),
            contrast=float(payload.get("contrast", 1.0)),
            saturation=float(payload.get("saturation", 1.0)),
            invert=bool(payload.get("invert", False)),
            stretch=StretchSettings(**payload.get("stretch", {})),
            color_ramp=ColorRampSettings(**payload.get("color_ramp", {})),
            azimuth=float(payload.get("azimuth", 315.0)),
            altitude=float(payload.get("altitude", 45.0)),
            z_factor=float(payload.get("z_factor", 1.0)),
            relief_blend_mode=str(payload.get("relief_blend_mode", "multiply")),
        )
    if renderer_type == "singleband_gray":
        return SinglebandGrayRenderStyle(
            renderer_type=renderer_type,
            band_indices=tuple(payload.get("band_indices", (1,))),
            gamma=float(payload.get("gamma", 1.0)),
            brightness=float(payload.get("brightness", 0.0)),
            contrast=float(payload.get("contrast", 1.0)),
            saturation=float(payload.get("saturation", 1.0)),
            invert=bool(payload.get("invert", False)),
            stretch=StretchSettings(**payload.get("stretch", {})),
        )
    return MultibandRenderStyle(
        renderer_type="multiband",
        band_indices=tuple(payload.get("band_indices", (1, 2, 3))),
        gamma=float(payload.get("gamma", 1.0)),
        brightness=float(payload.get("brightness", 0.0)),
        contrast=float(payload.get("contrast", 1.0)),
        saturation=float(payload.get("saturation", 1.0)),
        invert=bool(payload.get("invert", False)),
        stretch=StretchSettings(**payload.get("stretch", {})),
    )


def display_settings_from_dict(payload: dict[str, Any]) -> LayerDisplaySettings:
    nodata = NodataPolicy(**payload.get("nodata_policy", {}))
    resampling = ResamplingPolicy(**payload.get("resampling", {}))
    return LayerDisplaySettings(
        visible=bool(payload.get("visible", True)),
        opacity=float(payload.get("opacity", 1.0)),
        blend_mode=str(payload.get("blend_mode", "source_over")),
        nodata_policy=nodata,
        alpha_band=payload.get("alpha_band"),
        mask_enabled=bool(payload.get("mask_enabled", False)),
        resampling=resampling,
    )


def default_display_settings(*, visible: bool = True, opacity: float = 1.0, blend_mode: str = "source_over", nodata_value=None) -> LayerDisplaySettings:
    return LayerDisplaySettings(
        visible=visible,
        opacity=opacity,
        blend_mode=blend_mode,
        nodata_policy=NodataPolicy(enabled=True, value=nodata_value, use_source_nodata=True),
    )


def serialize_style_bundle(style: BaseRenderStyle, display_settings: LayerDisplaySettings) -> dict[str, Any]:
    return {
        "style": style_to_dict(style),
        "display_settings": display_settings_to_dict(display_settings),
    }


def deserialize_style_bundle(payload: dict[str, Any]) -> tuple[BaseRenderStyle, LayerDisplaySettings]:
    return (
        style_from_dict(payload.get("style", {})),
        display_settings_from_dict(payload.get("display_settings", {})),
    )


def style_to_display_mode(style: BaseRenderStyle) -> str:
    if isinstance(style, MultibandRenderStyle):
        return "RGB"
    if isinstance(style, HillshadeRenderStyle):
        return "晕渲地貌"
    return "灰度"


def legacy_config_to_style(render_config, metadata=None) -> BaseRenderStyle:
    band_count = max(1, int(getattr(metadata, "band_count", 1) or 1))
    if getattr(render_config, "display_mode", "灰度") == "RGB" and band_count >= 3:
        return MultibandRenderStyle(
            band_indices=tuple(getattr(render_config, "rgb_bands", (1, 2, 3))),
            gamma=float(getattr(render_config, "gamma", 1.0)),
            stretch=StretchSettings(
                stretch_type=str(getattr(render_config, "stretch_mode", "最大最小")).replace("无拉伸", "最大最小"),
                percent_clip=tuple(getattr(render_config, "percent_clip", (2.0, 98.0))),
                std_dev_n=float(getattr(render_config, "std_dev_n", 2.0)),
                auto_range=bool(getattr(render_config, "auto_range", False)),
                min_value=(getattr(render_config, "value_range", (0.0, 1.0)) or (0.0, 1.0))[0],
                max_value=(getattr(render_config, "value_range", (0.0, 1.0)) or (0.0, 1.0))[1],
            ),
        )
    if getattr(render_config, "display_mode", "灰度") == "晕渲地貌":
        params = getattr(render_config, "to_settings", lambda: {})().get("hillshade_params", {})
        return HillshadeRenderStyle(
            band_indices=(int(getattr(render_config, "gray_band", 1)),),
            gamma=float(getattr(render_config, "gamma", 1.0)),
            stretch=StretchSettings(
                stretch_type=str(getattr(render_config, "stretch_mode", "最大最小")),
                percent_clip=tuple(getattr(render_config, "percent_clip", (2.0, 98.0))),
                std_dev_n=float(getattr(render_config, "std_dev_n", 2.0)),
                auto_range=bool(getattr(render_config, "auto_range", True)),
                min_value=(getattr(render_config, "value_range", (0.0, 1.0)) or (0.0, 1.0))[0],
                max_value=(getattr(render_config, "value_range", (0.0, 1.0)) or (0.0, 1.0))[1],
            ),
            color_ramp=ColorRampSettings(
                name=str(getattr(render_config, "colormap_name", "terrain")),
                reversed=bool(getattr(render_config, "colormap_reversed", False)),
            ),
            azimuth=float(params.get("azimuth", 315.0)),
            altitude=float(params.get("altitude", 45.0)),
            z_factor=float(params.get("z_factor", 1.0)),
        )
    if getattr(metadata, "has_color_table", False) and getattr(metadata, "color_table", None):
        return PalettedRenderStyle(
            band_indices=(1,),
            palette=tuple(tuple(int(v) for v in entry) for entry in (metadata.color_table or [])),
        )
    colormap_name = str(getattr(render_config, "colormap_name", "gray"))
    base_kwargs = dict(
        band_indices=(int(getattr(render_config, "gray_band", 1)),),
        gamma=float(getattr(render_config, "gamma", 1.0)),
        invert=bool(getattr(render_config, "colormap_reversed", False) and colormap_name == "gray"),
    )
    stretch = StretchSettings(
        stretch_type=str(getattr(render_config, "stretch_mode", "最大最小")).replace("无拉伸", "最大最小"),
        percent_clip=tuple(getattr(render_config, "percent_clip", (2.0, 98.0))),
        std_dev_n=float(getattr(render_config, "std_dev_n", 2.0)),
        auto_range=bool(getattr(render_config, "auto_range", True)),
        min_value=(getattr(render_config, "value_range", (0.0, 1.0)) or (0.0, 1.0))[0],
        max_value=(getattr(render_config, "value_range", (0.0, 1.0)) or (0.0, 1.0))[1],
    )
    if colormap_name and colormap_name != "gray":
        return SinglebandPseudoColorRenderStyle(
            **base_kwargs,
            stretch=stretch,
            color_ramp=ColorRampSettings(name=colormap_name, reversed=bool(getattr(render_config, "colormap_reversed", False))),
        )
    return SinglebandGrayRenderStyle(**base_kwargs, stretch=stretch)


def style_to_legacy_config(style: BaseRenderStyle, display_settings: LayerDisplaySettings | None = None):
    from .config import RasterRenderConfig

    config = RasterRenderConfig()
    if isinstance(style, MultibandRenderStyle):
        config.display_mode = "RGB"
        config.rgb_bands = tuple(style.band_indices or (1, 2, 3))
        config.stretch_mode = style.stretch.stretch_type
        config.auto_range = style.stretch.auto_range
        config.value_range = (
            float(style.stretch.min_value if style.stretch.min_value is not None else 0.0),
            float(style.stretch.max_value if style.stretch.max_value is not None else 1.0),
        )
        config.percent_clip = tuple(style.stretch.percent_clip)
        config.std_dev_n = float(style.stretch.std_dev_n)
        config.gamma = float(style.gamma)
    elif isinstance(style, HillshadeRenderStyle):
        config.display_mode = "晕渲地貌"
        config.gray_band = int((style.band_indices or (1,))[0])
        config.stretch_mode = style.stretch.stretch_type
        config.auto_range = style.stretch.auto_range
        config.value_range = (
            float(style.stretch.min_value if style.stretch.min_value is not None else 0.0),
            float(style.stretch.max_value if style.stretch.max_value is not None else 1.0),
        )
        config.percent_clip = tuple(style.stretch.percent_clip)
        config.std_dev_n = float(style.stretch.std_dev_n)
        config.gamma = float(style.gamma)
        config.colormap_name = style.color_ramp.name
        config.colormap_reversed = style.color_ramp.reversed
    else:
        config.display_mode = "灰度"
        config.gray_band = int((style.band_indices or (1,))[0])
        config.gamma = float(style.gamma)
        stretch = getattr(style, "stretch", StretchSettings())
        config.stretch_mode = stretch.stretch_type
        config.auto_range = stretch.auto_range
        config.value_range = (
            float(stretch.min_value if stretch.min_value is not None else 0.0),
            float(stretch.max_value if stretch.max_value is not None else 1.0),
        )
        config.percent_clip = tuple(stretch.percent_clip)
        config.std_dev_n = float(stretch.std_dev_n)
        if isinstance(style, SinglebandPseudoColorRenderStyle):
            config.colormap_name = style.color_ramp.name
            config.colormap_reversed = style.color_ramp.reversed
        elif isinstance(style, PalettedRenderStyle):
            config.colormap_name = "gray"
        else:
            config.colormap_name = "gray"
            config.colormap_reversed = bool(style.invert)
    if display_settings is not None and display_settings.nodata_policy.value is not None:
        config.global_value_range = None
    return config


def migrate_style_on_renderer_switch(style: BaseRenderStyle, renderer_type: str, metadata=None) -> BaseRenderStyle:
    band = tuple(getattr(style, "band_indices", ()) or (1,))
    gamma = float(getattr(style, "gamma", 1.0))
    stretch = getattr(style, "stretch", StretchSettings())
    if renderer_type == "multiband":
        return MultibandRenderStyle(
            band_indices=(1, 2, 3) if (metadata and metadata.band_count >= 3) else (band[0], band[0], band[0]),
            channel_gamma=tuple(float(getattr(style, "gamma", gamma)) for _ in range(3)),
            gamma=gamma,
            stretch=stretch if isinstance(stretch, StretchSettings) else StretchSettings(),
        )
    if renderer_type == "singleband_pseudocolor":
        ramp_name = "gray"
        if isinstance(style, SinglebandPseudoColorRenderStyle):
            ramp_name = str(getattr(style.color_ramp, "name", "gray") or "gray")
        return SinglebandPseudoColorRenderStyle(
            band_indices=(band[0],),
            gamma=gamma,
            stretch=stretch,
            color_ramp=ColorRampSettings(name=ramp_name),
        )
    if renderer_type == "unique_value":
        return UniqueValueRenderStyle(band_indices=(band[0],))
    if renderer_type == "paletted":
        palette = tuple(tuple(int(v) for v in entry) for entry in (getattr(metadata, "color_table", None) or []))
        return PalettedRenderStyle(band_indices=(band[0],), palette=palette)
    if renderer_type == "hillshade":
        return HillshadeRenderStyle(band_indices=(band[0],), gamma=gamma, stretch=stretch)
    return SinglebandGrayRenderStyle(band_indices=(band[0],), gamma=gamma, stretch=stretch)
