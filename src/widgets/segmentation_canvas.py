"""
图像分割工具画布适配层。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QEvent, Qt, Signal
import pyqtgraph as pg

from src.rendering.canvas import LayeredRasterCanvas
from src.rendering.models import LayerSpec
from src.rendering.overlays import DraftOverlayItem, PreviewMaskItem, SnapIndicatorItem


@dataclass
class CanvasMousePayload:
    x: float
    y: float
    button: Qt.MouseButton
    buttons: Qt.MouseButtons
    modifiers: Qt.KeyboardModifiers
    double_click: bool = False


class SegmentationCanvas(LayeredRasterCanvas):
    mouse_pressed = Signal(object)
    mouse_moved = Signal(object)
    mouse_released = Signal(object)
    view_state_changed = Signal(object)

    LAYER_ANNOTATIONS = "annotations"
    LAYER_MASK = "mask"
    LAYER_PREVIEW_MASK = "preview_mask"
    LAYER_PREVIEW_VECTOR = "preview_vector"
    LAYER_DRAFT = "draft"
    LAYER_SNAP = "snap"

    def __init__(self, parent=None):
        super().__init__(parent)
        try:
            self.view_box.sigRangeChanged.disconnect(self._on_range_changed)
        except (TypeError, RuntimeError):
            pass
        self.view_box.setMouseEnabled(x=True, y=True)
        self.view_box.setMenuEnabled(False)
        self.view_box.invertY(True)
        self.view_box.setAspectLocked(True)
        self.view_box.setMouseMode(pg.ViewBox.PanMode)

        self.preview_mask_item = PreviewMaskItem()
        self.draft_item = DraftOverlayItem()
        self.snap_item = SnapIndicatorItem()
        self.view_box.addItem(self.preview_mask_item)
        self.view_box.addItem(self.draft_item.scatter)
        self.draft_item.path_item.setParentItem(self.view_box.childGroup)
        self.snap_item.path_item.setParentItem(self.view_box.childGroup)

        self.layer_manager.add_layer(LayerSpec(self.LAYER_PREVIEW_MASK, "预览Mask", "raster_overlay", opacity=0.35), self.preview_mask_item)
        self.layer_manager.add_layer(LayerSpec(self.LAYER_ANNOTATIONS, "矢量", "vector", opacity=1.0))
        self.layer_manager.add_layer(LayerSpec(self.LAYER_MASK, "Mask", "raster_overlay", opacity=0.45))
        self.layer_manager.add_layer(LayerSpec(self.LAYER_PREVIEW_VECTOR, "预览矢量", "vector", opacity=1.0))
        self.layer_manager.add_layer(LayerSpec(self.LAYER_DRAFT, "绘制草稿", "vector", opacity=1.0), self.draft_item.path_item)
        self.layer_manager.add_layer(LayerSpec(self.LAYER_SNAP, "吸附提示", "vector", opacity=1.0), self.snap_item.path_item)

        self._preview_polygon_items: list[object] = []
        self._interaction_mode = "browse"
        self._is_panning = False
        self._refresh_timer.timeout.disconnect()
        self._refresh_timer.timeout.connect(self.refresh_view)
        self.view_box.sigRangeChanged.connect(self._on_view_range_changed)
        self.graphics.viewport().setCursor(Qt.CrossCursor)

    def set_raster_source(self, source, reset_view: bool = True) -> None:
        super().set_raster_source(source, reset_view=reset_view)
        self._dynamic_source = bool(getattr(source.metadata(), "overview_levels", []))

    def set_interaction_mode(self, tool_name: str) -> None:
        self._interaction_mode = tool_name
        self.view_box.setMouseEnabled(x=True, y=True)
        self.view_box.setMouseMode(pg.ViewBox.PanMode)
        self.graphics.viewport().setCursor(Qt.ArrowCursor if tool_name == "browse" else Qt.CrossCursor)

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
                if self.is_panning:
                    return LayeredRasterCanvas.eventFilter(self, obj, event)
                if self._should_consume_left_drag(event):
                    return True
                return True
            elif event.type() == QEvent.MouseButtonRelease:
                if hasattr(event, "button") and event.button() == Qt.MiddleButton:
                    handled = LayeredRasterCanvas.eventFilter(self, obj, event)
                    self._end_pan_interaction()
                    return handled
                if self._should_forward_mouse_event(event):
                    self.mouse_released.emit(self._payload_from_event(event))
                if self._should_consume_left_mouse(event):
                    return True
        return super().eventFilter(obj, event)

    def fit_image(self) -> None:
        self.fit_in_view()

    def restore_view_state(self, state) -> bool:
        result = super().restore_view_state(state)
        self.refresh_view()
        return result

    def update_annotations(
        self,
        annotations,
        label_lookup,
        selected_ids: set[str] | None = None,
        editable_annotation_id: str | None = None,
        active_vertex=None,
    ) -> None:
        def style(annotation):
            label = label_lookup.get(annotation.label_id)
            return label.color if label is not None else "#ffd43b"

        self.set_vector_overlay(
            self.LAYER_ANNOTATIONS,
            annotations,
            style,
            selected_ids=selected_ids,
            editable_feature_id=editable_annotation_id,
            active_vertex=active_vertex,
            name="矢量",
        )

    def update_preview_mask(self, mask: np.ndarray | None, bbox: tuple[int, int, int, int] | None, color_name: str = "#ffd43b") -> None:
        self.preview_mask_item.update_mask(mask, bbox, color_name)

    def update_preview_polygons(self, annotations, color_name: str = "#ffd43b") -> None:
        self.set_vector_overlay(
            self.LAYER_PREVIEW_VECTOR,
            annotations or [],
            color_name,
            name="预览矢量",
        )

    def update_draft(self, points: list[list[float]] | None, color_name: str = "#ffd43b", fill_alpha: int = 40) -> None:
        self.draft_item.update_style(color_name, fill_alpha=fill_alpha)
        self.draft_item.update_geometry(points)

    def update_raster_mask(self, rgba_mask: np.ndarray | None, bbox: tuple[int, int, int, int] | None = None) -> None:
        self.set_raster_overlay(self.LAYER_MASK, rgba_mask, bbox, name="Mask", opacity=0.45)

    def update_snap_indicator(self, snap_type: str | None, position: tuple[float, float] | None = None) -> None:
        if snap_type is None or position is None:
            self.snap_item.clear()
            return
        self.snap_item.update_indicator(snap_type, position[0], position[1])

    def viewport_image(self) -> np.ndarray | None:
        return None if self.last_render is None else self.last_render.display_rgb

    def raw_viewport_image(self) -> np.ndarray | None:
        return None if self.last_render is None else self.last_render.raw_array

    def rendered_rgb_at(self, x: int, y: int):
        if self.last_render is None:
            return None
        x0, y0, width, height = self.last_render.source_window
        if width <= 0 or height <= 0 or not (x0 <= x < x0 + width and y0 <= y < y0 + height):
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

    def _begin_pan_interaction(self) -> None:
        self._is_panning = True
        self._refresh_timer.stop()

    def _end_pan_interaction(self) -> None:
        was_panning = self._is_panning
        self._is_panning = False
        if was_panning:
            self._refresh_timer.start()

    def _should_forward_mouse_event(self, event) -> bool:
        return hasattr(event, "button") and event.button() in (Qt.LeftButton, Qt.RightButton)

    def _should_consume_left_mouse(self, event) -> bool:
        return self._should_forward_mouse_event(event)

    def _should_consume_left_drag(self, event) -> bool:
        return hasattr(event, "buttons") and bool(event.buttons() & Qt.LeftButton)

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

    def _on_view_range_changed(self, *_args) -> None:
        if self.source is not None:
            if self._is_panning:
                self.view_state_changed.emit(self.current_view_state())
                return
            self._refresh_timer.start()
        self.view_state_changed.emit(self.current_view_state())
