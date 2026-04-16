"""
图像分割工具数据模型。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional
import uuid


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class OverviewInfo:
    level_index: int
    downsample_factor: float
    width: int
    height: int
    source_type: str


@dataclass
class ImageAsset:
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
    overview_levels: list[OverviewInfo] = field(default_factory=list)


@dataclass
class ViewportState:
    center_x: float = 0.0
    center_y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    viewport_width: float = 0.0
    viewport_height: float = 0.0


@dataclass
class RenderTileResult:
    raw_array: Any
    display_rgb: Any
    image_rect: tuple[float, float, float, float]
    overview_level: Optional[OverviewInfo]
    source_window: tuple[int, int, int, int]


@dataclass
class LabelClass:
    id: int
    name: str
    color: str
    shortcut: str
    visible: bool = True
    locked: bool = False


@dataclass
class AnnotationObject:
    id: str
    label_id: int
    geom_type: str
    exterior: list[list[float]]
    holes: list[list[list[float]]] = field(default_factory=list)
    bbox: Optional[list[float]] = None
    source_tool: str = "polygon"
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def clone(self) -> "AnnotationObject":
        return AnnotationObject.from_dict(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_polygon(
        cls,
        label_id: int,
        exterior: list[list[float]],
        holes: Optional[list[list[list[float]]]] = None,
        geom_type: str = "polygon",
        source_tool: str = "polygon",
    ) -> "AnnotationObject":
        xs = [pt[0] for pt in exterior]
        ys = [pt[1] for pt in exterior]
        bbox = [min(xs), min(ys), max(xs), max(ys)] if exterior else None
        return cls(
            id=new_id(),
            label_id=label_id,
            geom_type=geom_type,
            exterior=exterior,
            holes=holes or [],
            bbox=bbox,
            source_tool=source_tool,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AnnotationObject":
        return cls(**payload)


@dataclass
class MagicWandParams:
    tolerance: int = 15
    connectivity: int = 8
    min_area: int = 16
    similarity_mode: str = "rgba"
    fill_holes: bool = False
    simplify_polygon: bool = True
    vector_smoothness: int = 2


@dataclass
class PreviewSelection:
    seed_point: tuple[int, int]
    params: MagicWandParams
    bbox: tuple[int, int, int, int]
    mask: Any
    contours: list[list[list[float]]] = field(default_factory=list)
    polygon_preview: list[AnnotationObject] = field(default_factory=list)


@dataclass
class DisplayState:
    zoom: float = 1.0
    center: tuple[float, float] = (0.0, 0.0)
    show_image: bool = True
    show_annotations: bool = True
    show_raster: bool = True
    show_preview: bool = True
    show_preview_vector: bool = True
    show_preview_mask: bool = True


@dataclass
class SegmentationProject:
    project_version: str
    image_asset: Optional[ImageAsset]
    labels: list[LabelClass] = field(default_factory=list)
    annotations: list[AnnotationObject] = field(default_factory=list)
    annotations_asset: dict[str, Any] = field(default_factory=dict)
    mask_asset: dict[str, Any] = field(default_factory=dict)
    mask_data: Any = field(default=None, repr=False, compare=False)
    display_state: DisplayState = field(default_factory=DisplayState)
    active_tool: str = "browse"
    active_label_id: Optional[int] = None
    layer_visibility: dict[str, bool] = field(
        default_factory=lambda: {
            "image": True,
            "annotations": True,
            "raster": True,
            "preview": True,
            "preview_vector": True,
            "preview_mask": True,
        }
    )
    export_prefs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        image_asset = asdict(self.image_asset) if self.image_asset else None
        return {
            "project_version": self.project_version,
            "image_asset": image_asset,
            "labels": [asdict(label) for label in self.labels],
            "annotations_asset": dict(self.annotations_asset),
            "mask_asset": dict(self.mask_asset),
            "display_state": asdict(self.display_state),
            "active_tool": self.active_tool,
            "active_label_id": self.active_label_id,
            "layer_visibility": dict(self.layer_visibility),
            "export_prefs": dict(self.export_prefs),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SegmentationProject":
        image_asset = payload.get("image_asset")
        overview_levels = []
        if image_asset:
            overview_levels = [
                OverviewInfo(**item) for item in image_asset.get("overview_levels", [])
            ]
            image_asset = ImageAsset(
                **{
                    **image_asset,
                    "overview_levels": overview_levels,
                }
            )
        return cls(
            project_version=payload.get("project_version", "1.0"),
            image_asset=image_asset,
            labels=[LabelClass(**item) for item in payload.get("labels", [])],
            annotations=[],
            annotations_asset=payload.get("annotations_asset", {}),
            mask_asset=payload.get("mask_asset", {}),
            display_state=DisplayState(**payload.get("display_state", {})),
            active_tool=payload.get("active_tool", "browse"),
            active_label_id=payload.get("active_label_id"),
            layer_visibility=payload.get("layer_visibility", {}),
            export_prefs=payload.get("export_prefs", {}),
        )
