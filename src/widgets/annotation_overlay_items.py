"""
标注叠加图层。
"""

from __future__ import annotations

import numpy as np
from PySide6.QtGui import QColor, QPainterPath, QPen, QBrush
from PySide6.QtWidgets import QGraphicsPathItem

import pyqtgraph as pg

from src.segmentation.models import AnnotationObject, LabelClass


def build_annotation_path(annotation: AnnotationObject) -> QPainterPath:
    path = QPainterPath()
    path.setFillRule(pg.QtCore.Qt.OddEvenFill)

    def add_ring(points: list[list[float]]) -> None:
        if not points:
            return
        path.moveTo(points[0][0], points[0][1])
        for x, y in points[1:]:
            path.lineTo(x, y)
        path.closeSubpath()

    add_ring(annotation.exterior)
    for hole in annotation.holes:
        add_ring(hole)
    return path


class PolygonOverlayItem:
    def __init__(
        self,
        annotation: AnnotationObject,
        label: LabelClass,
        selected: bool = False,
        editable: bool = False,
        active_vertex=None,
    ):
        self.annotation = annotation
        self.label = label
        self.selected = selected
        self.editable = editable
        self.active_vertex = active_vertex
        self.path_item = QGraphicsPathItem()
        self.scatter = pg.ScatterPlotItem()
        self.update_style(selected, editable)
        self.update_geometry(annotation, active_vertex)

    def update_style(self, selected: bool, editable: bool = False) -> None:
        self.selected = selected
        self.editable = editable
        color = QColor(self.label.color)
        fill = QColor(color)
        fill.setAlpha(25 if not selected else 95)
        pen = QPen(color if not selected else QColor(color).lighter(145))
        pen.setWidthF(0.9 if not selected else 1.6)
        pen.setCosmetic(True)
        self.path_item.setPen(pen)
        self.path_item.setBrush(QBrush(fill))
        self.scatter.setBrush(pg.mkBrush(color))
        self.scatter.setPen(pg.mkPen("#ffffff", width=1.0))
        self.scatter.setSize(7 if editable else 0)
        self.scatter.setVisible(editable)

    def update_geometry(self, annotation: AnnotationObject, active_vertex=None) -> None:
        self.path_item.setPath(build_annotation_path(annotation))
        if not self.editable:
            self.scatter.setData(pos=np.empty((0, 2)))
            self.scatter.setVisible(False)
            return
        spots = []
        rings = [("exterior", -1, annotation.exterior)] + [
            ("hole", idx, hole) for idx, hole in enumerate(annotation.holes)
        ]
        for ring_type, hole_index, ring in rings:
            if not ring:
                continue
            for index, point in enumerate(ring[:-1] if len(ring) > 1 else ring):
                is_active = active_vertex == (ring_type, hole_index, index)
                spot_color = QColor("#f97316") if is_active else QColor(self.label.color)
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
            self.scatter.setVisible(False)


class PreviewMaskItem(pg.ImageItem):
    def __init__(self):
        super().__init__(axisOrder="row-major")
        self.setOpacity(0.35)

    def update_mask(self, mask: np.ndarray | None, bbox: tuple[int, int, int, int] | None, color_name: str = "#ffd43b") -> None:
        if mask is None or bbox is None:
            self.clear()
            return
        rgba = np.zeros((mask.shape[0], mask.shape[1], 4), dtype=np.uint8)
        color = QColor(color_name)
        rgba[mask > 0] = [color.red(), color.green(), color.blue(), 120]
        self.setImage(rgba, autoLevels=False)
        x, y, width, height = bbox
        self.setRect(pg.QtCore.QRectF(x, y, width, height))


class PreviewPolygonItem:
    def __init__(self, annotation: AnnotationObject, color_name: str):
        self.annotation = annotation
        self.path_item = QGraphicsPathItem()
        color = QColor(color_name)
        pen = QPen(color)
        pen.setWidthF(1.4)
        pen.setCosmetic(True)
        self.path_item.setPen(pen)
        fill = QColor(color)
        fill.setAlpha(70)
        self.path_item.setBrush(QBrush(fill))
        self.update_geometry(annotation)

    def update_geometry(self, annotation: AnnotationObject) -> None:
        self.path_item.setPath(build_annotation_path(annotation))


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
        if points:
            pts = np.array(points, dtype=float)
            self.scatter.setData(pos=pts)
        else:
            self.scatter.setData(pos=np.empty((0, 2)))
