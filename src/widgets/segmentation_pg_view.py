"""
基于 pyqtgraph 的图像显示画布。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QVBoxLayout, QWidget
import pyqtgraph as pg

from src.segmentation.image_sources.base import BaseImageSource
from src.segmentation.image_sources.render_request import RenderRequest
from src.segmentation.image_sources.geotiff_source import GeoTiffImageSource
from src.segmentation.rendering import default_render_config
from src.segmentation.models import AnnotationObject, RenderTileResult, ViewportState
from .annotation_overlay_items import (
    DraftOverlayItem,
    PolygonOverlayItem,
    PreviewMaskItem,
    PreviewPolygonItem,
    SnapIndicatorItem,
)


@dataclass
class CanvasMousePayload:
    x: float
    y: float
    button: Qt.MouseButton
    buttons: Qt.MouseButtons
    modifiers: Qt.KeyboardModifiers
    double_click: bool = False


class SegmentationPgView(QWidget):
    mouse_pressed = Signal(object)
    mouse_moved = Signal(object)
    mouse_released = Signal(object)
    view_state_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graphics)

        self.view_box = self.graphics.addViewBox(lockAspect=False, enableMouse=True)
        self.view_box.setMenuEnabled(False)
        self.view_box.invertY(True)
        self.view_box.setAspectLocked(False)
        self.view_box.setMouseMode(pg.ViewBox.PanMode)
        self.image_item = pg.ImageItem(axisOrder="row-major")
        self.raster_item = pg.ImageItem(axisOrder="row-major")
        self.preview_item = PreviewMaskItem()
        self.draft_item = DraftOverlayItem()
        self.snap_item = SnapIndicatorItem()
        self.view_box.addItem(self.image_item)
        self.view_box.addItem(self.raster_item)
        self.view_box.addItem(self.preview_item)
        self.view_box.addItem(self.draft_item.scatter)
        self.draft_item.path_item.setParentItem(self.view_box.childGroup)
        self.snap_item.path_item.setParentItem(self.view_box.childGroup)
        self.raster_item.setOpacity(0.45)

        self.source: BaseImageSource | None = None
        self.last_render: RenderTileResult | None = None
        self.render_config = default_render_config()
        self._overlay_items: dict[str, PolygonOverlayItem] = {}
        self._preview_polygon_items: list[PreviewPolygonItem] = []
        self._dynamic_source = False
        self._last_request_signature = None
        self._interaction_mode = "browse"
        self._is_panning = False
        self._dynamic_render_margin_ratio = 0.35

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(120)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh_view)
        self.view_box.sigRangeChanged.connect(self._on_view_range_changed)

        self.graphics.viewport().installEventFilter(self)
        self.graphics.viewport().setCursor(Qt.CrossCursor)

    def eventFilter(self, obj, event):
        if obj is self.graphics.viewport():
            if event.type() == QEvent.MouseButtonPress:
                if hasattr(event, "button") and event.button() == Qt.MiddleButton:
                    self._begin_pan_interaction()
                if self._should_forward_mouse_event(event):
                    self.mouse_pressed.emit(self._payload_from_event(event))
                if self._should_consume_left_mouse(event):
                    return True
            elif event.type() == QEvent.MouseButtonDblClick:
                if self._should_forward_mouse_event(event):
                    self.mouse_pressed.emit(self._payload_from_event(event, double_click=True))
                if self._should_consume_left_mouse(event):
                    return True
            elif event.type() == QEvent.MouseMove:
                self.mouse_moved.emit(self._payload_from_event(event))
                if self._should_consume_left_drag(event):
                    return True
            elif event.type() == QEvent.MouseButtonRelease:
                if hasattr(event, "button") and event.button() == Qt.MiddleButton:
                    self._end_pan_interaction()
                if self._should_forward_mouse_event(event):
                    self.mouse_released.emit(self._payload_from_event(event))
                if self._should_consume_left_mouse(event):
                    return True
        return super().eventFilter(obj, event)

    def _begin_pan_interaction(self) -> None:
        self._is_panning = True
        if self._dynamic_source:
            self._refresh_timer.stop()

    def _end_pan_interaction(self) -> None:
        was_panning = self._is_panning
        self._is_panning = False
        if self._dynamic_source and was_panning:
            self._refresh_timer.start()

    def _should_forward_mouse_event(self, event) -> bool:
        return hasattr(event, "button") and event.button() in (Qt.LeftButton, Qt.RightButton)

    def _should_consume_left_mouse(self, event) -> bool:
        return self._should_forward_mouse_event(event)

    def _should_consume_left_drag(self, event) -> bool:
        return (
            hasattr(event, "buttons")
            and bool(event.buttons() & Qt.LeftButton)
        )

    def _payload_from_event(self, event, double_click: bool = False) -> CanvasMousePayload:
        scene_pos = self.graphics.mapToScene(event.position().toPoint())
        image_pos = self.view_box.mapSceneToView(scene_pos)
        return CanvasMousePayload(
            x=float(image_pos.x()),
            y=float(image_pos.y()),
            button=event.button(),
            buttons=event.buttons(),
            modifiers=event.modifiers(),
            double_click=double_click,
        )

    def set_image_source(self, source: BaseImageSource) -> None:
        self.source = source
        self._dynamic_source = isinstance(source, GeoTiffImageSource)
        metadata = source.metadata()
        margin_x = metadata.width * 4
        margin_y = metadata.height * 4
        self.view_box.setLimits(
            xMin=-margin_x,
            yMin=-margin_y,
            xMax=metadata.width + margin_x,
            yMax=metadata.height + margin_y,
            minXRange=1,
            minYRange=1,
            maxXRange=metadata.width + margin_x * 2,
            maxYRange=metadata.height + margin_y * 2,
        )
        self.view_box.setRange(xRange=(0, metadata.width), yRange=(0, metadata.height), padding=0.02)
        self._last_request_signature = None
        self.refresh_view()

    def set_render_config(self, render_config) -> None:
        self.render_config = render_config
        self._last_request_signature = None
        if self.source is not None:
            self.refresh_view()

    def set_interaction_mode(self, tool_name: str) -> None:
        self._interaction_mode = tool_name
        self.view_box.setMouseEnabled(x=True, y=True)
        self.view_box.setMouseMode(pg.ViewBox.PanMode)
        self.graphics.viewport().setCursor(Qt.ArrowCursor if tool_name == "browse" else Qt.CrossCursor)

    def refresh_view(self) -> None:
        if self.source is None:
            return
        if not self._dynamic_source and self.last_render is not None:
            self.view_state_changed.emit(self.current_view_state())
            return
        request = self.current_render_request()
        signature = (
            int(request.x),
            int(request.y),
            int(request.width),
            int(request.height),
            int(request.screen_width),
            int(request.screen_height),
        )
        if signature == self._last_request_signature and self.last_render is not None:
            self.view_state_changed.emit(self.current_view_state())
            return
        self._last_request_signature = signature
        result = self.source.render(request, self.render_config)
        self.last_render = result
        self.image_item.setImage(result.display_rgb, autoLevels=False)
        self.image_item.setRect(QRectF(*result.image_rect))
        self.view_state_changed.emit(self.current_view_state())

    def _on_view_range_changed(self, *_args) -> None:
        if self._dynamic_source:
            if self._is_panning:
                self.view_state_changed.emit(self.current_view_state())
                return
            self._refresh_timer.start()
        else:
            self.view_state_changed.emit(self.current_view_state())

    def current_render_request(self) -> RenderRequest:
        ((x0, x1), (y0, y1)) = self.view_box.viewRange()
        view_rect = self.graphics.viewport().rect()
        width = max(x1 - x0, 1)
        height = max(y1 - y0, 1)
        if self._dynamic_source:
            margin_x = width * self._dynamic_render_margin_ratio
            margin_y = height * self._dynamic_render_margin_ratio
            x0 -= margin_x
            y0 -= margin_y
            width += margin_x * 2.0
            height += margin_y * 2.0
        return RenderRequest(
            x=x0,
            y=y0,
            width=width,
            height=height,
            screen_width=max(view_rect.width(), 1),
            screen_height=max(view_rect.height(), 1),
        )

    def current_view_state(self) -> ViewportState:
        ((x0, x1), (y0, y1)) = self.view_box.viewRange()
        center = QPointF((x0 + x1) / 2.0, (y0 + y1) / 2.0)
        view_rect = self.graphics.viewport().rect()
        return ViewportState(
            center_x=center.x(),
            center_y=center.y(),
            scale_x=max(x1 - x0, 1.0) / max(view_rect.width(), 1),
            scale_y=max(y1 - y0, 1.0) / max(view_rect.height(), 1),
            viewport_width=float(view_rect.width()),
            viewport_height=float(view_rect.height()),
        )

    def fit_image(self) -> None:
        if self.source is None:
            return
        meta = self.source.metadata()
        self.view_box.setRange(xRange=(0, meta.width), yRange=(0, meta.height), padding=0.02)
        self.refresh_view()

    def set_one_to_one(self) -> None:
        if self.source is None:
            return
        state = self.current_view_state()
        cx, cy = state.center_x, state.center_y
        rect = self.graphics.viewport().rect()
        width = rect.width()
        height = rect.height()
        self.view_box.setRange(
            xRange=(cx - width / 2.0, cx + width / 2.0),
            yRange=(cy - height / 2.0, cy + height / 2.0),
            padding=0,
        )
        self.refresh_view()

    def restore_view_state(self, state) -> bool:
        if self.source is None or state is None:
            return False
        rect = self.graphics.viewport().rect()
        viewport_width = max(rect.width(), 1)
        viewport_height = max(rect.height(), 1)
        scale_x = float(getattr(state, "scale_x", 0.0) or 0.0)
        scale_y = float(getattr(state, "scale_y", 0.0) or 0.0)
        if scale_x <= 0 or scale_y <= 0:
            return False
        center_x = float(getattr(state, "center_x", 0.0) or 0.0)
        center_y = float(getattr(state, "center_y", 0.0) or 0.0)
        width = max(scale_x * viewport_width, 1.0)
        height = max(scale_y * viewport_height, 1.0)
        self.view_box.setRange(
            xRange=(center_x - width / 2.0, center_x + width / 2.0),
            yRange=(center_y - height / 2.0, center_y + height / 2.0),
            padding=0,
        )
        self.refresh_view()
        return True

    def update_annotations(
        self,
        annotations,
        label_lookup,
        selected_ids: set[str] | None = None,
        editable_annotation_id: str | None = None,
        active_vertex=None,
    ) -> None:
        selected_ids = selected_ids or set()
        for overlay in self._overlay_items.values():
            self.view_box.removeItem(overlay.scatter)
            if overlay.path_item.scene() is not None:
                overlay.path_item.setParentItem(None)
                overlay.path_item.scene().removeItem(overlay.path_item)
        self._overlay_items = {}
        for annotation in annotations:
            label = label_lookup.get(annotation.label_id)
            if label is None:
                continue
            overlay = PolygonOverlayItem(
                annotation,
                label,
                selected=annotation.id in selected_ids,
                editable=annotation.id == editable_annotation_id,
                active_vertex=active_vertex if annotation.id == editable_annotation_id else None,
            )
            overlay.path_item.setParentItem(self.view_box.childGroup)
            self.view_box.addItem(overlay.scatter)
            self._overlay_items[annotation.id] = overlay

    def update_preview_mask(self, mask: np.ndarray | None, bbox: tuple[int, int, int, int] | None, color_name: str = "#ffd43b") -> None:
        self.preview_item.update_mask(mask, bbox, color_name)

    def update_preview_polygons(self, annotations, color_name: str = "#ffd43b") -> None:
        for item in self._preview_polygon_items:
            if item.path_item.scene() is not None:
                item.path_item.setParentItem(None)
                item.path_item.scene().removeItem(item.path_item)
        self._preview_polygon_items = []
        for annotation in annotations or []:
            item = PreviewPolygonItem(annotation, color_name)
            item.path_item.setParentItem(self.view_box.childGroup)
            self._preview_polygon_items.append(item)

    def update_draft(self, points: list[list[float]] | None, color_name: str = "#ffd43b", fill_alpha: int = 40) -> None:
        self.draft_item.update_style(color_name, fill_alpha=fill_alpha)
        self.draft_item.update_geometry(points)

    def update_raster_mask(self, rgba_mask: np.ndarray | None, bbox: tuple[int, int, int, int] | None = None) -> None:
        if rgba_mask is None:
            self.raster_item.clear()
            return
        self.raster_item.setImage(rgba_mask, autoLevels=False)
        if bbox is None:
            self.raster_item.setRect(QRectF(0, 0, rgba_mask.shape[1], rgba_mask.shape[0]))
        else:
            self.raster_item.setRect(QRectF(*bbox))

    def update_snap_indicator(self, snap_type: str | None, position: tuple[float, float] | None = None) -> None:
        if snap_type is None or position is None:
            self.snap_item.clear()
            return
        self.snap_item.update_indicator(snap_type, position[0], position[1])

    def viewport_image(self) -> np.ndarray | None:
        if self.last_render is None:
            return None
        return self.last_render.display_rgb

    def raw_viewport_image(self) -> np.ndarray | None:
        if self.last_render is None:
            return None
        return self.last_render.raw_array

    def rendered_rgb_at(self, x: int, y: int):
        if self.last_render is None:
            return None
        x0, y0, width, height = self.last_render.source_window
        if width <= 0 or height <= 0:
            return None
        if not (x0 <= x < x0 + width and y0 <= y < y0 + height):
            return None
        display = self.last_render.display_rgb
        rel_x = int(np.floor((x - x0) * display.shape[1] / max(width, 1)))
        rel_y = int(np.floor((y - y0) * display.shape[0] / max(height, 1)))
        rel_x = max(0, min(display.shape[1] - 1, rel_x))
        rel_y = max(0, min(display.shape[0] - 1, rel_y))
        value = display[rel_y, rel_x]
        if display.ndim == 2:
            gray = int(value)
            return [gray, gray, gray]
        if len(value) >= 3:
            return [int(value[0]), int(value[1]), int(value[2])]
        gray = int(value[0])
        return [gray, gray, gray]
