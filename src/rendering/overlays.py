"""
通用画布叠加项。
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainterPath, QPen, QBrush
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsSimpleTextItem

import pyqtgraph as pg


def build_polygon_path(feature) -> QPainterPath:
    path = QPainterPath()
    path.setFillRule(pg.QtCore.Qt.OddEvenFill)

    def add_ring(points) -> None:
        if not points:
            return
        path.moveTo(points[0][0], points[0][1])
        for x, y in points[1:]:
            path.lineTo(x, y)
        path.closeSubpath()

    add_ring(getattr(feature, "exterior", []))
    for hole in getattr(feature, "holes", []) or []:
        add_ring(hole)
    return path


def build_mask_outer_boundary_path(mask: np.ndarray, bbox: tuple[int, int, int, int]) -> QPainterPath:
    """Build boundaries on pixel-cell edges instead of pixel centres.

    ``findContours`` follows foreground pixel centres.  For a one-pixel-wide
    region that produces a degenerate path which travels out and back along
    the same line.  Here every foreground pixel is treated as a unit square
    and only its exposed edges are retained, producing its true outer border.
    """
    binary = np.ascontiguousarray(np.asarray(mask, dtype=np.uint8) > 0)
    return _build_mask_pixel_edge_path(binary, bbox)


def _build_mask_pixel_edge_path(binary: np.ndarray, bbox: tuple[int, int, int, int]) -> QPainterPath:
    """Trace exact pixel-cell edges while scanning only exposed boundaries.

    OpenCV contours are defined on pixel centres, whereas ImageItem paints a
    Mask over pixel cells.  Building edges at cell boundaries keeps the ants
    exactly aligned with the raster fill.  The edge tests are vectorised, so a
    large solid region costs O(perimeter) Python work instead of O(area).
    """
    height, width = binary.shape[:2]
    x0, y0, _, _ = bbox
    outgoing: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def add_edge(start: tuple[int, int], end: tuple[int, int]) -> None:
        outgoing.setdefault(start, []).append(end)

    if height <= 0 or width <= 0 or not np.any(binary):
        return QPainterPath()

    above = np.zeros_like(binary)
    above[1:] = binary[:-1]
    below = np.zeros_like(binary)
    below[:-1] = binary[1:]
    left = np.zeros_like(binary)
    left[:, 1:] = binary[:, :-1]
    right = np.zeros_like(binary)
    right[:, :-1] = binary[:, 1:]

    def add_edges(edge_mask: np.ndarray, edge_factory) -> None:
        rows, cols = np.nonzero(edge_mask)
        for row, col in zip(rows.tolist(), cols.tolist()):
            add_edge(*edge_factory(int(x0 + col), int(y0 + row)))

    add_edges(binary & ~above, lambda x, y: ((x, y), (x + 1, y)))
    add_edges(binary & ~right, lambda x, y: ((x + 1, y), (x + 1, y + 1)))
    add_edges(binary & ~below, lambda x, y: ((x + 1, y + 1), (x, y + 1)))
    add_edges(binary & ~left, lambda x, y: ((x, y + 1), (x, y)))

    path = QPainterPath()
    while outgoing:
        start = next(iter(outgoing))
        current = start
        points = [current]
        while current in outgoing:
            end = outgoing[current].pop()
            if not outgoing[current]:
                del outgoing[current]
            if len(points) >= 2:
                previous = points[-2]
                current_point = points[-1]
                previous_direction = (current_point[0] - previous[0], current_point[1] - previous[1])
                next_direction = (end[0] - current_point[0], end[1] - current_point[1])
                same_direction = (
                    previous_direction[0] * next_direction[1] == previous_direction[1] * next_direction[0]
                    and previous_direction[0] * next_direction[0] + previous_direction[1] * next_direction[1] > 0
                )
                if same_direction:
                    points[-1] = end
                else:
                    points.append(end)
            else:
                points.append(end)
            current = end
            if current == start:
                break
        path.moveTo(float(points[0][0]), float(points[0][1]))
        for point in points[1:]:
            path.lineTo(float(point[0]), float(point[1]))
        if current == start:
            path.closeSubpath()
    return path


class PolygonOverlayItem:
    def __init__(
        self,
        feature,
        color: str,
        selected: bool = False,
        editable: bool = False,
        active_vertex=None,
    ):
        self.feature = feature
        self.path_item = QGraphicsPathItem()
        self.scatter = pg.ScatterPlotItem()
        self._style = color
        self._editable = False
        self.update_style(selected, editable)
        self.update_geometry(feature, active_vertex)

    def update_style(self, selected: bool, editable: bool = False) -> None:
        style = self._style if isinstance(self._style, dict) else {"color": str(self._style)}
        color = QColor(style.get("color", "#22c55e"))
        fill = QColor(color)
        base_fill_alpha = int(style.get("fill_alpha", 25 if not selected else 95))
        fill.setAlpha(base_fill_alpha if not selected else max(base_fill_alpha, 95))
        pen = QPen(color if not selected else QColor(color).lighter(145))
        width = float(style.get("line_width", 0.9 if not selected else 1.6))
        pen.setWidthF(width if not selected else max(width, 1.6))
        pen.setCosmetic(True)
        self.path_item.setPen(pen)
        self.path_item.setBrush(QBrush(fill))
        self.scatter.setBrush(pg.mkBrush(color))
        self.scatter.setPen(pg.mkPen("#ffffff", width=1.0))
        self.scatter.setSize(7 if editable else 0)
        self.scatter.setVisible(editable)
        self._editable = editable

    def update_geometry(self, feature, active_vertex=None) -> None:
        self.path_item.setPath(build_polygon_path(feature))
        if not self._editable:
            self.scatter.setData(pos=np.empty((0, 2)))
            self.scatter.setVisible(False)
            return
        spots = []
        rings = [("exterior", -1, getattr(feature, "exterior", []))] + [
            ("hole", idx, hole) for idx, hole in enumerate(getattr(feature, "holes", []) or [])
        ]
        for ring_type, hole_index, ring in rings:
            for index, point in enumerate(ring[:-1] if len(ring) > 1 else ring):
                is_active = active_vertex == (ring_type, hole_index, index)
                style = self._style if isinstance(self._style, dict) else {"color": str(self._style)}
                spot_color = QColor("#f97316") if is_active else QColor(style.get("color", "#22c55e"))
                border_color = QColor("#111827") if is_active else QColor("#ffffff")
                spots.append(
                    {
                        "pos": point,
                        "size": 9 if is_active else 7,
                        "brush": pg.mkBrush(spot_color),
                        "pen": pg.mkPen(border_color, width=1.4),
                    }
                )
        if spots:
            self.scatter.setData(spots)
        else:
            self.scatter.setData(pos=np.empty((0, 2)))
        self.scatter.setVisible(bool(spots))


class PreviewMaskItem(pg.ImageItem):
    def __init__(self):
        super().__init__(axisOrder="row-major")

    def update_mask(self, mask: np.ndarray | None, bbox: tuple[int, int, int, int] | None, color_name: str = "#ffd43b") -> None:
        if mask is None or bbox is None:
            self.clear()
            return
        rgba = np.zeros((mask.shape[0], mask.shape[1], 4), dtype=np.uint8)
        color = QColor(color_name)
        rgba[mask > 0] = [color.red(), color.green(), color.blue(), 128]
        self.setImage(rgba, autoLevels=False)
        x, y, width, height = bbox
        self.setRect(pg.QtCore.QRectF(x, y, width, height))


class MaskSelectionItem:
    """Photoshop-style animated outline for a selected Mask connected component."""

    def __init__(self, primary_color: str = "#111111", secondary_color: str = "#ffffff"):
        self.path_item = QGraphicsPathItem()
        self.white_path_item = QGraphicsPathItem(self.path_item)
        self.path_item.setZValue(20_100)
        self.path_item.setBrush(QBrush(Qt.NoBrush))
        self.white_path_item.setBrush(QBrush(Qt.NoBrush))
        self._primary_color = QColor(primary_color)
        self._secondary_color = QColor(secondary_color)
        self._dash_offset = 0.0
        self._set_pen()
        self.clear()

    def update_mask(self, mask: np.ndarray | None, bbox: tuple[int, int, int, int] | None) -> None:
        self.update_masks([] if mask is None or bbox is None else [(mask, bbox)])

    def update_masks(self, selections) -> None:
        """Update one combined outline for one or more selected local Mask patches."""
        valid_selections = [
            (mask, bbox)
            for mask, bbox in (selections or [])
            if mask is not None and bbox is not None and np.any(mask)
        ]
        if not valid_selections:
            self.clear()
            return
        path = QPainterPath()
        for mask, bbox in valid_selections:
            path.addPath(build_mask_outer_boundary_path(mask, bbox))
        self.path_item.setPath(path)
        self.white_path_item.setPath(path)
        self.path_item.setVisible(not path.isEmpty())

    def update_path(self, path: QPainterPath | None) -> None:
        path = path or QPainterPath()
        self.path_item.setPath(path)
        self.white_path_item.setPath(path)
        self.path_item.setVisible(not path.isEmpty())

    def set_dash_offset(self, offset: float) -> None:
        self._dash_offset = float(offset)
        self._set_pen()

    def set_colors(self, primary_color: str, secondary_color: str = "#ffffff") -> None:
        self._primary_color = QColor(primary_color)
        self._secondary_color = QColor(secondary_color)
        self._set_pen()

    def _set_pen(self) -> None:
        for item, color, offset in (
            (self.path_item, self._primary_color, self._dash_offset),
            (self.white_path_item, self._secondary_color, self._dash_offset + 3.0),
        ):
            pen = QPen(color)
            pen.setWidthF(1.6)
            pen.setCosmetic(True)
            pen.setDashPattern([3.0, 3.0])
            pen.setDashOffset(offset)
            item.setPen(pen)

    def clear(self) -> None:
        self.path_item.setPath(QPainterPath())
        self.path_item.setVisible(False)


class DraftOverlayItem:
    def __init__(self):
        self.path_item = QGraphicsPathItem()
        self.scatter = pg.ScatterPlotItem()
        self.scatter.setSize(7)
        self.update_style("#ffd43b", fill_alpha=40)

    def update_style(self, color_name: str, fill_alpha: int = 40) -> None:
        color = QColor(color_name)
        pen = QPen(color)
        pen.setWidthF(2.0)
        pen.setStyle(pg.QtCore.Qt.DashLine)
        pen.setCosmetic(True)
        self.path_item.setPen(pen)
        fill = QColor(color)
        fill.setAlpha(fill_alpha)
        self.path_item.setBrush(QBrush(fill))
        self.scatter.setBrush(pg.mkBrush(color))
        self.scatter.setPen(pg.mkPen("#ffffff", width=1.0))

    def update_geometry(self, points: list[list[float]] | None) -> None:
        path = QPainterPath()
        if points:
            path.moveTo(points[0][0], points[0][1])
            for x, y in points[1:]:
                path.lineTo(x, y)
        self.path_item.setPath(path)
        self.scatter.setData(pos=np.array(points, dtype=float) if points else np.empty((0, 2)))


class SnapIndicatorItem:
    def __init__(self):
        self.path_item = QGraphicsPathItem()
        self.path_item.setZValue(10_000)
        self.path_item.setFlag(QGraphicsPathItem.ItemIgnoresTransformations, True)
        self.text_item = QGraphicsSimpleTextItem()
        self.text_item.setZValue(10_001)
        self.text_item.setFlag(QGraphicsSimpleTextItem.ItemIgnoresTransformations, True)
        self.text_item.setParentItem(self.path_item)
        self.clear()

    def update_indicator(self, snap_type: str | None, x: float | None, y: float | None) -> None:
        if snap_type is None or x is None or y is None:
            self.clear()
            return
        path = QPainterPath()
        size = 10.0
        if snap_type == "vertex":
            path.addRect(-size, -size, size * 2.0, size * 2.0)
            label_text = "节点"
        elif snap_type == "edge":
            path.addEllipse(-size, -size, size * 2.0, size * 2.0)
            path.moveTo(-size * 0.6, 0)
            path.lineTo(size * 0.6, 0)
            path.moveTo(0, -size * 0.6)
            path.lineTo(0, size * 0.6)
            label_text = "边"
        else:
            self.clear()
            return
        self.path_item.setPos(x, y)
        self.path_item.setPath(path)
        self.path_item.setVisible(True)
        pen = QPen(QColor("#dc2626"), 1.6)
        pen.setCosmetic(True)
        self.path_item.setPen(pen)
        self.path_item.setBrush(QBrush(Qt.NoBrush))
        self.text_item.setBrush(QBrush(QColor("#dc2626")))
        self.text_item.setText(label_text)
        self.text_item.setPos(size + 6.0, -size - 4.0)
        self.text_item.setVisible(True)

    def clear(self) -> None:
        self.path_item.setPos(0.0, 0.0)
        self.path_item.setPath(QPainterPath())
        self.path_item.setVisible(False)
        self.text_item.setText("")
        self.text_item.setVisible(False)
