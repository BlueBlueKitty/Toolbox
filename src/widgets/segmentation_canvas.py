"""
图像分割工具画布适配层。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QEvent, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QGraphicsEllipseItem
import pyqtgraph as pg

from src.rendering.canvas import LayeredRasterCanvas
from src.rendering.models import LayerSpec
from src.rendering.overlays import DraftOverlayItem, MaskSelectionItem, PreviewMaskItem, SnapIndicatorItem


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
    tool_wheel_adjust_requested = Signal(int)

    LAYER_ANNOTATIONS = "annotations"
    LAYER_MASK = "mask"
    LAYER_PREVIEW_VECTOR = "preview_vector"
    LAYER_DRAFT = "draft"
    LAYER_SNAP = "snap"

    _CURSOR_HOTSPOT_X = 7
    _CURSOR_HOTSPOT_Y = 7
    def __init__(self, parent=None):
        super().__init__(parent)
        # 分割工具单窗工作区更大，沿用通用画布的超大预取边距会显著放大
        # 每次交互后的重采样面积，导致同数据下比其它查看器更容易感觉到延迟。
        # 这里收敛到更稳妥的 0.5 倍边距，保留交互缓冲的同时降低重绘成本。
        self._dynamic_render_margin_ratio = 0.50
        self._dynamic_zoom_margin_ratio = 0.50
        self._dynamic_pan_margin_ratio = 0.50
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
        self.mask_selection_item = MaskSelectionItem()
        self.preview_mask_outline_item = MaskSelectionItem("#ffd43b", "#ffffff")
        self.draft_item = DraftOverlayItem()
        self.snap_item = SnapIndicatorItem()
        self.view_box.addItem(self.preview_mask_item)
        self.mask_selection_item.path_item.setParentItem(self.view_box.childGroup)
        self.preview_mask_outline_item.path_item.setParentItem(self.view_box.childGroup)
        self.view_box.addItem(self.draft_item.scatter)
        self.draft_item.path_item.setParentItem(self.view_box.childGroup)
        self.snap_item.path_item.setParentItem(self.view_box.childGroup)

        self.layer_manager.add_layer(LayerSpec(self.LAYER_ANNOTATIONS, "矢量", "vector", opacity=1.0))
        self.layer_manager.add_layer(LayerSpec(self.LAYER_MASK, "Mask", "raster_overlay", opacity=0.5, locked=True))
        self.layer_manager.add_layer(LayerSpec(self.LAYER_PREVIEW_VECTOR, "预览矢量", "vector", opacity=1.0))
        self.layer_manager.add_layer(LayerSpec(self.LAYER_DRAFT, "绘制草稿", "vector", opacity=1.0), self.draft_item.path_item)
        self.layer_manager.add_layer(LayerSpec(self.LAYER_SNAP, "吸附提示", "vector", opacity=1.0), self.snap_item.path_item)

        self._preview_polygon_items: list[object] = []
        self._interaction_mode = "browse"
        self._is_panning = False
        self._last_pointer_payload: CanvasMousePayload | None = None
        self._tool_color = QColor("#ffd43b")
        self._tool_icon_sources: dict[str, QIcon] = {}
        self._brush_radius = 6.0
        self._brush_range_item = QGraphicsEllipseItem()
        range_pen = QPen(self._tool_color, 1.2)
        range_pen.setCosmetic(True)
        self._brush_range_item.setPen(range_pen)
        self._brush_range_item.setBrush(Qt.NoBrush)
        self._brush_range_item.setZValue(20_000)
        self._brush_range_item.setVisible(False)
        self.view_box.addItem(self._brush_range_item)
        self._tool_cursors = {
            "magic_wand": self._make_tool_cursor("magic"),
            "brush": self._make_tool_cursor("brush"),
            "eraser": self._make_tool_cursor("eraser"),
        }
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
        self.graphics.viewport().setCursor(self._cursor_for_tool(tool_name))
        self._brush_range_item.setVisible(False)

    def set_brush_radius(self, radius: float) -> None:
        self._brush_radius = max(0.2, float(radius))
        if self._last_pointer_payload is not None:
            self._update_brush_range_indicator(self._last_pointer_payload)

    def set_tool_color(self, color_name: str) -> None:
        color = QColor(color_name)
        if not color.isValid():
            color = QColor("#ffd43b")
        self._tool_color = color
        range_color = QColor(color)
        range_color.setAlpha(230)
        pen = QPen(range_color, 1.2)
        pen.setCosmetic(True)
        self._brush_range_item.setPen(pen)
        self._rebuild_tool_cursors()
        if self._last_pointer_payload is not None:
            self._update_brush_range_indicator(self._last_pointer_payload)

    def set_tool_icons(self, icons: dict[str, QIcon]) -> None:
        self._tool_icon_sources = {
            tool_name: icon
            for tool_name, icon in icons.items()
            if icon is not None and not icon.isNull()
        }
        self._rebuild_tool_cursors()

    def _rebuild_tool_cursors(self) -> None:
        # 旋转图标以适应光标方向
        mapping = {
            "magic_wand": ("magic_wand", -90),
            "brush": ("brush", 90),
            "eraser": ("eraser", 0),
        }
        for tool_name, (_kind, angle) in mapping.items():
            icon = self._tool_icon_sources.get(tool_name)
            if icon is not None and not icon.isNull():
                self._tool_cursors[tool_name] = self._make_cursor_from_icon(icon, angle, self._tool_color)
        self.graphics.viewport().setCursor(self._cursor_for_tool(self._interaction_mode))

    def refresh_view(self):
        super().refresh_view()
        self.view_state_changed.emit(self.current_view_state())

    def eventFilter(self, obj, event):
        if obj is self.graphics.viewport():
            if event.type() == QEvent.Wheel and self._handle_tool_wheel_adjust(event):
                return True
            if event.type() == QEvent.MouseButtonPress:
                if hasattr(event, "button") and event.button() == Qt.LeftButton:
                    # 分割画布在此处自行消费左键事件，需主动通知工作区切换活动窗口，
                    # 否则双窗模式下窗口2点击不会被设为活动窗口，后续工具事件仍发往窗口1。
                    self.canvas_left_clicked.emit()
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
                if self.is_panning:
                    return LayeredRasterCanvas.eventFilter(self, obj, event)
                payload = self._payload_from_event(event)
                self._last_pointer_payload = payload
                self.mouse_moved.emit(payload)
                self._update_brush_range_indicator(payload)
                if self._should_consume_left_drag(event):
                    return True
                return True
            elif event.type() == QEvent.Leave:
                self.update_synced_pointer(None, None, visible=False)
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
        # 对动态源交由 rangeChanged 定时刷新，避免双窗同步时“立即刷新 + 定时刷新”双重开销。
        if result and self.source is None:
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
        self.preview_mask_outline_item.set_colors(color_name, "#ffffff")
        self.preview_mask_outline_item.update_mask(mask, bbox)

    def set_preview_mask_dash_offset(self, offset: float) -> None:
        self.preview_mask_outline_item.set_dash_offset(offset)

    def update_mask_selection(self, mask: np.ndarray | None, bbox: tuple[int, int, int, int] | None) -> None:
        self.mask_selection_item.update_mask(mask, bbox)

    def update_mask_selections(self, selections) -> None:
        self.mask_selection_item.update_masks(selections)

    def set_mask_selection_dash_offset(self, offset: float) -> None:
        self.mask_selection_item.set_dash_offset(offset)

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
            self._refresh_timer.start(1)
        self.graphics.viewport().setCursor(self._cursor_for_tool(self._interaction_mode))

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

    def _cursor_for_tool(self, tool_name: str):
        if tool_name == "browse":
            return Qt.ArrowCursor
        return self._tool_cursors.get(tool_name, Qt.CrossCursor)

    def _make_tool_cursor(self, kind: str) -> QCursor:
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#ffffff"), 3))
        painter.drawLine(2, 7, 12, 7)
        painter.drawLine(7, 2, 7, 12)
        painter.setPen(QPen(QColor("#111827"), 1))
        painter.drawLine(2, 7, 12, 7)
        painter.drawLine(7, 2, 7, 12)
        if kind == "magic":
            painter.setPen(QPen(QColor("#ffffff"), 5))
            painter.drawLine(13, 13, 28, 28)
            painter.setPen(QPen(QColor("#7c3aed"), 2))
            painter.drawLine(13, 13, 28, 28)
            painter.drawLine(13, 13, 19, 11)
            painter.drawLine(13, 13, 11, 19)
            painter.drawLine(22, 8, 22, 12)
            painter.drawLine(20, 10, 24, 10)
        elif kind == "eraser":
            painter.setPen(QPen(QColor("#ffffff"), 5))
            painter.drawLine(13, 13, 27, 27)
            painter.setPen(QPen(QColor("#ef4444"), 2))
            eraser = QPainterPath()
            eraser.moveTo(12, 17)
            eraser.lineTo(18, 11)
            eraser.lineTo(28, 21)
            eraser.lineTo(22, 27)
            eraser.closeSubpath()
            painter.setBrush(QColor(255, 255, 255, 230))
            painter.drawPath(eraser)
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(16, 21, 22, 15)
        else:
            painter.setPen(QPen(QColor("#ffffff"), 5))
            painter.drawLine(13, 13, 27, 27)
            painter.setPen(QPen(QColor("#2563eb"), 2))
            painter.drawLine(13, 13, 27, 27)
            painter.setPen(QPen(QColor("#ffffff"), 4))
            painter.drawPoint(28, 28)
            painter.setPen(QPen(QColor("#111827"), 2))
            painter.drawPoint(28, 28)
        painter.end()
        return QCursor(pixmap, self._CURSOR_HOTSPOT_X, self._CURSOR_HOTSPOT_Y)

    def _make_cursor_from_icon(self, icon: QIcon, angle: float, color: QColor | None = None) -> QCursor:
        pixmap = QPixmap(36, 36)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#ffffff"), 3))
        painter.drawLine(2, 7, 12, 7)
        painter.drawLine(7, 2, 7, 12)
        painter.setPen(QPen(QColor("#111827"), 1))
        painter.drawLine(2, 7, 12, 7)
        painter.drawLine(7, 2, 7, 12)

        icon_pixmap = icon.pixmap(22, 22)
        if not icon_pixmap.isNull():
            rotated = icon_pixmap.transformed(QTransform().rotate(angle), Qt.SmoothTransformation)
            if color is not None and color.isValid():
                tinted = QPixmap(rotated.size())
                tinted.fill(Qt.transparent)
                tint_painter = QPainter(tinted)
                tint_painter.drawPixmap(0, 0, rotated)
                tint_painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
                tint_color = QColor(color)
                tint_color.setAlpha(245)
                tint_painter.fillRect(tinted.rect(), tint_color)
                tint_painter.end()
                rotated = tinted
            painter.drawPixmap(13, 12, rotated)
        painter.end()
        return QCursor(pixmap, self._CURSOR_HOTSPOT_X, self._CURSOR_HOTSPOT_Y)

    def _update_brush_range_indicator(self, payload: CanvasMousePayload) -> None:
        if self._interaction_mode not in {"brush", "eraser"}:
            self._brush_range_item.setVisible(False)
            return
        radius = float(max(0.2, self._brush_radius))
        self._brush_range_item.setRect(payload.x - radius, payload.y - radius, radius * 2, radius * 2)
        self._brush_range_item.setVisible(True)
        self._brush_range_item.update()
        self.graphics.viewport().update()

    def _emit_mouse_moved(self, x: int, y: int, _value, event=None) -> None:
        if event is not None:
            payload = self._payload_from_event(event)
        else:
            payload = CanvasMousePayload(
                x=float(x),
                y=float(y),
                button=Qt.NoButton,
                buttons=Qt.NoButton,
                modifiers=Qt.NoModifier,
            )
        self._last_pointer_payload = payload
        self.mouse_moved.emit(payload)
        self._update_brush_range_indicator(payload)

    def _handle_tool_wheel_adjust(self, event) -> bool:
        if self._interaction_mode not in {"magic_wand", "brush", "eraser"}:
            return False
        if not (event.modifiers() & Qt.ControlModifier):
            return False
        steps = int(event.angleDelta().y() / 120)
        if steps == 0:
            steps = 1 if event.angleDelta().y() > 0 else -1
        self.tool_wheel_adjust_requested.emit(steps)
        return True

    def _on_view_range_changed(self, *_args) -> None:
        self._update_current_zoom_from_view_range()
        if self._suspend_range_signal:
            return
        if self.source is not None:
            if self._is_panning:
                if not self.is_syncing:
                    self.view_transformed.emit(self.capture_view_state())
                return
            self._refresh_timer.start()
        if not self.is_syncing:
            self.view_transformed.emit(self.capture_view_state())
        if self.source is None:
            self.view_state_changed.emit(self.current_view_state())
