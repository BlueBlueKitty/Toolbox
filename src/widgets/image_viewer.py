"""
基于 pyqtgraph 的通用图像查看器。
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QBrush, QPen
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsLineItem, QVBoxLayout, QWidget
import pyqtgraph as pg

from src.segmentation.rendering import default_render_config, render_base_rgb


class ImageViewer(QWidget):
    pixel_clicked = Signal(int, int)
    mouse_moved = Signal(int, int, object)
    view_transformed = Signal(object)
    cursor_changed = Signal(object)
    scroll_changed = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graphics)
        self.view_box = self.graphics.addViewBox(lockAspect=False, enableMouse=False)
        self.view_box.setMenuEnabled(False)
        self.view_box.invertY(True)
        self.image_item = pg.ImageItem(axisOrder="row-major")
        self.view_box.addItem(self.image_item)

        self.image_array = None
        self.display_array = None
        self.is_normalized = False
        self.original_width = 0
        self.original_height = 0
        self.downsample_factor = 1.0
        self.current_colormap = "gray"
        self.colormap_reversed = False
        self.current_zoom = 1.0
        self.zoom_factor = 1.15
        self.min_zoom = 0.1
        self.max_zoom = 1000.0
        self.is_panning = False
        self.pan_start_pos = None
        self.is_syncing = False
        self.nodata_value = None
        self.geotransform = None
        self.projection = None
        self.scene_world_rect = None
        self.image_world_rect = None
        self.render_settings = None
        self.selected_pixel = None
        self._selected_pixel_items = []
        self._image_rect = QRectF(0, 0, 0, 0)
        self._suspend_range_signal = False

        self.graphics.viewport().installEventFilter(self)
        self.graphics.viewport().setMouseTracking(True)
        self.graphics.viewport().setCursor(Qt.ArrowCursor)
        self.view_box.sigRangeChanged.connect(self._on_range_changed)

    def set_image_from_array(self, image_array, original_size=None):
        self.image_array = image_array
        self.is_normalized = False
        if original_size is not None:
            self.original_width, self.original_height = original_size
            self.downsample_factor = self.original_width / max(image_array.shape[1], 1)
        else:
            self.original_height, self.original_width = image_array.shape[:2]
            self.downsample_factor = 1.0
        self._update_display()

    def set_scene_mapping(self, scene_world_rect=None, image_world_rect=None):
        self.scene_world_rect = scene_world_rect
        self.image_world_rect = image_world_rect
        if self.image_array is not None:
            self._update_image_rect()

    def capture_view_state(self):
        ((x0, x1), (y0, y1)) = self.view_box.viewRange()
        return {
            "x_range": (float(x0), float(x1)),
            "y_range": (float(y0), float(y1)),
            "scene_center_x": float((x0 + x1) / 2.0),
            "scene_center_y": float((y0 + y1) / 2.0),
        }

    def restore_view_state(self, state):
        if not state:
            return
        self.is_syncing = True
        self._suspend_range_signal = True
        if "x_range" in state and "y_range" in state:
            self.view_box.setRange(xRange=state["x_range"], yRange=state["y_range"], padding=0)
        elif "scene_center_x" in state and "scene_center_y" in state:
            current = self.capture_view_state()
            x0, x1 = current["x_range"]
            y0, y1 = current["y_range"]
            cx = float(state["scene_center_x"])
            cy = float(state["scene_center_y"])
            half_w = max((x1 - x0) / 2.0, 0.5)
            half_h = max((y1 - y0) / 2.0, 0.5)
            self.view_box.setRange(xRange=(cx - half_w, cx + half_w), yRange=(cy - half_h, cy + half_h), padding=0)
        self._suspend_range_signal = False
        self.is_syncing = False

    def fit_in_view(self, delayed=False):
        if delayed:
            QTimer.singleShot(100, lambda: self.fit_in_view(delayed=False))
            return
        rect = self._current_scene_rect()
        if rect.isNull():
            return
        self._suspend_range_signal = True
        self.view_box.setRange(xRange=(rect.left(), rect.right()), yRange=(rect.top(), rect.bottom()), padding=0.02)
        self._suspend_range_signal = False
        self.current_zoom = 1.0

    def zoom_in(self):
        if self.current_zoom * self.zoom_factor <= self.max_zoom:
            self.view_box.scaleBy((1.0 / self.zoom_factor, 1.0 / self.zoom_factor))
            self.current_zoom *= self.zoom_factor

    def zoom_out(self):
        if self.current_zoom / self.zoom_factor >= self.min_zoom:
            self.view_box.scaleBy((self.zoom_factor, self.zoom_factor))
            self.current_zoom /= self.zoom_factor

    def set_colormap(self, colormap_name):
        self.current_colormap = colormap_name
        self._update_display()

    def set_colormap_reversed(self, reversed):
        self.colormap_reversed = reversed
        if self.image_array is not None:
            self._update_display()

    def set_render_settings(self, settings):
        self.render_settings = settings
        if settings:
            self.colormap_reversed = settings.get("colormap_reversed", False)
        if self.image_array is not None:
            self._update_display()

    def set_nodata_value(self, nodata_value):
        self.nodata_value = nodata_value
        if self.image_array is not None:
            self._update_display()

    def set_geotransform(self, geotransform, projection=None):
        self.geotransform = geotransform
        self.projection = projection

    def set_selected_pixel(self, x, y):
        self.selected_pixel = (int(x), int(y))
        self._rebuild_selected_pixel_marker()

    def clear_selected_pixel(self):
        self.selected_pixel = None
        self._clear_selected_pixel_marker()

    def sync_transform(self, transform):
        self.restore_view_state(transform)

    def sync_cursor(self, cursor):
        self.is_syncing = True
        self.graphics.viewport().setCursor(cursor)
        self.is_syncing = False

    def sync_scroll(self, h_value, v_value):
        if isinstance(h_value, dict):
            self.sync_transform(h_value)

    def get_image_size(self):
        if self.original_width > 0 and self.original_height > 0:
            return self.original_height, self.original_width
        if self.image_array is not None:
            return self.image_array.shape[:2]
        return None

    def get_pixel_value(self, x, y):
        if self.image_array is None:
            return None
        display_x = int(x / max(self.downsample_factor, 1e-9))
        display_y = int(y / max(self.downsample_factor, 1e-9))
        if 0 <= display_x < self.image_array.shape[1] and 0 <= display_y < self.image_array.shape[0]:
            return self.image_array[display_y, display_x]
        return None

    def image_pos_from_event(self, event):
        scene_pos = self.graphics.mapToScene(event.position().toPoint())
        view_pos = self.view_box.mapSceneToView(scene_pos)
        return self._view_to_image_pos(view_pos)

    def image_contains_pos(self, point: QPointF) -> bool:
        if self.image_array is None:
            return False
        return 0 <= point.x() < self.image_array.shape[1] and 0 <= point.y() < self.image_array.shape[0]

    def eventFilter(self, obj, event):
        if obj is self.graphics.viewport():
            if event.type() == QEvent.MouseButtonPress:
                return self._handle_mouse_press(event)
            if event.type() == QEvent.MouseMove:
                return self._handle_mouse_move(event)
            if event.type() == QEvent.MouseButtonRelease:
                return self._handle_mouse_release(event)
            if event.type() == QEvent.Wheel:
                self.wheelEvent(event)
                return True
        return super().eventFilter(obj, event)

    def wheelEvent(self, event):
        scene_pos = self.graphics.mapToScene(event.position().toPoint())
        center = self.view_box.mapSceneToView(scene_pos)
        if event.angleDelta().y() > 0 and self.current_zoom * self.zoom_factor <= self.max_zoom:
            self.view_box.scaleBy((1.0 / self.zoom_factor, 1.0 / self.zoom_factor), center=center)
            self.current_zoom *= self.zoom_factor
        elif event.angleDelta().y() <= 0 and self.current_zoom / self.zoom_factor >= self.min_zoom:
            self.view_box.scaleBy((self.zoom_factor, self.zoom_factor), center=center)
            self.current_zoom /= self.zoom_factor

    def _handle_mouse_press(self, event) -> bool:
        if event.button() == Qt.LeftButton and self.image_array is not None:
            pos = self.image_pos_from_event(event)
            if pos is not None and self.image_contains_pos(pos):
                x = int(pos.x() * self.downsample_factor)
                y = int(pos.y() * self.downsample_factor)
                if 0 <= x < self.original_width and 0 <= y < self.original_height:
                    self.pixel_clicked.emit(x, y)
                return True
        if event.button() == Qt.MiddleButton:
            self.is_panning = True
            self.pan_start_pos = event.position().toPoint()
            self.graphics.viewport().setCursor(Qt.ClosedHandCursor)
            if not self.is_syncing:
                self.cursor_changed.emit(Qt.ClosedHandCursor)
            return True
        return False

    def _handle_mouse_move(self, event) -> bool:
        if self.is_panning and self.pan_start_pos is not None:
            current_pos = event.position().toPoint()
            old_scene = self.graphics.mapToScene(self.pan_start_pos)
            new_scene = self.graphics.mapToScene(current_pos)
            old_view = self.view_box.mapSceneToView(old_scene)
            new_view = self.view_box.mapSceneToView(new_scene)
            delta = new_view - old_view
            self.pan_start_pos = current_pos
            self.view_box.translateBy(x=-delta.x(), y=-delta.y())
            if not self.is_syncing:
                self.scroll_changed.emit(0, 0)
            return True

        if self.image_array is not None:
            pos = self.image_pos_from_event(event)
            if pos is not None and self.image_contains_pos(pos):
                x = int(pos.x() * self.downsample_factor)
                y = int(pos.y() * self.downsample_factor)
                value = self.get_pixel_value(x, y)
                self.mouse_moved.emit(x, y, value)
        return False

    def _handle_mouse_release(self, event) -> bool:
        if event.button() == Qt.MiddleButton:
            self.is_panning = False
            self.pan_start_pos = None
            self.graphics.viewport().setCursor(Qt.ArrowCursor)
            if not self.is_syncing:
                self.cursor_changed.emit(Qt.ArrowCursor)
            return True
        return False

    def _update_display(self):
        if self.image_array is None:
            return
        display = self._render_display_array(self.image_array)
        alpha = self._create_alpha_channel(self.image_array)
        if alpha is not None and display.ndim == 3 and display.shape[2] == 3:
            display = np.dstack([display, alpha])
        self.display_array = np.ascontiguousarray(display)
        self.image_item.setImage(self.display_array, autoLevels=False)
        self._update_image_rect()
        self._rebuild_selected_pixel_marker()

    def _render_display_array(self, image_array):
        if self.render_settings is None and image_array.ndim == 3 and image_array.shape[2] == 3:
            if image_array.dtype == np.uint8:
                return image_array
            finite = np.isfinite(image_array)
            if not np.any(finite):
                return np.zeros(image_array.shape, dtype=np.uint8)
            vmin = float(np.nanmin(image_array[finite]))
            vmax = float(np.nanmax(image_array[finite]))
            if vmax <= vmin:
                return np.zeros(image_array.shape, dtype=np.uint8)
            normalized = np.clip((image_array.astype(np.float32) - vmin) / (vmax - vmin), 0.0, 1.0)
            return np.clip(normalized * 255.0, 0, 255).astype(np.uint8)

        config = default_render_config()
        if self.render_settings:
            settings = dict(self.render_settings)
            config.display_mode = settings.get("display_mode", config.display_mode)
            config.gray_band = settings.get("gray_band", config.gray_band)
            config.rgb_bands = tuple(settings.get("rgb_bands", config.rgb_bands))
            config.gamma = settings.get("gamma", config.gamma)
            config.stretch_mode = settings.get("stretch_mode", config.stretch_mode)
            config.percent_clip = tuple(settings.get("percent_clip", config.percent_clip))
            config.std_dev_n = settings.get("std_dev_n", config.std_dev_n)
            config.auto_range = settings.get("auto_range", config.auto_range)
            value_range = settings.get("value_range", config.value_range)
            config.value_range = tuple(value_range)
            config.global_value_range = tuple(value_range) if not config.auto_range else None
            config.colormap_reversed = settings.get("colormap_reversed", self.colormap_reversed)
            config.smooth_display = settings.get("smooth_display", config.smooth_display)
        else:
            config.colormap_name = self.current_colormap
            config.colormap_reversed = self.colormap_reversed
        config.colormap_name = self.current_colormap
        return render_base_rgb(image_array, config, nodata_value=self.nodata_value)

    def _create_alpha_channel(self, image_array):
        arr = image_array
        if arr.ndim == 3:
            if self.render_settings and self.render_settings.get("display_mode") == "RGB":
                bands = [min(max(int(b), 1), arr.shape[2]) - 1 for b in self.render_settings.get("rgb_bands", (1, 2, 3))]
                invalid = np.zeros(arr.shape[:2], dtype=bool)
                for band in bands:
                    band_data = arr[:, :, band]
                    invalid |= ~np.isfinite(band_data)
                    if self.nodata_value is not None:
                        invalid |= band_data == self.nodata_value
            else:
                band = min(max(int((self.render_settings or {}).get("gray_band", 1)), 1), arr.shape[2]) - 1
                band_data = arr[:, :, band]
                invalid = ~np.isfinite(band_data)
                if self.nodata_value is not None:
                    invalid |= band_data == self.nodata_value
        else:
            invalid = ~np.isfinite(arr)
            if self.nodata_value is not None:
                invalid |= arr == self.nodata_value
        if not np.any(invalid):
            return None
        alpha = np.full(arr.shape[:2], 255, dtype=np.uint8)
        alpha[invalid] = 0
        return alpha

    def _update_image_rect(self):
        if self.image_array is None:
            return
        if self.image_world_rect is not None:
            x, y, width, height = self.image_world_rect
            self._image_rect = QRectF(x, y, width, height)
        else:
            self._image_rect = QRectF(0, 0, self.image_array.shape[1], self.image_array.shape[0])
        self.image_item.setRect(self._image_rect)
        self.view_box.setLimits(
            xMin=self._image_rect.left() - self._image_rect.width() * 4,
            xMax=self._image_rect.right() + self._image_rect.width() * 4,
            yMin=self._image_rect.top() - self._image_rect.height() * 4,
            yMax=self._image_rect.bottom() + self._image_rect.height() * 4,
            minXRange=1,
            minYRange=1,
        )

    def _current_scene_rect(self) -> QRectF:
        if self.scene_world_rect is not None:
            x, y, width, height = self.scene_world_rect
            return QRectF(x, y, width, height)
        return QRectF(self._image_rect)

    def _view_to_image_pos(self, view_pos: QPointF):
        if self.image_array is None or self._image_rect.isNull():
            return None
        x = (view_pos.x() - self._image_rect.left()) * self.image_array.shape[1] / max(self._image_rect.width(), 1e-9)
        y = (view_pos.y() - self._image_rect.top()) * self.image_array.shape[0] / max(self._image_rect.height(), 1e-9)
        return QPointF(x, y)

    def image_to_view_point(self, x: float, y: float) -> QPointF:
        if self.image_array is None or self._image_rect.isNull():
            return QPointF(x, y)
        vx = self._image_rect.left() + x * self._image_rect.width() / max(self.image_array.shape[1], 1)
        vy = self._image_rect.top() + y * self._image_rect.height() / max(self.image_array.shape[0], 1)
        return QPointF(vx, vy)

    def _clear_selected_pixel_marker(self):
        for item in self._selected_pixel_items:
            self.view_box.removeItem(item)
        self._selected_pixel_items = []

    def _rebuild_selected_pixel_marker(self):
        self._clear_selected_pixel_marker()
        if self.selected_pixel is None or self.image_array is None:
            return
        x, y = self.selected_pixel
        if not (0 <= x < self.original_width and 0 <= y < self.original_height):
            return
        display_x = (x + 0.5) / max(self.downsample_factor, 1e-9)
        display_y = (y + 0.5) / max(self.downsample_factor, 1e-9)
        center = self.image_to_view_point(display_x, display_y)
        half = 6.0
        outer_pen = QPen(QColor(255, 255, 255), 2)
        outer_pen.setCosmetic(True)
        inner_pen = QPen(QColor(220, 20, 60), 1)
        inner_pen.setCosmetic(True)
        for pen in (outer_pen, inner_pen):
            h_line = QGraphicsLineItem(center.x() - half, center.y(), center.x() + half, center.y())
            v_line = QGraphicsLineItem(center.x(), center.y() - half, center.x(), center.y() + half)
            h_line.setPen(pen)
            v_line.setPen(pen)
            self.view_box.addItem(h_line)
            self.view_box.addItem(v_line)
            self._selected_pixel_items.extend([h_line, v_line])
        circle = QGraphicsEllipseItem(center.x() - 3.5, center.y() - 3.5, 7, 7)
        circle.setPen(inner_pen)
        circle.setBrush(QBrush(Qt.NoBrush))
        self.view_box.addItem(circle)
        self._selected_pixel_items.append(circle)

    def _emit_view_changed(self):
        if self.is_syncing or self._suspend_range_signal:
            return
        self.view_transformed.emit(self.capture_view_state())

    def _on_range_changed(self, *_args):
        self._emit_view_changed()


class ImageViewerSynchronizer:
    def __init__(self, viewers):
        self.viewers = viewers
        self._connect_signals()

    def _connect_signals(self):
        for viewer in self.viewers:
            viewer.view_transformed.connect(self._on_view_transformed)
            viewer.cursor_changed.connect(self._on_cursor_changed)
            viewer.scroll_changed.connect(self._on_scroll_changed)

    def _on_view_transformed(self, transform):
        sender = self.sender()
        for viewer in self.viewers:
            if viewer != sender:
                viewer.sync_transform(transform)

    def _on_cursor_changed(self, cursor):
        sender = self.sender()
        for viewer in self.viewers:
            if viewer != sender:
                viewer.sync_cursor(cursor)

    def _on_scroll_changed(self, h_value, v_value):
        sender = self.sender()
        for viewer in self.viewers:
            if viewer != sender:
                viewer.sync_scroll(h_value, v_value)
