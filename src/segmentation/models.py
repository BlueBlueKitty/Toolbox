"""
图像分割工具数据模型。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields as dataclass_fields
from datetime import datetime
from typing import Any, Optional
import uuid

from src.rendering.models import ImageSourceMetadata, OverviewInfo, ViewportState


def utc_now_iso() -> str:
    """utc_now_iso。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        无。
    返回:
        str: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def new_id() -> str:
    """new_id。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        无。
    返回:
        str: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    return uuid.uuid4().hex


def round_image_coord(value: float) -> float:
    """round_image_coord。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        value (float): 输入参数。
    返回:
        float: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    return round(float(value), 3)


def round_image_ring(points: list[list[float]]) -> list[list[float]]:
    """round_image_ring。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        points (list[list[float]]): 输入参数。
    返回:
        list[list[float]]: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    return [[round_image_coord(x), round_image_coord(y)] for x, y in points]


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
    coord_ref: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def clone(self) -> "AnnotationObject":
        """clone。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            'AnnotationObject': 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        return AnnotationObject.from_dict(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        """to_dict。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            dict[str, Any]: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        payload = asdict(self)
        payload["exterior"] = round_image_ring(self.exterior)
        payload["holes"] = [round_image_ring(hole) for hole in self.holes]
        if self.bbox is not None:
            payload["bbox"] = [round_image_coord(value) for value in self.bbox]
        return payload

    @classmethod
    def from_polygon(
        cls,
        label_id: int,
        exterior: list[list[float]],
        holes: Optional[list[list[list[float]]]] = None,
        geom_type: str = "polygon",
        source_tool: str = "polygon",
    ) -> "AnnotationObject":
        """from_polygon。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            label_id (int): 输入参数。
            exterior (list[list[float]]): 输入参数。
            holes (Optional[list[list[list[float]]]]): 输入参数。
            geom_type (str): 输入参数。
            source_tool (str): 输入参数。
        返回:
            'AnnotationObject': 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        复杂度:
            时间和空间复杂度与输入规模线性或近线性相关。
        """
        exterior = round_image_ring(exterior)
        holes = [round_image_ring(hole) for hole in (holes or [])]
        xs = [pt[0] for pt in exterior]
        ys = [pt[1] for pt in exterior]
        bbox = [min(xs), min(ys), max(xs), max(ys)] if exterior else None
        return cls(
            id=new_id(),
            label_id=label_id,
            geom_type=geom_type,
            exterior=exterior,
            holes=holes,
            bbox=bbox,
            source_tool=source_tool,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AnnotationObject":
        """from_dict。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            payload (dict[str, Any]): 输入参数。
        返回:
            'AnnotationObject': 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        return cls(**_filter_dataclass_payload(cls, payload))


@dataclass
class MagicWandParams:
    tolerance: int = 15
    connectivity: int = 8
    min_area: int = 16
    similarity_mode: str = "rgba"
    fill_small_holes: bool = False
    fill_all_holes: bool = False


@dataclass
class PreviewSelection:
    seed_point: tuple[int, int]
    params: MagicWandParams
    bbox: tuple[int, int, int, int]
    mask: Any
    contours: list[list[list[float]]] = field(default_factory=list)
    polygon_preview: list[AnnotationObject] = field(default_factory=list)
    pixel_area: int = 0
    filtered_by_min_area: bool = False


@dataclass
class DisplayState:
    zoom: float = 1.0
    center: tuple[float, float] = (0.0, 0.0)
    center_x: float = 0.0
    center_y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    viewport_width: float = 0.0
    viewport_height: float = 0.0
    show_image: bool = True
    show_annotations: bool = True
    show_raster: bool = True
    show_preview: bool = True
    show_preview_vector: bool = False
    show_preview_mask: bool = True

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DisplayState":
        """from_dict。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            payload (dict[str, Any]): 输入参数。
        返回:
            'DisplayState': 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        payload = dict(payload or {})
        center = payload.get("center", (0.0, 0.0))
        if isinstance(center, (list, tuple)) and len(center) >= 2:
            payload.setdefault("center_x", float(center[0]))
            payload.setdefault("center_y", float(center[1]))
        else:
            payload["center"] = (0.0, 0.0)
        payload["center"] = (
            float(payload.get("center_x", 0.0)),
            float(payload.get("center_y", 0.0)),
        )
        return cls(**_filter_dataclass_payload(cls, payload))


@dataclass
class SegmentationProject:
    project_version: str
    image_asset: Optional[ImageSourceMetadata]
    labels: list[LabelClass] = field(default_factory=list)
    annotations: list[AnnotationObject] = field(default_factory=list)
    annotations_asset: dict[str, Any] = field(default_factory=dict)
    mask_asset: dict[str, Any] = field(default_factory=dict)
    mask_data: Any = field(default=None, repr=False, compare=False)
    display_state: DisplayState = field(default_factory=DisplayState)
    active_tool: str = "browse"
    active_label_id: Optional[int] = None
    magic_panel_settings: dict[str, Any] = field(default_factory=dict)
    layer_visibility: dict[str, bool] = field(
        default_factory=lambda: {
            "base_raster": True,
            "annotations": True,
            "mask": True,
            "preview_vector": False,
            "preview_mask": True,
        }
    )
    export_prefs: dict[str, Any] = field(default_factory=dict)
    coordinate_mode: str = "pixel"
    primary_window_id: str = "viewer_1"

    def to_dict(self) -> dict[str, Any]:
        """to_dict。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            dict[str, Any]: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
            "magic_panel_settings": dict(self.magic_panel_settings),
            "layer_visibility": dict(self.layer_visibility),
            "export_prefs": dict(self.export_prefs),
            "coordinate_mode": self.coordinate_mode,
            "primary_window_id": self.primary_window_id,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SegmentationProject":
        """from_dict。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            payload (dict[str, Any]): 输入参数。
        返回:
            'SegmentationProject': 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        image_asset = payload.get("image_asset")
        overview_levels = []
        if image_asset:
            image_asset = dict(image_asset)
            overview_levels = [
                OverviewInfo(**_filter_dataclass_payload(OverviewInfo, item))
                for item in image_asset.get("overview_levels", [])
                if isinstance(item, dict)
            ]
            image_asset["overview_levels"] = overview_levels
            if isinstance(image_asset.get("geotransform"), list):
                image_asset["geotransform"] = tuple(image_asset["geotransform"])
            if isinstance(image_asset.get("resolution"), list):
                image_asset["resolution"] = tuple(image_asset["resolution"])
            image_asset = ImageSourceMetadata(**_filter_dataclass_payload(ImageSourceMetadata, image_asset))
        return cls(
            project_version=payload.get("project_version", "1.0"),
            image_asset=image_asset,
            labels=[
                LabelClass(**_filter_dataclass_payload(LabelClass, item))
                for item in payload.get("labels", [])
                if isinstance(item, dict)
            ],
            annotations=[],
            annotations_asset=payload.get("annotations_asset", {}) if isinstance(payload.get("annotations_asset", {}), dict) else {},
            mask_asset=payload.get("mask_asset", {}) if isinstance(payload.get("mask_asset", {}), dict) else {},
            display_state=DisplayState.from_dict(payload.get("display_state", {})),
            active_tool=payload.get("active_tool", "browse"),
            active_label_id=payload.get("active_label_id"),
            magic_panel_settings=payload.get("magic_panel_settings", {}) if isinstance(payload.get("magic_panel_settings", {}), dict) else {},
            layer_visibility=_normalize_layer_visibility(payload.get("layer_visibility", {})),
            export_prefs=payload.get("export_prefs", {}) if isinstance(payload.get("export_prefs", {}), dict) else {},
            coordinate_mode=payload.get("coordinate_mode", "pixel"),
            primary_window_id=payload.get("primary_window_id", "viewer_1"),
        )


def _normalize_layer_visibility(payload: dict[str, Any]) -> dict[str, bool]:
    """_normalize_layer_visibility。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        payload (dict[str, Any]): 输入参数。
    返回:
        dict[str, bool]: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    values = dict(payload or {}) if isinstance(payload, dict) else {}
    if "image" in values and "base_raster" not in values:
        values["base_raster"] = values.pop("image")
    if "raster" in values and "mask" not in values:
        values["mask"] = values.pop("raster")
    values.pop("preview", None)
    defaults = {
        "base_raster": True,
        "annotations": True,
        "mask": True,
        "preview_vector": False,
        "preview_mask": True,
    }
    return {**defaults, **values}


def _filter_dataclass_payload(dataclass_type, payload: dict[str, Any] | None) -> dict[str, Any]:
    """_filter_dataclass_payload。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        dataclass_type (Any): 输入参数。
        payload (dict[str, Any] | None): 输入参数。
    返回:
        dict[str, Any]: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    source = dict(payload or {})
    allowed = {item.name for item in dataclass_fields(dataclass_type)}
    return {key: value for key, value in source.items() if key in allowed}
