"""
标注叠加图层。
"""

from __future__ import annotations

import numpy as np
from PySide6.QtGui import QColor, QPainterPath, QPen, QBrush
from PySide6.QtWidgets import QGraphicsPathItem

import pyqtgraph as pg

from src.segmentation.models import AnnotationObject, LabelClass


class PolygonOverlayItem:
    def __init__(self, annotation: AnnotationObject, label: LabelClass, selected: bool = False):
        self.annotation = annotation
        self.label = label
        self.path_item = QGraphicsPathItem()
        self.scatter = pg.ScatterPlotItem()
        self.update_style(selected)
        self.update_geometry(annotation.exterior)

    def update_style(self, selected: bool) -> None:
        color = QColor(self.label.color)
        fill = QColor(color)
        fill.setAlpha(80 if not selected else 120)
        pen = QPen(color if not selected else QColor("#ffd43b"))
        pen.setWidthF(2.0 if not selected else 3.0)
        pen.setCosmetic(True)
        self.path_item.setPen(pen)
        self.path_item.setBrush(QBrush(fill))
        self.scatter.setBrush(pg.mkBrush(color))
        self.scatter.setPen(pg.mkPen("#ffffff", width=1.0))
        self.scatter.setSize(8 if selected else 6)

    def update_geometry(self, points: list[list[float]]) -> None:
        path = QPainterPath()
        if points:
            path.moveTo(points[0][0], points[0][1])
            for x, y in points[1:]:
                path.lineTo(x, y)
        self.path_item.setPath(path)
        if points:
            pts = np.array(points[:-1] if len(points) > 1 else points, dtype=float)
            self.scatter.setData(pos=pts)
        else:
            self.scatter.setData(pos=np.empty((0, 2)))


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
