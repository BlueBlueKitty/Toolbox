"""
分割工具交互控制器。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QObject, Qt, Signal

from src.segmentation.geometry_service import GeometryService
from src.segmentation.models import AnnotationObject


class SegmentationToolController(QObject):
    polygon_finished = Signal(object)
    rectangle_finished = Signal(object)
    magic_wand_requested = Signal(int, int)
    selection_changed = Signal(object)
    geometry_changed = Signal(str, object)
    draft_changed = Signal(str, object)

    TOOL_BROWSE = "browse"
    TOOL_RECTANGLE = "rectangle"
    TOOL_POLYGON = "polygon"
    TOOL_MAGIC_WAND = "magic_wand"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active_tool = self.TOOL_BROWSE
        self.annotations: list[AnnotationObject] = []
        self.selected_annotation_id: str | None = None
        self.selected_vertex_index: int | None = None
        self._polygon_points: list[list[float]] = []
        self._rectangle_start: tuple[float, float] | None = None
        self._dragging_vertex = False

    def set_annotations(self, annotations: list[AnnotationObject]) -> None:
        self.annotations = annotations[:]

    def set_tool(self, tool_name: str) -> None:
        self.active_tool = tool_name
        if tool_name != self.TOOL_POLYGON:
            self._polygon_points = []
        self._rectangle_start = None
        self.draft_changed.emit("clear", None)

    def handle_press(self, payload) -> None:
        if payload.button != Qt.LeftButton:
            return
        x, y = payload.x, payload.y
        if self.active_tool == self.TOOL_RECTANGLE:
            self._rectangle_start = (x, y)
            self.draft_changed.emit("rectangle", GeometryService.rectangle_to_polygon(x, y, x, y))
            return
        if self.active_tool == self.TOOL_POLYGON:
            self._append_polygon_point(x, y)
            self.draft_changed.emit("polygon", self._polygon_points[:])
            if payload.double_click:
                self.finish_polygon()
            return
        if self.active_tool == self.TOOL_MAGIC_WAND:
            self.magic_wand_requested.emit(int(round(x)), int(round(y)))
            return
        self._handle_selection_press(x, y)

    def handle_move(self, payload) -> None:
        x, y = payload.x, payload.y
        if self.active_tool == self.TOOL_POLYGON and bool(payload.buttons & Qt.LeftButton):
            if not self._polygon_points:
                self._append_polygon_point(x, y)
            elif self._distance(self._polygon_points[-1], [x, y]) >= 3:
                self._append_polygon_point(x, y)
            self.draft_changed.emit("polygon", self._polygon_points[:] + [[x, y]])
            return
        if self.active_tool == self.TOOL_POLYGON and self._polygon_points:
            self.draft_changed.emit("polygon", self._polygon_points[:] + [[x, y]])
            return
        if self.active_tool == self.TOOL_RECTANGLE and self._rectangle_start is not None:
            x0, y0 = self._rectangle_start
            self.draft_changed.emit("rectangle", GeometryService.rectangle_to_polygon(x0, y0, x, y))
            return
        if self.active_tool == self.TOOL_BROWSE and self._dragging_vertex and self.selected_annotation_id:
            annotation = self._find_annotation(self.selected_annotation_id)
            if annotation is None or self.selected_vertex_index is None:
                return
            points = [point[:] for point in annotation.exterior]
            points[self.selected_vertex_index] = [x, y]
            if self.selected_vertex_index == 0 or self.selected_vertex_index == len(points) - 1:
                points[0] = [x, y]
                points[-1] = [x, y]
            updated = annotation.clone()
            updated.exterior = GeometryService.ensure_closed(points[:-1]) if len(points) > 2 else points
            xs = [pt[0] for pt in updated.exterior]
            ys = [pt[1] for pt in updated.exterior]
            updated.bbox = [min(xs), min(ys), max(xs), max(ys)]
            self.geometry_changed.emit(annotation.id, updated)

    def handle_release(self, payload) -> None:
        if payload.button != Qt.LeftButton:
            return
        x, y = payload.x, payload.y
        if self.active_tool == self.TOOL_RECTANGLE and self._rectangle_start is not None:
            x0, y0 = self._rectangle_start
            polygon = GeometryService.rectangle_to_polygon(x0, y0, x, y)
            self._rectangle_start = None
            self.draft_changed.emit("clear", None)
            self.rectangle_finished.emit(polygon)
        self._dragging_vertex = False

    def finish_polygon(self) -> None:
        if len(self._polygon_points) < 3:
            self._polygon_points = []
            self.draft_changed.emit("clear", None)
            return
        polygon = GeometryService.ensure_closed(self._polygon_points)
        self._polygon_points = []
        self.draft_changed.emit("clear", None)
        self.polygon_finished.emit(polygon)

    def delete_selected(self) -> str | None:
        selected = self.selected_annotation_id
        self.selected_annotation_id = None
        self.selected_vertex_index = None
        self.selection_changed.emit(None)
        return selected

    def remove_selected_vertex(self) -> bool:
        annotation = self._find_annotation(self.selected_annotation_id)
        if annotation is None or self.selected_vertex_index is None:
            return False
        points = annotation.exterior[:-1]
        if len(points) <= 3:
            return False
        del points[self.selected_vertex_index]
        updated = annotation.clone()
        updated.exterior = GeometryService.ensure_closed(points)
        self.geometry_changed.emit(annotation.id, updated)
        self.selected_vertex_index = None
        return True

    def _append_polygon_point(self, x: float, y: float) -> None:
        point = [x, y]
        if not self._polygon_points or self._distance(self._polygon_points[-1], point) >= 1:
            self._polygon_points.append(point)

    def _handle_selection_press(self, x: float, y: float) -> None:
        hit_annotation = None
        hit_vertex = None
        for annotation in reversed(self.annotations):
            vertex_index = self._hit_vertex(annotation, x, y)
            if vertex_index is not None:
                hit_annotation = annotation
                hit_vertex = vertex_index
                break
            if self._point_in_polygon(annotation.exterior, x, y):
                hit_annotation = annotation
                break
            segment_index = self._hit_segment(annotation, x, y)
            if segment_index is not None:
                updated = annotation.clone()
                points = updated.exterior[:-1]
                points.insert(segment_index + 1, [x, y])
                updated.exterior = GeometryService.ensure_closed(points)
                self.geometry_changed.emit(annotation.id, updated)
                hit_annotation = updated
                hit_vertex = segment_index + 1
                break

        self.selected_annotation_id = hit_annotation.id if hit_annotation else None
        self.selected_vertex_index = hit_vertex
        self._dragging_vertex = hit_vertex is not None
        self.selection_changed.emit(self.selected_annotation_id)

    def _find_annotation(self, annotation_id: str | None) -> AnnotationObject | None:
        if annotation_id is None:
            return None
        for annotation in self.annotations:
            if annotation.id == annotation_id:
                return annotation
        return None

    def _hit_vertex(self, annotation: AnnotationObject, x: float, y: float) -> int | None:
        for index, point in enumerate(annotation.exterior[:-1]):
            if self._distance(point, [x, y]) <= 6:
                return index
        return None

    def _hit_segment(self, annotation: AnnotationObject, x: float, y: float) -> int | None:
        points = annotation.exterior
        for index in range(len(points) - 1):
            dist = self._distance_to_segment([x, y], points[index], points[index + 1])
            if dist <= 4:
                return index
        return None

    def _point_in_polygon(self, points: list[list[float]], x: float, y: float) -> bool:
        inside = False
        for index in range(len(points) - 1):
            x1, y1 = points[index]
            x2, y2 = points[index + 1]
            intersects = ((y1 > y) != (y2 > y)) and (
                x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-6) + x1
            )
            if intersects:
                inside = not inside
        return inside

    def _distance_to_segment(self, point, start, end) -> float:
        px, py = point
        x1, y1 = start
        x2, y2 = end
        dx = x2 - x1
        dy = y2 - y1
        if dx == 0 and dy == 0:
            return self._distance(point, start)
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
        proj_x = x1 + t * dx
        proj_y = y1 + t * dy
        return math.hypot(px - proj_x, py - proj_y)

    def _distance(self, p1, p2) -> float:
        return math.hypot(p1[0] - p2[0], p1[1] - p2[1])
