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
    def __init__(self, annotation: AnnotationObject, label: LabelClass, selected: bool = False):
        self.annotation = annotation
        self.label = label
        self.selected = selected
        self.path_item = QGraphicsPathItem()
        self.scatter = pg.ScatterPlotItem()
        self.update_style(selected)
        self.update_geometry(annotation)

    def update_style(self, selected: bool) -> None:
        self.selected = selected
        color = QColor(self.label.color)
        fill = QColor(color)
        fill.setAlpha(25 if not selected else 95)
        pen = QPen(color if not selected else QColor("#ffd43b"))
        pen.setWidthF(0.9 if not selected else 1.6)
        pen.setCosmetic(True)
        self.path_item.setPen(pen)
        self.path_item.setBrush(QBrush(fill))
        self.scatter.setBrush(pg.mkBrush(color))
        self.scatter.setPen(pg.mkPen("#ffffff", width=1.0))
        self.scatter.setSize(5 if selected else 0)
        self.scatter.setVisible(selected)

    def update_geometry(self, annotation: AnnotationObject) -> None:
        self.path_item.setPath(build_annotation_path(annotation))
        points = annotation.exterior
        if points and self.selected:
            pts = np.array(points[:-1] if len(points) > 1 else points, dtype=float)
            self.scatter.setData(pos=pts)
        else:
            self.scatter.setData(pos=np.empty((0, 2)))
            self.scatter.setVisible(False)


class PreviewMaskItem(pg.ImageItem):
    def __init__(self):
        super().__init__(axisOrder="row-major")
        self.setOpacity(0.35)

    def update_mask(self, mask: np.ndarray | None, bbox: tuple[int, int, int, int] | None) -> None:
        if mask is None or bbox is None:
            self.clear()
            return
        rgba = np.zeros((mask.shape[0], mask.shape[1], 4), dtype=np.uint8)
        rgba[mask > 0] = [255, 212, 59, 120]
        self.setImage(rgba, autoLevels=False)
        x, y, width, height = bbox
        self.setRect(pg.QtCore.QRectF(x, y, width, height))


class PreviewPolygonItem:
    def __init__(self, annotation: AnnotationObject):
        self.annotation = annotation
        self.path_item = QGraphicsPathItem()
        pen = QPen(QColor("#ffd43b"))
        pen.setWidthF(1.4)
        pen.setCosmetic(True)
        self.path_item.setPen(pen)
        self.path_item.setBrush(QBrush(QColor(255, 212, 59, 70)))
        self.update_geometry(annotation)

    def update_geometry(self, annotation: AnnotationObject) -> None:
        self.path_item.setPath(build_annotation_path(annotation))


class DraftOverlayItem:
    def __init__(self):
        self.path_item = QGraphicsPathItem()
        pen = QPen(QColor("#ffd43b"))
        pen.setWidthF(2.0)
        pen.setStyle(pg.QtCore.Qt.DashLine)
        pen.setCosmetic(True)
        self.path_item.setPen(pen)
        self.path_item.setBrush(QBrush(QColor(255, 212, 59, 40)))
        self.scatter = pg.ScatterPlotItem()
        self.scatter.setBrush(pg.mkBrush("#ffd43b"))
        self.scatter.setPen(pg.mkPen("#ffffff", width=1.0))
        self.scatter.setSize(7)

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
