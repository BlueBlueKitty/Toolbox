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

from src.segmentation.image_sources import BaseImageSource, RenderRequest
from src.segmentation.models import AnnotationObject, RenderTileResult, ViewportState
from .annotation_overlay_items import PolygonOverlayItem, PreviewMaskItem


@dataclass
class CanvasMousePayload:
    x: float
    y: float
    button: int
    buttons: int
    modifiers: int
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
        self.view_box.invertY(False)
        self.view_box.setAspectLocked(False)
        self.view_box.setMouseMode(pg.ViewBox.PanMode)
        self.image_item = pg.ImageItem(axisOrder="row-major")
        self.preview_item = PreviewMaskItem()
        self.view_box.addItem(self.image_item)
        self.view_box.addItem(self.preview_item)

        self.source: BaseImageSource | None = None
        self.last_render: RenderTileResult | None = None
        self._overlay_items: dict[str, PolygonOverlayItem] = {}

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(50)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh_view)
        self.view_box.sigRangeChanged.connect(lambda *_: self._refresh_timer.start())

        self.graphics.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.graphics.viewport():
            if event.type() == QEvent.MouseButtonPress:
                self.mouse_pressed.emit(self._payload_from_event(event))
            elif event.type() == QEvent.MouseButtonDblClick:
                self.mouse_pressed.emit(self._payload_from_event(event, double_click=True))
            elif event.type() == QEvent.MouseMove:
                self.mouse_moved.emit(self._payload_from_event(event))
            elif event.type() == QEvent.MouseButtonRelease:
                self.mouse_released.emit(self._payload_from_event(event))
        return super().eventFilter(obj, event)

    def _payload_from_event(self, event, double_click: bool = False) -> CanvasMousePayload:
        scene_pos = self.graphics.mapToScene(event.position().toPoint())
        image_pos = self.view_box.mapSceneToView(scene_pos)
        return CanvasMousePayload(
            x=float(image_pos.x()),
            y=float(image_pos.y()),
            button=int(event.button()),
            buttons=int(event.buttons()),
            modifiers=int(event.modifiers()),
            double_click=double_click,
        )

    def set_image_source(self, source: BaseImageSource) -> None:
        self.source = source
        metadata = source.metadata()
        self.view_box.setLimits(xMin=0, yMin=0, xMax=metadata.width, yMax=metadata.height)
        self.view_box.setRange(xRange=(0, metadata.width), yRange=(0, metadata.height), padding=0)
        self.refresh_view()

    def refresh_view(self) -> None:
        if self.source is None:
            return
        request = self.current_render_request()
        result = self.source.render(request)
        self.last_render = result
        image = self._normalize_for_display(result.array)
        self.image_item.setImage(image, autoLevels=False)
        self.image_item.setRect(QRectF(*result.image_rect))
        self.view_state_changed.emit(self.current_view_state())

    def _normalize_for_display(self, array: np.ndarray) -> np.ndarray:
        if array.dtype == np.uint8:
            return array
        if array.ndim == 3:
            clipped = np.clip(array, 0, 255)
            return clipped.astype(np.uint8)
        arr = array.astype(np.float32)
        min_val = float(np.nanmin(arr))
        max_val = float(np.nanmax(arr))
        if max_val - min_val < 1e-6:
            return np.zeros_like(arr, dtype=np.uint8)
        return (((arr - min_val) / (max_val - min_val)) * 255).astype(np.uint8)

    def current_render_request(self) -> RenderRequest:
        ((x0, x1), (y0, y1)) = self.view_box.viewRange()
        view_rect = self.graphics.viewport().rect()
        return RenderRequest(
            x=x0,
            y=y0,
            width=max(x1 - x0, 1),
            height=max(y1 - y0, 1),
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
        self.view_box.setRange(xRange=(0, meta.width), yRange=(0, meta.height), padding=0)
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

    def update_annotations(self, annotations, label_lookup, selected_id: str | None = None) -> None:
        for overlay in self._overlay_items.values():
            self.view_box.removeItem(overlay.scatter)
            if overlay.path_item.scene() is not None:
                overlay.path_item.scene().removeItem(overlay.path_item)
        self._overlay_items = {}
        for annotation in annotations:
            label = label_lookup.get(annotation.label_id)
            if label is None:
                continue
            overlay = PolygonOverlayItem(annotation, label, selected=annotation.id == selected_id)
            overlay.path_item.setParentItem(self.view_box.childGroup)
            self.view_box.addItem(overlay.scatter)
            self._overlay_items[annotation.id] = overlay

    def update_preview_mask(self, mask: np.ndarray | None, bbox: tuple[int, int, int, int] | None) -> None:
        self.preview_item.update_mask(mask, bbox)

    def viewport_image(self) -> np.ndarray | None:
        if self.last_render is None:
            return None
        return self.last_render.array
