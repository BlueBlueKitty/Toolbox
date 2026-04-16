"""
分割工具交互控制器。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QObject, Qt, Signal
from shapely.geometry import Point, box as shapely_box

from src.segmentation.geometry_service import GeometryService
from src.segmentation.models import AnnotationObject


class SegmentationToolController(QObject):
    polygon_finished = Signal(object)
    rectangle_finished = Signal(object)
    magic_wand_requested = Signal(int, int)
    selection_changed = Signal(object)
    geometry_changed = Signal(str, object)
    geometry_committed = Signal(str, object, object)
    draft_changed = Signal(str, object)
    message_requested = Signal(str)

    TOOL_BROWSE = "browse"
    TOOL_RECTANGLE = "rectangle"
    TOOL_POLYGON = "polygon"
    TOOL_MAGIC_WAND = "magic_wand"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active_tool = self.TOOL_BROWSE
        self.annotations: list[AnnotationObject] = []
        self.selected_annotation_id: str | None = None
        self.selected_annotation_ids: set[str] = set()
        self.selected_vertex_index = None
        self._polygon_points: list[list[float]] = []
        self._rectangle_start: tuple[float, float] | None = None
        self._dragging_vertex = False
        self._selection_box_start: tuple[float, float] | None = None
        self._selection_box_active = False
        self._selection_box_additive = False
        self._selection_box_click_target: str | None = None
        self._drag_origin_annotation: AnnotationObject | None = None
        self._drag_last_annotation: AnnotationObject | None = None
        self._snap_grid_size = 10.0
        self._snap_tolerance = 8.0

    def set_annotations(self, annotations: list[AnnotationObject]) -> None:
        self.annotations = annotations[:]

    def set_tool(self, tool_name: str) -> None:
        self.active_tool = tool_name
        if tool_name != self.TOOL_POLYGON:
            self._polygon_points = []
        self._rectangle_start = None
        self._dragging_vertex = False
        self.draft_changed.emit("clear", None)

    def handle_press(self, payload) -> None:
        x, y = payload.x, payload.y
        if payload.button == Qt.RightButton and self.active_tool == self.TOOL_BROWSE:
            self._handle_right_click_insert(x, y)
            return
        if payload.button != Qt.LeftButton:
            return
        if self.active_tool == self.TOOL_RECTANGLE:
            self._clear_selection_for_new_drawing()
            self._rectangle_start = (x, y)
            self.draft_changed.emit("rectangle", GeometryService.rectangle_to_polygon(x, y, x, y))
            return
        if self.active_tool == self.TOOL_POLYGON:
            if not self._polygon_points:
                self._clear_selection_for_new_drawing()
            self._append_polygon_point(x, y)
            self.draft_changed.emit("polygon", self._polygon_points[:])
            if payload.double_click:
                self.finish_polygon()
            return
        if self.active_tool == self.TOOL_MAGIC_WAND:
            self._clear_selection_for_new_drawing()
            self.magic_wand_requested.emit(int(round(x)), int(round(y)))
            return
        self._handle_selection_press(x, y, bool(payload.modifiers & Qt.ControlModifier))

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
        if self.active_tool == self.TOOL_BROWSE and self._selection_box_start is not None and bool(payload.buttons & Qt.LeftButton):
            if self._dragging_vertex and self.selected_annotation_id and self.selected_vertex_index is not None:
                annotation = self._find_annotation(self.selected_annotation_id)
                if annotation is None:
                    return
                updated = annotation.clone()
                snapped_x, snapped_y = self._apply_snapping(x, y, annotation.id, self.selected_vertex_index)
                self._update_ring_vertex(updated, self.selected_vertex_index, snapped_x, snapped_y)
                if not GeometryService.is_annotation_geometry_valid(updated):
                    return
                GeometryService.refresh_annotation_metadata(updated)
                self._drag_last_annotation = updated.clone()
                self.geometry_changed.emit(annotation.id, updated)
                return
            if self._distance(self._selection_box_start, [x, y]) >= 3:
                self._selection_box_active = True
                x0, y0 = self._selection_box_start
                self.draft_changed.emit("selection_box", GeometryService.rectangle_to_polygon(x0, y0, x, y))

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
        elif self.active_tool == self.TOOL_BROWSE and self._selection_box_start is not None:
            if self._dragging_vertex:
                if (
                    self.selected_annotation_id
                    and self._drag_origin_annotation is not None
                    and self._drag_last_annotation is not None
                ):
                    self.geometry_committed.emit(
                        self.selected_annotation_id,
                        self._drag_origin_annotation.clone(),
                        self._drag_last_annotation.clone(),
                    )
                self._selection_box_start = None
                self._selection_box_active = False
                self._selection_box_additive = False
                self._selection_box_click_target = None
            elif self._selection_box_active:
                self._apply_box_selection(self._selection_box_start, (x, y), self._selection_box_additive)
                self._selection_box_start = None
                self._selection_box_active = False
                self._selection_box_additive = False
                self._selection_box_click_target = None
                self.draft_changed.emit("clear", None)
            else:
                self._apply_click_selection(self._selection_box_click_target, bool(payload.modifiers & Qt.ControlModifier))
                self._selection_box_start = None
                self._selection_box_active = False
                self._selection_box_additive = False
                self._selection_box_click_target = None
                self.draft_changed.emit("clear", None)
        self._dragging_vertex = False
        self._drag_origin_annotation = None
        self._drag_last_annotation = None

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
        self.selected_annotation_ids.clear()
        self.selected_vertex_index = None
        self.selection_changed.emit(set())
        return selected

    def remove_selected_vertex(self) -> AnnotationObject | None:
        if len(self.selected_annotation_ids) != 1:
            if len(self.selected_annotation_ids) > 1:
                self.message_requested.emit("当前为多选状态。节点编辑仅支持单选对象，请先单选一个对象。")
            return None
        annotation = self._find_annotation(self.selected_annotation_id)
        if annotation is None or self.selected_vertex_index is None:
            return None
        before = annotation.clone()
        updated = annotation.clone()
        if not self._remove_ring_vertex(updated, self.selected_vertex_index):
            self.message_requested.emit("多边形至少需要保留 3 个节点。")
            return None
        if not GeometryService.is_annotation_geometry_valid(updated):
            self.message_requested.emit("删除该节点会导致几何无效，已取消。")
            return None
        GeometryService.refresh_annotation_metadata(updated)
        self.geometry_changed.emit(annotation.id, updated)
        self.geometry_committed.emit(annotation.id, before, updated)
        self.selected_vertex_index = None
        return updated

    def editable_annotation_id(self) -> str | None:
        if self.active_tool != self.TOOL_BROWSE:
            return None
        if len(self.selected_annotation_ids) != 1 or self.selected_annotation_id is None:
            return None
        return self.selected_annotation_id

    def is_node_edit_active(self) -> bool:
        return self.editable_annotation_id() is not None

    def _append_polygon_point(self, x: float, y: float) -> None:
        point = [x, y]
        if not self._polygon_points or self._distance(self._polygon_points[-1], point) >= 1:
            self._polygon_points.append(point)

    def _handle_selection_press(self, x: float, y: float, additive: bool = False) -> None:
        hit_annotation = None
        hit_vertex = None
        for annotation in reversed(self.annotations):
            vertex_index = self._hit_vertex(annotation, x, y)
            if vertex_index is not None:
                if additive or len(self.selected_annotation_ids) > 1:
                    self.message_requested.emit("当前为多选状态。节点编辑仅支持单选对象，请先单选一个对象。")
                    hit_annotation = annotation
                    break
                hit_annotation = annotation
                hit_vertex = vertex_index
                break
            polygon = GeometryService.annotation_to_polygon(annotation)
            if polygon is not None and not polygon.is_empty and polygon.covers(Point(x, y)):
                hit_annotation = annotation
                break

        self.selected_vertex_index = hit_vertex
        self._dragging_vertex = hit_vertex is not None
        self._drag_origin_annotation = hit_annotation.clone() if hit_vertex is not None and hit_annotation is not None else None
        self._drag_last_annotation = hit_annotation.clone() if hit_vertex is not None and hit_annotation is not None else None
        self._selection_box_start = (x, y)
        self._selection_box_active = False
        self._selection_box_additive = additive
        self._selection_box_click_target = hit_annotation.id if hit_annotation else None
        if hit_vertex is not None and hit_annotation is not None:
            self.selected_annotation_id = hit_annotation.id
            self.selected_annotation_ids = {hit_annotation.id}
            self.selection_changed.emit(set(self.selected_annotation_ids))

    def _find_annotation(self, annotation_id: str | None) -> AnnotationObject | None:
        if annotation_id is None:
            return None
        for annotation in self.annotations:
            if annotation.id == annotation_id:
                return annotation
        return None

    def _hit_vertex(self, annotation: AnnotationObject, x: float, y: float):
        for index, point in enumerate(annotation.exterior[:-1]):
            if self._distance(point, [x, y]) <= 6:
                return ("exterior", -1, index)
        for hole_index, hole in enumerate(annotation.holes):
            for index, point in enumerate(hole[:-1]):
                if self._distance(point, [x, y]) <= 6:
                    return ("hole", hole_index, index)
        return None

    def _hit_segment(self, annotation: AnnotationObject, x: float, y: float):
        rings = [("exterior", -1, annotation.exterior)] + [
            ("hole", idx, hole) for idx, hole in enumerate(annotation.holes)
        ]
        for ring_type, hole_index, points in rings:
            for index in range(len(points) - 1):
                dist = self._distance_to_segment([x, y], points[index], points[index + 1])
                if dist <= 4:
                    return (ring_type, hole_index, index)
        return None

    def _handle_right_click_insert(self, x: float, y: float) -> None:
        if len(self.selected_annotation_ids) > 1:
            self.message_requested.emit("当前为多选状态。节点编辑仅支持单选对象，请先单选一个对象。")
            return
        for annotation in reversed(self.annotations):
            segment_index = self._hit_segment(annotation, x, y)
            if segment_index is None:
                continue
            before = annotation.clone()
            updated = annotation.clone()
            self._insert_ring_vertex(updated, segment_index, x, y)
            if not GeometryService.is_annotation_geometry_valid(updated):
                self.message_requested.emit("插入该节点会导致几何无效，已取消。")
                return
            GeometryService.refresh_annotation_metadata(updated)
            hit_vertex = (segment_index[0], segment_index[1], segment_index[2] + 1)
            self.selected_annotation_id = annotation.id
            self.selected_annotation_ids = {annotation.id}
            self.selected_vertex_index = hit_vertex
            self.geometry_changed.emit(annotation.id, updated)
            self.geometry_committed.emit(annotation.id, before, updated)
            self.selection_changed.emit(set(self.selected_annotation_ids))
            return

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

    def _apply_snapping(self, x: float, y: float, annotation_id: str, vertex_ref) -> tuple[float, float]:
        snapped_x, snapped_y = x, y
        best_distance = self._snap_tolerance + 1.0

        grid_x = round(x / self._snap_grid_size) * self._snap_grid_size
        grid_y = round(y / self._snap_grid_size) * self._snap_grid_size
        grid_distance = math.hypot(grid_x - x, grid_y - y)
        if grid_distance <= self._snap_tolerance:
            snapped_x, snapped_y = grid_x, grid_y
            best_distance = grid_distance

        for annotation in self.annotations:
            rings = [annotation.exterior] + annotation.holes
            for ring in rings:
                for index, point in enumerate(ring[:-1]):
                    if annotation.id == annotation_id and vertex_ref == ("exterior", -1, index):
                        continue
                    distance = self._distance(point, [x, y])
                    if distance < best_distance and distance <= self._snap_tolerance:
                        snapped_x, snapped_y = point[0], point[1]
                        best_distance = distance

        for annotation in self.annotations:
            rings = [("exterior", -1, annotation.exterior)] + [("hole", idx, hole) for idx, hole in enumerate(annotation.holes)]
            for ring_type, hole_index, ring in rings:
                for index in range(len(ring) - 1):
                    start = ring[index]
                    end = ring[index + 1]
                    if annotation.id == annotation_id and {index, index + 1} & {vertex_ref[2], 0 if vertex_ref[2] == len(ring) - 2 else -1}:
                        continue
                    projection = self._project_point_to_segment([x, y], start, end)
                    if projection is None:
                        continue
                    distance = self._distance(projection, [x, y])
                    if distance < best_distance and distance <= self._snap_tolerance:
                        snapped_x, snapped_y = projection[0], projection[1]
                        best_distance = distance

        return snapped_x, snapped_y

    def _project_point_to_segment(self, point, start, end):
        px, py = point
        x1, y1 = start
        x2, y2 = end
        dx = x2 - x1
        dy = y2 - y1
        denom = dx * dx + dy * dy
        if denom == 0:
            return None
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / denom))
        return [x1 + t * dx, y1 + t * dy]

    def _clear_selection_for_new_drawing(self) -> None:
        if self.selected_annotation_ids or self.selected_vertex_index is not None:
            self.selected_annotation_id = None
            self.selected_annotation_ids.clear()
            self.selected_vertex_index = None
            self._dragging_vertex = False
            self.selection_changed.emit(set())

    def select_all(self) -> None:
        self.selected_annotation_ids = {annotation.id for annotation in self.annotations}
        self.selected_annotation_id = next(iter(self.selected_annotation_ids), None)
        self.selected_vertex_index = None
        self._dragging_vertex = False
        self.selection_changed.emit(set(self.selected_annotation_ids))

    def _apply_click_selection(self, annotation_id: str | None, additive: bool) -> None:
        if additive:
            if annotation_id is None:
                return
            if annotation_id in self.selected_annotation_ids:
                self.selected_annotation_ids.remove(annotation_id)
            else:
                self.selected_annotation_ids.add(annotation_id)
            self.selected_annotation_id = annotation_id if annotation_id in self.selected_annotation_ids else next(iter(self.selected_annotation_ids), None)
        else:
            self.selected_annotation_ids = {annotation_id} if annotation_id else set()
            self.selected_annotation_id = annotation_id
        self.selected_vertex_index = None
        self.selection_changed.emit(set(self.selected_annotation_ids))

    def _apply_box_selection(self, start: tuple[float, float], end: tuple[float, float], additive: bool) -> None:
        x0, y0 = start
        x1, y1 = end
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        crossing = x1 >= x0 and y1 >= y0
        select_box = shapely_box(left, top, right, bottom)
        selected = set(self.selected_annotation_ids) if additive else set()
        for annotation in self.annotations:
            polygon = GeometryService.annotation_to_polygon(annotation)
            if polygon is None or polygon.is_empty:
                continue
            hit = select_box.covers(polygon) if crossing else select_box.intersects(polygon)
            if hit:
                selected.add(annotation.id)
            elif not additive and annotation.id in selected:
                selected.remove(annotation.id)
        self.selected_annotation_ids = selected
        self.selected_annotation_id = next(iter(selected), None)
        self.selected_vertex_index = None
        self.selection_changed.emit(set(self.selected_annotation_ids))

    def _get_ring_points(self, annotation: AnnotationObject, vertex_ref):
        ring_type, hole_index, _ = vertex_ref
        return annotation.exterior if ring_type == "exterior" else annotation.holes[hole_index]

    def _set_ring_points(self, annotation: AnnotationObject, vertex_ref, points: list[list[float]]) -> None:
        ring_type, hole_index, _ = vertex_ref
        if ring_type == "exterior":
            annotation.exterior = points
        else:
            annotation.holes[hole_index] = points

    def _update_ring_vertex(self, annotation: AnnotationObject, vertex_ref, x: float, y: float) -> None:
        _, _, vertex_index = vertex_ref
        points = [point[:] for point in self._get_ring_points(annotation, vertex_ref)]
        points[vertex_index] = [x, y]
        if vertex_index == 0 or vertex_index == len(points) - 1:
            points[0] = [x, y]
            points[-1] = [x, y]
        self._set_ring_points(annotation, vertex_ref, GeometryService.ensure_closed(points[:-1]) if len(points) > 2 else points)

    def _insert_ring_vertex(self, annotation: AnnotationObject, segment_ref, x: float, y: float) -> None:
        ring_type, hole_index, segment_index = segment_ref
        base_ref = (ring_type, hole_index, 0)
        points = [point[:] for point in self._get_ring_points(annotation, base_ref)[:-1]]
        points.insert(segment_index + 1, [x, y])
        self._set_ring_points(annotation, base_ref, GeometryService.ensure_closed(points))

    def _remove_ring_vertex(self, annotation: AnnotationObject, vertex_ref) -> bool:
        base_ref = (vertex_ref[0], vertex_ref[1], 0)
        points = [point[:] for point in self._get_ring_points(annotation, base_ref)[:-1]]
        if len(points) <= 3:
            return False
        del points[vertex_ref[2]]
        self._set_ring_points(annotation, base_ref, GeometryService.ensure_closed(points))
        return True
