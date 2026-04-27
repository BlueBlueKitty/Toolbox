"""
支持金字塔动态渲染和图层叠加的通用栅格画布。
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QBrush, QPen
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsItem, QGraphicsLineItem, QVBoxLayout, QWidget
import pyqtgraph as pg
from shiboken6 import isValid

from .config import default_raster_render_config, render_raster_rgb
from .layers import LayerManager
from .models import LayerSpec, RasterLayer, RenderRequest, RenderTileResult, ViewportState
from .pipeline import DEFAULT_RENDER_PIPELINE
from .style_auto_selector import DefaultRenderStyleFactory
from .styles import default_display_settings, legacy_config_to_style, style_to_legacy_config

_UNSET = object()


class LayeredRasterCanvas(QWidget):
    pixel_clicked = Signal(int, int)
    canvas_left_clicked = Signal()
    mouse_moved = Signal(int, int, object)
    view_transformed = Signal(object)
    cursor_changed = Signal(object)
    scroll_changed = Signal(int, int)
    files_dropped = Signal(list)

    BASE_LAYER_ID = "base_raster"

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graphics)
        self.view_box = self.graphics.addViewBox(lockAspect=True, enableMouse=False)
        self.view_box.setMenuEnabled(False)
        self.view_box.invertY(True)
        self.view_box.setAspectLocked(True)
        self.view_box.enableAutoRange(x=False, y=False)
        self.image_item = pg.ImageItem(axisOrder="row-major")
        self.view_box.addItem(self.image_item)

        self.layer_manager = LayerManager()
        self.layer_manager.add_layer(LayerSpec(self.BASE_LAYER_ID, "图像", "raster", locked=True), self.image_item)
        self.layer_manager.layer_style_changed.connect(self._on_base_layer_style_changed)
        self.layer_manager.layer_display_changed.connect(self._on_base_layer_display_changed)

        self.image_array = None
        self.source = None
        self.last_render: RenderTileResult | None = None
        self.render_config = default_raster_render_config()
        self.display_array = None
        self.original_width = 0
        self.original_height = 0
        self.downsample_factor = 1.0
        self.current_colormap = "gray"
        self.colormap_reversed = False
        self.current_zoom = 1.0
        self.zoom_factor = 1.75
        self.min_zoom = 0.01
        self.max_zoom = 1000.0
        self.is_panning = False
        self.pan_start_pos = None
        self._pan_start_view_range = None
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
        self._dynamic_source = False
        self._last_request_signature = None
        self._is_refresh_panning = False
        self._is_refresh_zooming = False
        # 缩放/拖动/静止态使用同一渲染预取边距，避免交互停止时因采样窗口变化导致像素大小抖动。
        self._dynamic_render_margin_ratio = 1.10
        self._dynamic_zoom_margin_ratio = 1.10
        self._dynamic_pan_margin_ratio = 1.10
        self._pan_axis_lock_ratio = 3.0
        self._pan_axis_lock_tolerance_px = 6.0
        self._coordinates_are_image_space = False
        self._overlay_items_by_layer: dict[str, list[object]] = {}
        self._synced_pointer_items: list[object] = []
        self._pending_sync_refresh = False
        self._pending_sync_refresh_delay_ms = 80
        self._background_color_override: QColor | None = None
        self._last_scene_request_rect: QRectF | None = None

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(80)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh_view)
        self._zoom_settle_timer = QTimer(self)
        self._zoom_settle_timer.setInterval(140)
        self._zoom_settle_timer.setSingleShot(True)
        self._zoom_settle_timer.timeout.connect(self._on_zoom_settled)

        self.graphics.viewport().installEventFilter(self)
        self.graphics.installEventFilter(self)
        self.graphics.viewport().setMouseTracking(True)
        self.graphics.viewport().setCursor(Qt.ArrowCursor)
        self.setAcceptDrops(True)
        # 统一在 viewport 层处理拖拽，避免 GraphicsView 与 viewport 双方同时接收时
        # 出现 Qt 的 "drag leave received before drag enter" 警告。
        self.graphics.setAcceptDrops(False)
        self.graphics.viewport().setAcceptDrops(True)
        self.view_box.sigRangeChanged.connect(self._on_range_changed)
        self._apply_background_from_palette()

    def set_raster_array(self, image_array, original_size=None, refresh: bool = True):
        self.source = None
        self.last_render = None
        self._dynamic_source = False
        self._coordinates_are_image_space = False
        self.image_array = image_array
        if original_size is not None:
            self.original_width, self.original_height = original_size
            self.downsample_factor = self.original_width / max(image_array.shape[1], 1)
        else:
            self.original_height, self.original_width = image_array.shape[:2]
            self.downsample_factor = 1.0
        self._sync_base_layer_from_array(image_array)
        if refresh:
            self._update_display()

    def set_raster_source(self, source, reset_view: bool = True, refresh: bool = True, nodata_value=_UNSET):
        self.source = source
        self.last_render = None
        self.image_array = None
        self.display_array = None
        self.downsample_factor = 1.0
        self._dynamic_source = True
        self._coordinates_are_image_space = True
        self._last_request_signature = None
        metadata = source.metadata()
        self.original_width = int(metadata.width)
        self.original_height = int(metadata.height)
        self.nodata_value = metadata.nodata if nodata_value is _UNSET else nodata_value
        self.geotransform = metadata.geotransform
        self.projection = metadata.crs_wkt
        self._sync_base_layer_from_source(source, metadata)
        if self.image_world_rect is not None:
            self._image_rect = QRectF(*self.image_world_rect)
        else:
            self._image_rect = QRectF(0, 0, self.original_width, self.original_height)
        limit_rect = self._current_scene_rect()
        if limit_rect.isNull():
            limit_rect = QRectF(self._image_rect)
        margin_x = max(limit_rect.width() * 4, 1)
        margin_y = max(limit_rect.height() * 4, 1)
        min_x_range, min_y_range = self._min_scene_ranges()
        self.view_box.setLimits(
            xMin=limit_rect.left() - margin_x,
            yMin=limit_rect.top() - margin_y,
            xMax=limit_rect.right() + margin_x,
            yMax=limit_rect.bottom() + margin_y,
            minXRange=min_x_range,
            minYRange=min_y_range,
            maxXRange=max(limit_rect.width() + margin_x * 2, 1),
            maxYRange=max(limit_rect.height() + margin_y * 2, 1),
        )
        self._update_zoom_limits()
        if reset_view:
            self.view_box.setRange(
                xRange=(limit_rect.left(), limit_rect.right()),
                yRange=(limit_rect.top(), limit_rect.bottom()),
                padding=0.02,
            )
        if refresh:
            self.refresh_view()

    def set_scene_mapping(self, scene_world_rect=None, image_world_rect=None):
        self.scene_world_rect = scene_world_rect
        self.image_world_rect = image_world_rect
        if self.source is not None:
            if self.image_world_rect is not None:
                self._image_rect = QRectF(*self.image_world_rect)
            limit_rect = self._current_scene_rect()
            if limit_rect.isNull():
                limit_rect = QRectF(self._image_rect)
            min_x_range, min_y_range = self._min_scene_ranges()
            self.view_box.setLimits(
                xMin=limit_rect.left() - limit_rect.width() * 4,
                xMax=limit_rect.right() + limit_rect.width() * 4,
                yMin=limit_rect.top() - limit_rect.height() * 4,
                yMax=limit_rect.bottom() + limit_rect.height() * 4,
                minXRange=min_x_range,
                minYRange=min_y_range,
                maxXRange=max(limit_rect.width() * 9, min_x_range),
                maxYRange=max(limit_rect.height() * 9, min_y_range),
            )
            self._update_zoom_limits()
            self._rebuild_selected_pixel_marker()
        elif self.image_array is not None:
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
            return False
        sync_refresh_delay_ms = None
        if isinstance(state, dict):
            sync_mode = state.get("_sync_interaction")
            if sync_mode == "zoom":
                sync_refresh_delay_ms = 35
            elif sync_mode in {"pan", "idle"}:
                sync_refresh_delay_ms = self._refresh_timer.interval()
        self._pending_sync_refresh = sync_refresh_delay_ms is not None
        self._pending_sync_refresh_delay_ms = sync_refresh_delay_ms or self._refresh_timer.interval()
        self.is_syncing = True
        self._suspend_range_signal = True
        if isinstance(state, dict) and "x_range" in state and "y_range" in state:
            self.view_box.setRange(xRange=state["x_range"], yRange=state["y_range"], padding=0)
        else:
            current = self.capture_view_state()
            x0, x1 = current["x_range"]
            y0, y1 = current["y_range"]
            cx = float(getattr(state, "center_x", current["scene_center_x"]))
            cy = float(getattr(state, "center_y", current["scene_center_y"]))
            scale_x = float(getattr(state, "scale_x", 0.0) or 0.0)
            scale_y = float(getattr(state, "scale_y", 0.0) or 0.0)
            viewport_width = max(float(getattr(state, "viewport_width", 0.0) or 0.0), 1.0)
            viewport_height = max(float(getattr(state, "viewport_height", 0.0) or 0.0), 1.0)
            half_w = max((scale_x * viewport_width) / 2.0, (x1 - x0) / 2.0, 0.5)
            half_h = max((scale_y * viewport_height) / 2.0, (y1 - y0) / 2.0, 0.5)
            self.view_box.setRange(xRange=(cx - half_w, cx + half_w), yRange=(cy - half_h, cy + half_h), padding=0)
        self._suspend_range_signal = False
        self.is_syncing = False
        if self.source is not None:
            if self._pending_sync_refresh:
                self._refresh_timer.start(max(1, int(self._pending_sync_refresh_delay_ms)))
            else:
                self.refresh_view()
        self._pending_sync_refresh = False
        self._pending_sync_refresh_delay_ms = self._refresh_timer.interval()
        return True

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
        self._update_zoom_limits()

    def set_one_to_one(self) -> None:
        state = self.current_view_state()
        cx, cy = state.center_x, state.center_y
        rect = self.graphics.viewport().rect()
        self.view_box.setRange(
            xRange=(cx - rect.width() / 2.0, cx + rect.width() / 2.0),
            yRange=(cy - rect.height() / 2.0, cy + rect.height() / 2.0),
            padding=0,
        )
        self.refresh_view()

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
        self.render_config.colormap_name = colormap_name
        self._sync_base_layer_from_config()
        self._update_display() if self.source is None else self.refresh_view()

    def set_colormap_reversed(self, reversed):
        self.colormap_reversed = reversed
        self.render_config.colormap_reversed = reversed
        self._sync_base_layer_from_config()
        self._update_display() if self.source is None else self.refresh_view()

    def set_render_config(self, render_config) -> None:
        self.render_config = render_config
        self.current_colormap = render_config.colormap_name
        self.colormap_reversed = render_config.colormap_reversed
        self._last_request_signature = None
        self._sync_base_layer_from_config()
        self.refresh_view() if self.source is not None else self._update_display()

    def set_render_settings(self, settings):
        if not isValid(self):
            return
        self.render_settings = settings
        if settings:
            self.colormap_reversed = settings.get("colormap_reversed", False)
        self.render_config = self._render_config_from_state()
        self._last_request_signature = None
        self._sync_base_layer_from_config()
        self.refresh_view() if self.source is not None else self._update_display()

    def prime_render_settings(self, settings):
        """Update render state without repainting; used before swapping data sources."""
        self.render_settings = settings
        if settings:
            self.colormap_reversed = settings.get("colormap_reversed", False)
        self.render_config = self._render_config_from_state()
        self._last_request_signature = None
        self._sync_base_layer_from_config()

    def set_nodata_value(self, nodata_value):
        self.nodata_value = nodata_value
        self._sync_base_layer_nodata(nodata_value)
        self.refresh_view() if self.source is not None else self._update_display()

    def clear_raster(self) -> None:
        """清空当前栅格显示。"""
        self.source = None
        self.last_render = None
        self.image_array = None
        self.display_array = None
        self.original_width = 0
        self.original_height = 0
        self.downsample_factor = 1.0
        self.nodata_value = None
        self.geotransform = None
        self.projection = None
        self.scene_world_rect = None
        self.image_world_rect = None
        self._coordinates_are_image_space = False
        self._dynamic_source = False
        self._last_request_signature = None
        self._last_scene_request_rect = None
        self._image_rect = QRectF(0, 0, 0, 0)
        self.image_item.clear()
        self.clear_selected_pixel()
        self._clear_synced_pointer()

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
        if self.source is not None:
            return self.source.read_pixel(int(x), int(y))
        if self.image_array is None:
            return None
        display_x = int(x / max(self.downsample_factor, 1e-9))
        display_y = int(y / max(self.downsample_factor, 1e-9))
        if 0 <= display_x < self.image_array.shape[1] and 0 <= display_y < self.image_array.shape[0]:
            return self.image_array[display_y, display_x]
        return None

    def read_window_native(self, x: int, y: int, width: int, height: int):
        if self.source is not None:
            return self.source.read_window_native(x, y, width, height)
        if self.image_array is None:
            return None
        return self.image_array[y:y + height, x:x + width]

    def set_layer_visible(self, layer_id: str, visible: bool) -> None:
        self.layer_manager.set_visible(layer_id, visible)

    def set_layer_opacity(self, layer_id: str, opacity: float) -> None:
        self.layer_manager.set_opacity(layer_id, opacity)

    def move_layer(self, layer_id: str, index: int) -> None:
        self.layer_manager.move_layer(layer_id, index)

    def set_layer_blend_mode(self, layer_id: str, blend_mode: str) -> None:
        self.layer_manager.set_blend_mode(layer_id, blend_mode)

    def remove_layer(self, layer_id: str) -> None:
        state = self.layer_manager.remove_layer(layer_id)
        self.clear_overlay_layer(layer_id)
        if state is None:
            return
        item = state.item
        if item is None:
            return
        try:
            if hasattr(item, "scene") and item.scene() is not None:
                item.scene().removeItem(item)
            else:
                self.view_box.removeItem(item)
        except (RuntimeError, ValueError):
            pass

    def set_raster_overlay(self, layer_id: str, rgba_array, bbox=None, name: str | None = None, opacity: float = 1.0):
        item = self._single_image_layer(layer_id, name or layer_id, opacity)
        if rgba_array is None:
            item.clear()
            return
        item.setImage(rgba_array, autoLevels=False)
        if bbox is None:
            item.setRect(QRectF(0, 0, rgba_array.shape[1], rgba_array.shape[0]))
        else:
            item.setRect(QRectF(*bbox))

    def set_vector_overlay(
        self,
        layer_id: str,
        features,
        style,
        selected_ids: set[str] | None = None,
        editable_feature_id: str | None = None,
        active_vertex=None,
        name: str | None = None,
    ) -> None:
        self.clear_overlay_layer(layer_id)
        state = self.layer_manager.layer(layer_id)
        if state is None:
            self.layer_manager.add_layer(LayerSpec(layer_id, name or layer_id, "vector"))
        selected_ids = selected_ids or set()
        items = []
        for feature in features or []:
            feature_id = getattr(feature, "id", "")
            feature_style = style(feature) if callable(style) else style
            overlay = __import__("src.rendering.overlays", fromlist=["PolygonOverlayItem"]).PolygonOverlayItem(
                feature,
                feature_style,
                selected=feature_id in selected_ids,
                editable=feature_id == editable_feature_id,
                active_vertex=active_vertex if feature_id == editable_feature_id else None,
            )
            overlay.path_item.setParentItem(self.view_box.childGroup)
            self.view_box.addItem(overlay.scatter)
            items.extend([overlay.path_item, overlay.scatter])
        self._overlay_items_by_layer[layer_id] = items
        self._sync_overlay_items(layer_id)

    def clear_overlay_layer(self, layer_id: str) -> None:
        for item in self._overlay_items_by_layer.pop(layer_id, []):
            try:
                if hasattr(item, "scene") and item.scene() is not None:
                    item.scene().removeItem(item)
                else:
                    self.view_box.removeItem(item)
            except (RuntimeError, ValueError):
                pass

    def image_pos_from_event(self, event):
        scene_pos = self.graphics.mapToScene(event.position().toPoint())
        view_pos = self.view_box.mapSceneToView(scene_pos)
        return self._view_to_image_pos(view_pos)

    def image_contains_pos(self, point: QPointF) -> bool:
        return 0 <= point.x() < self.original_width and 0 <= point.y() < self.original_height

    def eventFilter(self, obj, event):
        if obj is self.graphics or obj is self.graphics.viewport():
            if event.type() == QEvent.DragEnter:
                if self._accept_drag_event(event):
                    return True
            if event.type() == QEvent.DragMove:
                if self._accept_drag_event(event):
                    return True
            if event.type() == QEvent.DragLeave:
                event.accept()
                return True
            if event.type() == QEvent.Drop:
                if self._handle_drop_event(event):
                    return True
            if obj is self.graphics:
                return super().eventFilter(obj, event)
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

    def changeEvent(self, event):
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange):
            self._apply_background_from_palette()
        super().changeEvent(event)

    def _apply_background_from_palette(self) -> None:
        color = self._resolved_background_color()
        self.graphics.setBackground(color)

    def _resolved_background_color(self) -> QColor:
        if self._background_color_override is not None:
            return QColor(self._background_color_override)
        window_color = self.palette().color(self.backgroundRole())
        is_dark = window_color.lightness() < 128
        return QColor("#000000" if is_dark else "#ffffff")

    def set_background_color(self, rgba: tuple[int, int, int, int] | None) -> None:
        self._background_color_override = None if rgba is None else QColor(*rgba[:4])
        self._apply_background_from_palette()

    def _accept_drag_event(self, event) -> bool:
        mime = event.mimeData()
        if mime is None or not mime.hasUrls():
            return False
        local_paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
        if not local_paths:
            return False
        event.acceptProposedAction()
        return True

    def _handle_drop_event(self, event) -> bool:
        mime = event.mimeData()
        if mime is None or not mime.hasUrls():
            return False
        local_paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
        if not local_paths:
            return False
        self.files_dropped.emit(local_paths)
        event.acceptProposedAction()
        return True

    def wheelEvent(self, event):
        scene_pos = self.graphics.mapToScene(event.position().toPoint())
        anchor = self.view_box.mapSceneToView(scene_pos)
        ((x0, x1), (y0, y1)) = self.view_box.viewRange()
        wheel_steps = max(abs(event.angleDelta().y()) / 120.0, 1.0)
        wheel_factor = self.zoom_factor ** wheel_steps
        if event.angleDelta().y() > 0:
            next_zoom = min(self.current_zoom * wheel_factor, self.max_zoom)
            if next_zoom <= self.current_zoom:
                return
            scale = self.current_zoom / next_zoom
            self.current_zoom = next_zoom
        elif event.angleDelta().y() <= 0:
            next_zoom = max(self.current_zoom / wheel_factor, self.min_zoom)
            if next_zoom >= self.current_zoom:
                return
            scale = self.current_zoom / next_zoom
            self.current_zoom = next_zoom
        else:
            return
        zooming_source = self.source is not None
        if zooming_source:
            # 先标记缩放态，再更新视图范围，确保 rangeChanged 期间不会按“静止态”去调度延迟重绘。
            self._is_refresh_zooming = True
        self.view_box.setRange(
            xRange=(anchor.x() - (anchor.x() - x0) * scale, anchor.x() + (x1 - anchor.x()) * scale),
            yRange=(anchor.y() - (anchor.y() - y0) * scale, anchor.y() + (y1 - anchor.y()) * scale),
            padding=0,
        )
        if zooming_source:
            # 缩放时旧瓦片会先按视图变换被放大/缩小，等定时器刷新时再替换成新的采样网格，
            # 用户就会看到“暂停后像素块突然变大/变小”。这里直接同步刷新当前视图，
            # 让缩放结束时屏幕上看到的就是目标采样结果，而不是旧瓦片的临时缩放预览。
            self._refresh_timer.stop()
            self.refresh_view()
            self._zoom_settle_timer.start()

    def refresh_view(self):
        if self.source is None or not isValid(self):
            self._is_refresh_zooming = False
            return
        request = self.current_render_request()
        if request is None:
            self.last_render = None
            self.image_array = None
            self.display_array = None
            self.image_item.clear()
            self._clear_selected_pixel_marker()
            self._is_refresh_zooming = False
            return
        request.layer_id = self.BASE_LAYER_ID
        signature = self._request_signature(request)
        if signature == self._last_request_signature and self.last_render is not None:
            self._is_refresh_zooming = False
            return
        self._last_request_signature = signature
        render_config = self._render_config_from_state()
        base_state = self.layer_manager.layer(self.BASE_LAYER_ID)
        if base_state is not None and base_state.layer is not None:
            result = DEFAULT_RENDER_PIPELINE.render_source(
                self.source,
                request,
                base_state.layer.render_style,
                base_state.layer.display_settings,
                layer_id=self.BASE_LAYER_ID,
                layer_revision=self._base_layer_revision(),
            )
        else:
            result = self.source.render(request, render_config)
        if base_state is None and self.nodata_value != getattr(self.source.metadata(), "nodata", None):
            rect_x, rect_y, rect_width, rect_height = result.image_rect
            display_rgb = render_raster_rgb(
                result.raw_array,
                render_config,
                nodata_value=self.nodata_value,
                geotransform=self._window_geotransform(rect_x, rect_y),
                projection=self.projection,
                downsample_factor=max(
                    rect_width / max(result.raw_array.shape[1], 1),
                    rect_height / max(result.raw_array.shape[0], 1),
                    1.0,
                ),
            )
            result = RenderTileResult(
                result.raw_array,
                display_rgb,
                result.image_rect,
                result.overview_level,
                result.source_window,
            )
        self.last_render = result
        self.image_array = result.raw_array
        display = result.display_rgb
        alpha = self._create_alpha_channel(result.raw_array)
        if alpha is not None and display.ndim == 3 and display.shape[2] == 3:
            display = np.dstack([display, alpha])
        self.display_array = np.ascontiguousarray(display)
        self.image_item.setImage(self.display_array, autoLevels=False)
        if self.image_world_rect is not None and self._last_scene_request_rect is not None:
            self.image_item.setRect(QRectF(self._last_scene_request_rect))
            self._image_rect = QRectF(*self.image_world_rect)
        else:
            self.image_item.setRect(QRectF(*result.image_rect))
            self._image_rect = QRectF(0, 0, self.original_width, self.original_height)
        self._rebuild_selected_pixel_marker()

    def _request_signature(self, request: RenderRequest) -> tuple:
        # 不再用 int 截断请求范围，避免亚像素缩放被错误去重导致“暂停时跳变”。
        return (
            round(float(request.x), 4),
            round(float(request.y), 4),
            round(float(request.width), 4),
            round(float(request.height), 4),
            int(request.screen_width),
            int(request.screen_height),
            tuple(request.bands or ()),
            self._render_config_signature(),
            self._base_layer_revision(),
        )

    def current_render_request(self) -> RenderRequest:
        if not all(
            isValid(obj) for obj in (self, self.graphics, self.view_box)
        ):
            return None
        ((x0, x1), (y0, y1)) = self.view_box.viewRange()
        try:
            viewport = self.graphics.viewport()
            if viewport is None or not isValid(viewport):
                return None
            view_rect = viewport.rect()
        except RuntimeError:
            return None
        viewport_width = max(int(view_rect.width()), 1)
        viewport_height = max(int(view_rect.height()), 1)
        width = max(float(x1 - x0), 1e-6)
        height = max(float(y1 - y0), 1e-6)
        scale_x = width / viewport_width
        scale_y = height / viewport_height
        if self._is_refresh_zooming:
            margin_ratio = self._dynamic_zoom_margin_ratio
        elif self._is_refresh_panning:
            margin_ratio = self._dynamic_pan_margin_ratio
        else:
            margin_ratio = self._dynamic_render_margin_ratio
        margin_px_x = max(0, int(np.ceil(viewport_width * margin_ratio)))
        margin_px_y = max(0, int(np.ceil(viewport_height * margin_ratio)))
        margin_x = margin_px_x * scale_x
        margin_y = margin_px_y * scale_y
        screen_width = viewport_width + margin_px_x * 2
        screen_height = viewport_height + margin_px_y * 2
        if self.source is not None and self.image_world_rect is not None:
            image_rect = QRectF(*self.image_world_rect)
            visible_rect = QRectF(x0, y0, width, height).intersected(image_rect)
            if visible_rect.isNull() or visible_rect.width() <= 0 or visible_rect.height() <= 0:
                self._last_scene_request_rect = None
                return None
            visible_screen_width = max(1, int(round(visible_rect.width() / max(scale_x, 1e-9))))
            visible_screen_height = max(1, int(round(visible_rect.height() / max(scale_y, 1e-9))))
            margin_px_x = max(0, int(np.ceil(visible_screen_width * margin_ratio)))
            margin_px_y = max(0, int(np.ceil(visible_screen_height * margin_ratio)))
            margin_x = margin_px_x * scale_x
            margin_y = margin_px_y * scale_y
            request_rect = QRectF(
                visible_rect.left() - margin_x,
                visible_rect.top() - margin_y,
                visible_rect.width() + margin_x * 2.0,
                visible_rect.height() + margin_y * 2.0,
            ).intersected(image_rect)
            if request_rect.isNull() or request_rect.width() <= 0 or request_rect.height() <= 0:
                self._last_scene_request_rect = None
                return None
            self._last_scene_request_rect = QRectF(request_rect)
            img_x = (request_rect.left() - image_rect.left()) * self.original_width / max(image_rect.width(), 1e-9)
            img_y = (request_rect.top() - image_rect.top()) * self.original_height / max(image_rect.height(), 1e-9)
            img_width = request_rect.width() * self.original_width / max(image_rect.width(), 1e-9)
            img_height = request_rect.height() * self.original_height / max(image_rect.height(), 1e-9)
            req_screen_width = max(1, int(round(request_rect.width() / max(scale_x, 1e-9))))
            req_screen_height = max(1, int(round(request_rect.height() / max(scale_y, 1e-9))))
            return RenderRequest(
                x=img_x,
                y=img_y,
                width=max(img_width, 1e-6),
                height=max(img_height, 1e-6),
                screen_width=req_screen_width,
                screen_height=req_screen_height,
            )
        self._last_scene_request_rect = None
        return RenderRequest(
            x=x0 - margin_x,
            y=y0 - margin_y,
            width=screen_width * scale_x,
            height=screen_height * scale_y,
            screen_width=screen_width,
            screen_height=screen_height,
        )

    def _on_zoom_settled(self) -> None:
        self._is_refresh_zooming = False
        if self.source is not None:
            self._refresh_timer.stop()
            self.refresh_view()

    def current_view_state(self) -> ViewportState:
        ((x0, x1), (y0, y1)) = self.view_box.viewRange()
        view_rect = self.graphics.viewport().rect()
        return ViewportState(
            center_x=(x0 + x1) / 2.0,
            center_y=(y0 + y1) / 2.0,
            scale_x=max(x1 - x0, 1.0) / max(view_rect.width(), 1),
            scale_y=max(y1 - y0, 1.0) / max(view_rect.height(), 1),
            viewport_width=float(view_rect.width()),
            viewport_height=float(view_rect.height()),
        )

    def _single_image_layer(self, layer_id: str, name: str, opacity: float):
        state = self.layer_manager.layer(layer_id)
        if state is not None and state.item is not None:
            return state.item
        item = pg.ImageItem(axisOrder="row-major")
        self.view_box.addItem(item)
        if state is None:
            self.layer_manager.add_layer(LayerSpec(layer_id, name, "raster_overlay", opacity=opacity), item)
        else:
            self.layer_manager.set_item(layer_id, item)
        return item

    def _sync_overlay_items(self, layer_id: str) -> None:
        state = self.layer_manager.layer(layer_id)
        if state is None:
            return
        for item in self._overlay_items_by_layer.get(layer_id, []):
            if hasattr(item, "setVisible"):
                item.setVisible(state.spec.visible)
            if hasattr(item, "setOpacity"):
                item.setOpacity(state.spec.opacity)
            if hasattr(item, "setZValue"):
                item.setZValue(state.z_order)

    def _handle_mouse_press(self, event) -> bool:
        if event.button() == Qt.LeftButton:
            self.canvas_left_clicked.emit()
        if event.button() == Qt.LeftButton and (self.image_array is not None or self.source is not None):
            pos = self.image_pos_from_event(event)
            if pos is not None and self.image_contains_pos(pos):
                if self.source is not None:
                    self.pixel_clicked.emit(int(pos.x()), int(pos.y()))
                else:
                    x = int(pos.x() * self.downsample_factor)
                    y = int(pos.y() * self.downsample_factor)
                    self.pixel_clicked.emit(x, y)
                return True
        if event.button() == Qt.MiddleButton:
            self.is_panning = True
            self._is_refresh_panning = True
            self._refresh_timer.stop()
            self.pan_start_pos = QPointF(event.position())
            self._pan_start_view_range = self.view_box.viewRange()
            self.graphics.viewport().setCursor(Qt.ClosedHandCursor)
            if not self.is_syncing:
                self.cursor_changed.emit(Qt.ClosedHandCursor)
            return True
        return False

    def _handle_mouse_move(self, event) -> bool:
        if self.is_panning and self.pan_start_pos is not None:
            current_pos = QPointF(event.position())
            start_range = self._pan_start_view_range or self.view_box.viewRange()
            (x0, x1), (y0, y1) = start_range
            view_rect = self.graphics.viewport().rect()
            scale_x = (x1 - x0) / max(view_rect.width(), 1)
            scale_y = (y1 - y0) / max(view_rect.height(), 1)
            pixel_dx, pixel_dy = self._stabilized_pan_delta(current_pos.x() - self.pan_start_pos.x(), current_pos.y() - self.pan_start_pos.y())
            dx = pixel_dx * scale_x
            dy = pixel_dy * scale_y
            self.view_box.setRange(
                xRange=(x0 - dx, x1 - dx),
                yRange=(y0 - dy, y1 - dy),
                padding=0,
            )
            pos = self.image_pos_from_event(event)
            if pos is not None and self.image_contains_pos(pos):
                if self.source is not None:
                    x, y = int(pos.x()), int(pos.y())
                else:
                    x = int(pos.x() * self.downsample_factor)
                    y = int(pos.y() * self.downsample_factor)
                self._emit_mouse_moved(x, y, self.get_pixel_value(x, y), event)
            if not self.is_syncing:
                self.scroll_changed.emit(0, 0)
            return True
        pos = self.image_pos_from_event(event)
        if pos is not None and self.image_contains_pos(pos):
            if self.source is not None:
                x, y = int(pos.x()), int(pos.y())
            else:
                x = int(pos.x() * self.downsample_factor)
                y = int(pos.y() * self.downsample_factor)
            self._emit_mouse_moved(x, y, self.get_pixel_value(x, y), event)
        return False

    def _handle_mouse_release(self, event) -> bool:
        if event.button() == Qt.MiddleButton:
            self.is_panning = False
            was_refresh_panning = self._is_refresh_panning
            self._is_refresh_panning = False
            self.pan_start_pos = None
            self._pan_start_view_range = None
            self.graphics.viewport().setCursor(Qt.ArrowCursor)
            if not self.is_syncing:
                self.cursor_changed.emit(Qt.ArrowCursor)
            if self.source is not None and was_refresh_panning:
                # 避免在 mouse release 事件里同步重绘阻塞后续 move 事件，导致跨窗十字丝“跟手慢半拍”。
                self._refresh_timer.start(1)
            return True
        return False

    def _stabilized_pan_delta(self, dx: float, dy: float) -> tuple[float, float]:
        abs_dx = abs(dx)
        abs_dy = abs(dy)
        if (
            abs_dx >= self._pan_axis_lock_ratio * max(abs_dy, 1e-9)
            and abs_dy <= self._pan_axis_lock_tolerance_px
        ):
            return float(round(dx)), 0.0
        if (
            abs_dy >= self._pan_axis_lock_ratio * max(abs_dx, 1e-9)
            and abs_dx <= self._pan_axis_lock_tolerance_px
        ):
            return 0.0, float(round(dy))
        return float(round(dx)), float(round(dy))

    def _emit_mouse_moved(self, x: int, y: int, value, _event=None) -> None:
        self.mouse_moved.emit(x, y, value)

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
        return render_raster_rgb(
            image_array,
            self._render_config_from_state(),
            nodata_value=self.nodata_value,
            geotransform=self.geotransform,
            projection=self.projection,
            downsample_factor=self.downsample_factor,
        )

    def _window_geotransform(self, x0: float, y0: float):
        gt = self.geotransform
        if gt is None:
            return None
        return (
            gt[0] + x0 * gt[1] + y0 * gt[2],
            gt[1],
            gt[2],
            gt[3] + x0 * gt[4] + y0 * gt[5],
            gt[4],
            gt[5],
        )

    def _render_config_from_state(self):
        config = replace(self.render_config) if self.render_config is not None else default_raster_render_config()
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
            if config.auto_range and config.stretch_mode != "直方图均衡化":
                config.global_value_range = tuple(value_range)
            else:
                config.global_value_range = None
            config.colormap_reversed = settings.get("colormap_reversed", self.colormap_reversed)
            config.smooth_display = settings.get("smooth_display", config.smooth_display)
        else:
            config.colormap_name = self.current_colormap
            config.colormap_reversed = self.colormap_reversed
        config.colormap_name = self.current_colormap
        return config

    def _sync_base_layer_from_source(self, source, metadata) -> None:
        current_config = self._render_config_from_state()
        auto_style = DefaultRenderStyleFactory.create(metadata)
        if current_config is None:
            render_style = auto_style
        else:
            current_style = legacy_config_to_style(current_config, metadata)
            if self._should_prefer_auto_style(current_style, auto_style, metadata):
                render_style = auto_style
            else:
                render_style = current_style
        display_settings = DefaultRenderStyleFactory.create_display_settings(metadata)
        if self.nodata_value is not None and display_settings.nodata_policy.value != self.nodata_value:
            display_settings = replace(
                display_settings,
                nodata_policy=replace(display_settings.nodata_policy, value=self.nodata_value, use_source_nodata=False),
            )
        layer = RasterLayer(
            id=self.BASE_LAYER_ID,
            name="图像",
            source=source,
            metadata=metadata,
            render_style=render_style,
            display_settings=display_settings,
            visible=True,
            selected=self.layer_manager.active_layer_id() == self.BASE_LAYER_ID,
            locked=True,
        )
        self.layer_manager.add_raster_layer(layer, item=self.image_item)
        self.render_config = style_to_legacy_config(render_style, display_settings)
        self.current_colormap = self.render_config.colormap_name
        self.colormap_reversed = self.render_config.colormap_reversed
        self.layer_manager.set_active_layer(self.BASE_LAYER_ID)

    def _sync_base_layer_from_array(self, image_array) -> None:
        metadata = self._memory_metadata_for_array(image_array)
        current_style = legacy_config_to_style(self._render_config_from_state(), metadata)
        auto_style = DefaultRenderStyleFactory.create(metadata)
        render_style = auto_style if self._should_prefer_auto_style(current_style, auto_style, metadata) else current_style
        display_settings = default_display_settings(nodata_value=self.nodata_value)
        layer = RasterLayer(
            id=self.BASE_LAYER_ID,
            name="图像",
            source=None,
            metadata=metadata,
            render_style=render_style,
            display_settings=display_settings,
            visible=True,
            selected=self.layer_manager.active_layer_id() == self.BASE_LAYER_ID,
            locked=True,
        )
        self.layer_manager.add_raster_layer(layer, item=self.image_item)
        self.layer_manager.set_active_layer(self.BASE_LAYER_ID)

    def _sync_base_layer_from_config(self) -> None:
        state = self.layer_manager.layer(self.BASE_LAYER_ID)
        if state is None or state.layer is None:
            return
        state.layer.render_style = legacy_config_to_style(self._render_config_from_state(), state.layer.metadata)
        state.layer.revision += 1
        self.layer_manager.layer_style_changed.emit(self.BASE_LAYER_ID)

    def _should_prefer_auto_style(self, current_style, auto_style, metadata) -> bool:
        current_renderer = getattr(current_style, "renderer_type", "")
        auto_renderer = getattr(auto_style, "renderer_type", "")
        if auto_renderer == current_renderer:
            return False
        if auto_renderer in {"multiband", "paletted"} and current_renderer in {"singleband_gray", "singleband_pseudocolor"}:
            band_indices = tuple(getattr(current_style, "band_indices", ()) or ())
            color_ramp = getattr(getattr(current_style, "color_ramp", None), "name", "gray")
            if band_indices in {(), (1,)} and str(color_ramp or "gray") == "gray":
                return True
        if auto_renderer == "singleband_pseudocolor" and current_renderer == "singleband_gray":
            return True
        if int(getattr(metadata, "band_count", 1) or 1) >= 3 and auto_renderer == "multiband" and current_renderer != "multiband":
            return True
        return False

    def _sync_base_layer_nodata(self, nodata_value) -> None:
        state = self.layer_manager.layer(self.BASE_LAYER_ID)
        if state is None or state.layer is None:
            return
        source_nodata = getattr(state.layer.metadata, "nodata", None)
        use_source_nodata = self._nodata_values_equal(nodata_value, source_nodata)
        display_settings = replace(
            state.layer.display_settings,
            nodata_policy=replace(
                state.layer.display_settings.nodata_policy,
                value=nodata_value,
                use_source_nodata=use_source_nodata,
            ),
        )
        self.layer_manager.set_display_settings(self.BASE_LAYER_ID, display_settings)

    def _nodata_values_equal(self, left, right) -> bool:
        if left is right:
            return True
        if left is None or right is None:
            return left is None and right is None
        try:
            if np.isnan(left) and np.isnan(right):
                return True
        except Exception:
            pass
        return left == right

    def _memory_metadata_for_array(self, image_array):
        from .models import ImageSourceMetadata

        return ImageSourceMetadata(
            id=self.BASE_LAYER_ID,
            path="",
            path_mode="memory",
            width=int(image_array.shape[1]),
            height=int(image_array.shape[0]),
            band_count=1 if image_array.ndim == 2 else int(image_array.shape[2]),
            dtype=str(image_array.dtype),
            nodata=self.nodata_value,
            crs_wkt=self.projection,
            geotransform=self.geotransform,
            resolution=None,
            has_georef=bool(self.geotransform or self.projection),
            custom_properties={"source_kind": "memory"},
        )

    def _base_layer_revision(self) -> int:
        state = self.layer_manager.layer(self.BASE_LAYER_ID)
        if state is None or state.layer is None:
            return 0
        return int(state.layer.revision)

    def _on_base_layer_style_changed(self, layer_id: str) -> None:
        if layer_id != self.BASE_LAYER_ID:
            return
        state = self.layer_manager.layer(layer_id)
        if state is None or state.layer is None:
            return
        self.render_config = style_to_legacy_config(state.layer.render_style, state.layer.display_settings)
        self.current_colormap = self.render_config.colormap_name
        self.colormap_reversed = self.render_config.colormap_reversed
        self._last_request_signature = None
        if self.source is not None:
            self.refresh_view()
        elif self.image_array is not None:
            self._update_display()

    def _on_base_layer_display_changed(self, layer_id: str) -> None:
        if layer_id != self.BASE_LAYER_ID:
            return
        state = self.layer_manager.layer(layer_id)
        if state is None or state.layer is None:
            return
        self.nodata_value = state.layer.display_settings.nodata_policy.value
        self.set_background_color(state.layer.display_settings.background_color)
        self._last_request_signature = None
        if self.source is not None:
            self.refresh_view()
        elif self.image_array is not None:
            self._update_display()

    def _render_config_signature(self):
        config = self._render_config_from_state()
        return (
            config.display_mode,
            config.gray_band,
            config.rgb_bands,
            config.gamma,
            config.stretch_mode,
            config.percent_clip,
            config.std_dev_n,
            config.auto_range,
            config.value_range,
            config.colormap_name,
            config.colormap_reversed,
        )

    def _create_alpha_channel(self, image_array):
        arr = image_array
        def _invalid(data):
            mask = ~np.isfinite(data)
            if self.nodata_value is not None:
                try:
                    if np.isnan(self.nodata_value):
                        mask |= np.isnan(data)
                    else:
                        mask |= data == self.nodata_value
                except TypeError:
                    mask |= data == self.nodata_value
            return mask

        if arr.ndim == 3:
            band = min(max(int((self.render_settings or {}).get("gray_band", 1)), 1), arr.shape[2]) - 1
            band_data = arr[:, :, band]
            invalid = _invalid(band_data)
        else:
            invalid = _invalid(arr)
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
        limit_rect = self._current_scene_rect()
        if limit_rect.isNull():
            limit_rect = QRectF(self._image_rect)
        min_x_range, min_y_range = self._min_scene_ranges()
        self.view_box.setLimits(
            xMin=limit_rect.left() - limit_rect.width() * 4,
            xMax=limit_rect.right() + limit_rect.width() * 4,
            yMin=limit_rect.top() - limit_rect.height() * 4,
            yMax=limit_rect.bottom() + limit_rect.height() * 4,
            minXRange=min_x_range,
            minYRange=min_y_range,
        )
        self._update_zoom_limits()

    def _current_scene_rect(self) -> QRectF:
        if self.scene_world_rect is not None:
            x, y, width, height = self.scene_world_rect
            return QRectF(x, y, width, height)
        return QRectF(self._image_rect)

    def _min_scene_ranges(self) -> tuple[float, float]:
        width_ref = float(self.original_width or (self.image_array.shape[1] if self.image_array is not None else 1))
        height_ref = float(self.original_height or (self.image_array.shape[0] if self.image_array is not None else 1))
        if self._image_rect.isNull():
            return 1e-6, 1e-6
        pixel_w = abs(self._image_rect.width()) / max(width_ref, 1.0)
        pixel_h = abs(self._image_rect.height()) / max(height_ref, 1.0)
        return max(pixel_w, 1e-9), max(pixel_h, 1e-9)

    def _update_zoom_limits(self) -> None:
        scene_rect = self._current_scene_rect()
        if scene_rect.isNull():
            self.max_zoom = 1000.0
            return
        min_x_range, min_y_range = self._min_scene_ranges()
        scene_w = max(abs(scene_rect.width()), min_x_range)
        scene_h = max(abs(scene_rect.height()), min_y_range)
        needed_x = scene_w / max(min_x_range, 1e-9)
        needed_y = scene_h / max(min_y_range, 1e-9)
        needed_zoom = max(needed_x, needed_y)
        self.max_zoom = max(1000.0, float(needed_zoom) * 4.0)

    def _view_to_image_pos(self, view_pos: QPointF):
        if self._coordinates_are_image_space:
            if self.image_world_rect is None:
                return QPointF(view_pos.x(), view_pos.y())
            x = (view_pos.x() - self._image_rect.left()) * self.original_width / max(self._image_rect.width(), 1e-9)
            y = (view_pos.y() - self._image_rect.top()) * self.original_height / max(self._image_rect.height(), 1e-9)
            return QPointF(x, y)
        if self.image_array is None or self._image_rect.isNull():
            return None
        x = (view_pos.x() - self._image_rect.left()) * self.image_array.shape[1] / max(self._image_rect.width(), 1e-9)
        y = (view_pos.y() - self._image_rect.top()) * self.image_array.shape[0] / max(self._image_rect.height(), 1e-9)
        return QPointF(x, y)

    def image_to_view_point(self, x: float, y: float) -> QPointF:
        if self._coordinates_are_image_space:
            if self.image_world_rect is None:
                return QPointF(x, y)
            vx = self._image_rect.left() + x * self._image_rect.width() / max(float(self.original_width), 1.0)
            vy = self._image_rect.top() + y * self._image_rect.height() / max(float(self.original_height), 1.0)
            return QPointF(vx, vy)
        if self.image_array is None or self._image_rect.isNull():
            return QPointF(x, y)
        vx = self._image_rect.left() + x * self._image_rect.width() / max(self.image_array.shape[1], 1)
        vy = self._image_rect.top() + y * self._image_rect.height() / max(self.image_array.shape[0], 1)
        return QPointF(vx, vy)

    def sync_pointer_coordinates(self, x: float, y: float) -> tuple[float, float] | None:
        point = self.image_to_view_point(float(x) + 0.5, float(y) + 0.5)
        return float(point.x()), float(point.y())

    def image_coordinates_from_sync_point(self, sync_x: float, sync_y: float) -> tuple[int, int] | None:
        pos = self._view_to_image_pos(QPointF(float(sync_x), float(sync_y)))
        if pos is None or not self.image_contains_pos(pos):
            return None
        return int(pos.x()), int(pos.y())

    def _clear_selected_pixel_marker(self):
        for item in self._selected_pixel_items:
            self.view_box.removeItem(item)
        self._selected_pixel_items = []

    def _rebuild_selected_pixel_marker(self):
        self._clear_selected_pixel_marker()
        if self.selected_pixel is None or (self.image_array is None and self.source is None):
            return
        x, y = self.selected_pixel
        if not (0 <= x < self.original_width and 0 <= y < self.original_height):
            return
        center = self.image_to_view_point(x + 0.5, y + 0.5)
        half = 6.0
        outer_pen = QPen(QColor(255, 255, 255), 2)
        outer_pen.setCosmetic(True)
        inner_pen = QPen(QColor(220, 20, 60), 1)
        inner_pen.setCosmetic(True)
        for pen in (outer_pen, inner_pen):
            h_line = QGraphicsLineItem(-half, 0, half, 0)
            v_line = QGraphicsLineItem(0, -half, 0, half)
            h_line.setPen(pen)
            v_line.setPen(pen)
            h_line.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
            v_line.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
            h_line.setPos(center)
            v_line.setPos(center)
            self.view_box.addItem(h_line)
            self.view_box.addItem(v_line)
            self._selected_pixel_items.extend([h_line, v_line])
        circle = QGraphicsEllipseItem(-3.5, -3.5, 7, 7)
        circle.setPen(inner_pen)
        circle.setBrush(QBrush(Qt.NoBrush))
        circle.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        circle.setPos(center)
        self.view_box.addItem(circle)
        self._selected_pixel_items.append(circle)

    def _emit_view_changed(self):
        if self.is_syncing or self._suspend_range_signal:
            return
        self.view_transformed.emit(self.capture_view_state())

    def update_synced_pointer(self, x: float | None, y: float | None, visible: bool = True) -> None:
        if not visible or x is None or y is None:
            self._clear_synced_pointer()
            return
        if not (0 <= float(x) < max(1, int(self.original_width)) and 0 <= float(y) < max(1, int(self.original_height))):
            self._clear_synced_pointer()
            return
        center = self.image_to_view_point(float(x) + 0.5, float(y) + 0.5)
        if len(self._synced_pointer_items) != 4:
            self._clear_synced_pointer()
            outer_pen = QPen(QColor(255, 255, 255, 230), 3.8)
            outer_pen.setCosmetic(True)
            inner_pen = QPen(QColor(0, 245, 255, 255), 1.8)
            inner_pen.setCosmetic(True)
            h_outer = QGraphicsLineItem(-12.0, 0.0, 12.0, 0.0)
            v_outer = QGraphicsLineItem(0.0, -12.0, 0.0, 12.0)
            h_inner = QGraphicsLineItem(-12.0, 0.0, 12.0, 0.0)
            v_inner = QGraphicsLineItem(0.0, -12.0, 0.0, 12.0)
            for item, pen in (
                (h_outer, outer_pen),
                (v_outer, outer_pen),
                (h_inner, inner_pen),
                (v_inner, inner_pen),
            ):
                item.setPen(pen)
                item.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
                self.view_box.addItem(item)
            self._synced_pointer_items = [h_outer, v_outer, h_inner, v_inner]
        for item in self._synced_pointer_items:
            item.setPos(center)
            item.setVisible(True)

    def _clear_synced_pointer(self) -> None:
        for item in self._synced_pointer_items:
            try:
                self.view_box.removeItem(item)
            except Exception:
                pass
        self._synced_pointer_items = []

    def _on_range_changed(self, *_args):
        self._update_current_zoom_from_view_range()
        if self._suspend_range_signal:
            return
        if self.source is not None:
            if self._is_refresh_panning:
                self._emit_view_changed()
                return
            if self._is_refresh_zooming:
                if not self._refresh_timer.isActive():
                    self._refresh_timer.start(35)
            else:
                self._refresh_timer.start()
        self._emit_view_changed()

    def _update_current_zoom_from_view_range(self) -> None:
        if self.original_width <= 0 or self.original_height <= 0:
            self.current_zoom = 1.0
            return
        ((x0, x1), (y0, y1)) = self.view_box.viewRange()
        if self._coordinates_are_image_space and not self._image_rect.isNull():
            image_rect = QRectF(self._image_rect)
            view_rect = QRectF(float(x0), float(y0), float(x1 - x0), float(y1 - y0))
            mapped_rect = view_rect.intersected(image_rect)
            if mapped_rect.isNull() or mapped_rect.width() <= 0 or mapped_rect.height() <= 0:
                mapped_rect = view_rect
            view_width = max(
                float(mapped_rect.width()) * float(self.original_width) / max(abs(float(image_rect.width())), 1e-9),
                1e-6,
            )
            view_height = max(
                float(mapped_rect.height()) * float(self.original_height) / max(abs(float(image_rect.height())), 1e-9),
                1e-6,
            )
        else:
            view_width = max(float(x1 - x0), 1e-6)
            view_height = max(float(y1 - y0), 1e-6)
        image_diag = float(np.hypot(self.original_width, self.original_height))
        view_diag = float(np.hypot(view_width, view_height))
        if view_diag <= 0:
            return
        self.current_zoom = max(self.min_zoom, min(self.max_zoom, image_diag / view_diag))


class RasterCanvasSynchronizer:
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
