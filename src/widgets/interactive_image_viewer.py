"""
交互式图像查看器组件，支持绘制矩形和折线。
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
)

from src.rendering.canvas import LayeredRasterCanvas


class InteractiveImageViewer(LayeredRasterCanvas):
    """
    在通用 pyqtgraph 图像查看器基础上增加矩形和折线交互。

    对外保留原来的像素坐标语义：current_rect、polyline_points 以及采样接口
    都使用图像像素坐标；绘制到屏幕时再转换到 ViewBox 坐标。
    """

    MODE_NONE = 0
    MODE_RECT = 1
    MODE_POLYLINE = 2

    rect_drawn = Signal(object)  # QRectF
    polyline_drawn = Signal(list)  # [(x, y), ...]
    polyline_hover = Signal(int)  # 折线路径上的点索引

    def __init__(self, parent=None):
        super().__init__(parent)

        self.draw_mode = self.MODE_NONE

        self.rect_start = None
        self.rect_item = None
        self.current_rect = None

        self.polyline_points = []
        self.polyline_item = None
        self.polyline_markers = []
        self.hover_marker = None
        self.polyline_completed = False

        self.rect_pen = QPen(QColor(255, 0, 0), 3, Qt.SolidLine)
        self.rect_pen.setCosmetic(True)

        self.polyline_pen = QPen(QColor(0, 255, 0), 3, Qt.SolidLine)
        self.polyline_pen.setCosmetic(True)

        self.marker_brush = QBrush(QColor(0, 255, 0))
        self.hover_brush = QBrush(QColor(255, 255, 0))

    def set_raster_array(self, image_array, original_size=None):
        self.clear_rect()
        self.clear_polyline()
        super().set_raster_array(image_array, original_size=original_size)

    def set_raster_source(self, source, reset_view: bool = True):
        self.clear_rect()
        self.clear_polyline()
        super().set_raster_source(source, reset_view=reset_view)

    def set_scene_mapping(self, scene_world_rect=None, image_world_rect=None):
        super().set_scene_mapping(scene_world_rect, image_world_rect)
        self._refresh_overlay_items()

    def set_draw_mode(self, mode):
        """设置绘制模式。"""
        self.draw_mode = mode
        if mode == self.MODE_RECT:
            self.clear_polyline()
        elif mode == self.MODE_POLYLINE:
            self.clear_rect()

    def clear_rect(self):
        """清除矩形。"""
        self._remove_view_item(self.rect_item)
        self.rect_item = None
        self.rect_start = None
        self.current_rect = None

    def clear_polyline(self):
        """清除折线。"""
        self._remove_view_item(self.polyline_item)
        self.polyline_item = None

        for marker in self.polyline_markers:
            self._remove_view_item(marker)
        self.polyline_markers.clear()

        self._remove_view_item(self.hover_marker)
        self.hover_marker = None

        self.polyline_points.clear()
        self.polyline_completed = False

    def _handle_mouse_press(self, event) -> bool:
        if event.button() == Qt.LeftButton and (self.image_array is not None or self.source is not None):
            image_pos = self.image_pos_from_event(event)
            if image_pos is not None and self.image_contains_pos(image_pos):
                if self.draw_mode == self.MODE_RECT:
                    self.clear_rect()
                    self.rect_start = image_pos
                    return True

                if self.draw_mode == self.MODE_POLYLINE:
                    x = int(image_pos.x())
                    y = int(image_pos.y())
                    if self.polyline_completed:
                        self.clear_polyline()
                    if len(self.polyline_points) == 0:
                        self.clear_polyline()
                    self.polyline_points.append((x, y))
                    self.polyline_completed = False
                    self._update_polyline()
                    return True

        return super()._handle_mouse_press(event)

    def _handle_mouse_move(self, event) -> bool:
        if self.image_array is None and self.source is None:
            return super()._handle_mouse_move(event)

        image_pos = self.image_pos_from_event(event)
        inside_image = image_pos is not None and self.image_contains_pos(image_pos)

        if self.draw_mode == self.MODE_RECT and self.rect_start is not None and inside_image:
            self._update_rect(image_pos)
            return True

        if (
            self.draw_mode == self.MODE_POLYLINE
            and self.polyline_points
            and not self.polyline_completed
            and inside_image
        ):
            self._update_polyline_preview(int(image_pos.x()), int(image_pos.y()))
            return True

        if self.draw_mode == self.MODE_NONE and self.polyline_completed and self.polyline_points:
            if inside_image:
                self._update_polyline_hover(int(image_pos.x()), int(image_pos.y()))
            else:
                self._hide_hover_marker()

        return super()._handle_mouse_move(event)

    def _handle_mouse_release(self, event) -> bool:
        if event.button() == Qt.LeftButton and self.draw_mode == self.MODE_RECT:
            if self.rect_start is not None:
                image_pos = self.image_pos_from_event(event)
                if image_pos is not None and self.image_contains_pos(image_pos):
                    self._update_rect(image_pos)
                    if self.current_rect:
                        self.rect_drawn.emit(self.current_rect)
                self.rect_start = None
                return True

        if event.button() == Qt.RightButton and self.draw_mode == self.MODE_POLYLINE:
            if len(self.polyline_points) > 1 and not self.polyline_completed:
                self.polyline_completed = True
                self._update_polyline()
                self.polyline_drawn.emit(self.polyline_points.copy())
                return True

        return super()._handle_mouse_release(event)

    def _update_rect(self, end_pos):
        """更新矩形显示。"""
        if self.rect_start is None or (self.image_array is None and self.source is None):
            return

        x1 = min(self.rect_start.x(), end_pos.x())
        y1 = min(self.rect_start.y(), end_pos.y())
        x2 = max(self.rect_start.x(), end_pos.x())
        y2 = max(self.rect_start.y(), end_pos.y())

        x1 = max(0.0, min(x1, self.original_width - 1))
        y1 = max(0.0, min(y1, self.original_height - 1))
        x2 = max(0.0, min(x2, self.original_width))
        y2 = max(0.0, min(y2, self.original_height))

        self.current_rect = QRectF(x1, y1, x2 - x1, y2 - y1)
        self._update_rect_item()

    def _update_rect_item(self):
        if self.current_rect is None:
            return
        rect = self._image_rect_to_view_rect(self.current_rect)
        if self.rect_item is None:
            self.rect_item = QGraphicsRectItem(rect)
            self.rect_item.setPen(self.rect_pen)
            self.rect_item.setBrush(QBrush(QColor(255, 0, 0, 30)))
            self.view_box.addItem(self.rect_item)
        else:
            self.rect_item.setRect(rect)

    def _update_polyline(self, temp_point=None):
        """更新折线显示。"""
        if not self.polyline_points:
            return

        self._remove_view_item(self.polyline_item)
        self.polyline_item = None
        for marker in self.polyline_markers:
            self._remove_view_item(marker)
        self.polyline_markers.clear()

        path = self._build_polyline_path(self.polyline_points)
        self.polyline_item = QGraphicsPathItem(path)
        self.polyline_item.setPen(self.polyline_pen)
        self.view_box.addItem(self.polyline_item)

        if temp_point is not None:
            last_point = self.polyline_points[-1]
            preview_path = self._build_polyline_path([last_point, temp_point])
            preview_item = QGraphicsPathItem(preview_path)
            temp_pen = QPen(self.polyline_pen)
            temp_pen.setStyle(Qt.DashLine)
            temp_pen.setCosmetic(True)
            preview_item.setPen(temp_pen)
            self.view_box.addItem(preview_item)
            self.polyline_markers.append(preview_item)

        if self.polyline_completed:
            for x, y in self.polyline_points:
                marker = self._create_marker(x, y, self.marker_brush, QColor(0, 255, 0), 0)
                self.view_box.addItem(marker)
                self.polyline_markers.append(marker)

    def _update_polyline_preview(self, x, y):
        """更新折线预览。"""
        self._update_polyline(temp_point=(x, y))

    def _update_polyline_hover(self, x, y):
        path_points, _ = self.get_polyline_path_values()
        if not path_points:
            self._hide_hover_marker()
            return

        nearest_idx = -1
        min_dist = float("inf")
        for idx, (px, py) in enumerate(path_points):
            dist = math.hypot(x - px, y - py)
            if dist < min_dist:
                min_dist = dist
                nearest_idx = idx

        if min_dist < 5:
            px, py = path_points[nearest_idx]
            self._show_hover_marker_at(px, py)
            self.polyline_hover.emit(nearest_idx)
        else:
            self._hide_hover_marker()

    def _show_hover_marker(self, idx):
        """显示悬停标记（基于折点索引）。"""
        if 0 <= idx < len(self.polyline_points):
            x, y = self.polyline_points[idx]
            self._show_hover_marker_at(x, y)

    def _show_hover_marker_at(self, x, y):
        """在指定图像像素坐标显示悬停标记。"""
        view_pos = self.image_to_view_point(x + 0.5, y + 0.5)
        if self.hover_marker is None:
            self.hover_marker = self._create_marker(0, 0, self.hover_brush, QColor(255, 0, 0), 2, radius=5)
            self.view_box.addItem(self.hover_marker)
        self.hover_marker.setPos(view_pos)
        self.hover_marker.setVisible(True)

    def _hide_hover_marker(self):
        """隐藏悬停标记。"""
        if self.hover_marker:
            self.hover_marker.setVisible(False)

    def get_rect_region(self):
        """获取矩形区域的图像数据。"""
        if not self.current_rect:
            return None

        x1 = int(self.current_rect.x())
        y1 = int(self.current_rect.y())
        x2 = int(self.current_rect.x() + self.current_rect.width())
        y2 = int(self.current_rect.y() + self.current_rect.height())

        return self.read_window_native(x1, y1, x2 - x1 + 1, y2 - y1 + 1)

    def get_polyline_values(self):
        """获取折线上折点的像素值。"""
        if not self.polyline_points:
            return None

        values = []
        for x, y in self.polyline_points:
            values.append(self.get_pixel_value(x, y))
        return values

    def get_polyline_path_values(self):
        """获取折线路径上所有像素的值。"""
        if len(self.polyline_points) < 2:
            return None, None

        all_points = []
        all_values = []
        for i in range(len(self.polyline_points) - 1):
            x0, y0 = self.polyline_points[i]
            x1, y1 = self.polyline_points[i + 1]
            for x, y in self._bresenham_line(x0, y0, x1, y1):
                if 0 <= x < self.original_width and 0 <= y < self.original_height:
                    all_points.append((x, y))
                    all_values.append(self.get_pixel_value(x, y))

        return all_points, all_values

    def _bresenham_line(self, x0, y0, x1, y1):
        """使用 Bresenham 算法获取线段上的所有像素点。"""
        points = []
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy

        x, y = x0, y0
        while True:
            points.append((x, y))
            if x == x1 and y == y1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x += sx
            if e2 < dx:
                err += dx
                y += sy
        return points

    def _build_polyline_path(self, points):
        path = QPainterPath()
        first = self.image_to_view_point(points[0][0] + 0.5, points[0][1] + 0.5)
        path.moveTo(first)
        for x, y in points[1:]:
            path.lineTo(self.image_to_view_point(x + 0.5, y + 0.5))
        return path

    def _image_rect_to_view_rect(self, rect: QRectF) -> QRectF:
        p1 = self.image_to_view_point(rect.left(), rect.top())
        p2 = self.image_to_view_point(rect.right(), rect.bottom())
        return QRectF(p1, p2).normalized()

    def _create_marker(self, x, y, brush, pen_color, pen_width, radius=3):
        item = QGraphicsEllipseItem(-radius, -radius, radius * 2, radius * 2)
        item.setBrush(brush)
        if pen_width > 0:
            pen = QPen(pen_color, pen_width)
            pen.setCosmetic(True)
            item.setPen(pen)
        else:
            item.setPen(QPen(Qt.NoPen))
        item.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        if x != 0 or y != 0:
            item.setPos(self.image_to_view_point(x + 0.5, y + 0.5))
        return item

    def _remove_view_item(self, item):
        if item is None:
            return
        try:
            self.view_box.removeItem(item)
        except (RuntimeError, ValueError):
            pass

    def _refresh_overlay_items(self):
        if self.current_rect is not None:
            self._update_rect_item()
        if self.polyline_points:
            self._update_polyline()
        if self.hover_marker and self.hover_marker.isVisible():
            self._hide_hover_marker()
