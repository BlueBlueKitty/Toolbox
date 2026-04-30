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
        rgba[mask > 0] = [color.red(), color.green(), color.blue(), 255]
        self.setImage(rgba, autoLevels=False)
        x, y, width, height = bbox
        self.setRect(pg.QtCore.QRectF(x, y, width, height))


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
