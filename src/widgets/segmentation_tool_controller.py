"""
分割工具交互控制器。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QObject, Qt, Signal
from shapely.geometry import Point, box as shapely_box

from src.segmentation.geometry_service import GeometryService
from src.rendering.models import ViewportState
from src.segmentation.models import AnnotationObject


class SegmentationToolController(QObject):
    polygon_finished = Signal(object)
    rectangle_finished = Signal(object)
    magic_wand_requested = Signal(int, int)
    selection_changed = Signal(object)
    geometry_changed = Signal(str, object)
    geometry_committed = Signal(str, object, object)
    draft_changed = Signal(str, object)
    snap_indicator_changed = Signal(object, object)
    message_requested = Signal(str)

    TOOL_BROWSE = "browse"
    TOOL_RECTANGLE = "rectangle"
    TOOL_POLYGON = "polygon"
    TOOL_MAGIC_WAND = "magic_wand"
    TOOL_BRUSH = "brush"
    TOOL_ERASER = "eraser"

    def __init__(self, parent=None):
        """__init__。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            parent (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        super().__init__(parent)
        self.active_tool = self.TOOL_BROWSE
        self.annotations: list[AnnotationObject] = []
        self.selected_annotation_id: str | None = None
        self.selected_annotation_ids: set[str] = set()
        self.selected_vertex_index = None
        self._last_edited_vertex_ref = None
        self._polygon_points: list[list[float]] = []
        self._rectangle_start: tuple[float, float] | None = None
        self._dragging_vertex = False
        self._selection_box_start: tuple[float, float] | None = None
        self._selection_box_active = False
        self._selection_box_additive = False
        self._selection_box_click_target: str | None = None
        self._drag_origin_annotation: AnnotationObject | None = None
        self._drag_last_annotation: AnnotationObject | None = None
        self._viewport_state = ViewportState()
        self._screen_snap_tolerance = {
            "vertex": 18.0,
            "edge": 16.0,
        }

    def set_annotations(self, annotations: list[AnnotationObject]) -> None:
        """set_annotations。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotations (list[AnnotationObject]): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self.annotations = annotations[:]

    def set_view_state(self, state: ViewportState) -> None:
        """set_view_state。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            state (ViewportState): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self._viewport_state = state

    def set_tool(self, tool_name: str) -> None:
        """set_tool。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            tool_name (str): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self.active_tool = tool_name
        if tool_name != self.TOOL_POLYGON:
            self._polygon_points = []
        self._rectangle_start = None
        self._dragging_vertex = False
        self.draft_changed.emit("clear", None)
        self.snap_indicator_changed.emit(None, None)
        self._last_edited_vertex_ref = None

    def handle_press(self, payload) -> None:
        """handle_press。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            payload (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
            self.magic_wand_requested.emit(int(math.floor(x)), int(math.floor(y)))
            return
        self._handle_selection_press(x, y, bool(payload.modifiers & Qt.ControlModifier))

    def handle_move(self, payload) -> None:
        """handle_move。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            payload (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
                normalized_vertex_ref = self._normalize_vertex_ref(annotation, self.selected_vertex_index)
                self.selected_vertex_index = normalized_vertex_ref
                current_point = self._current_vertex_point(annotation.id, normalized_vertex_ref)
                trial = annotation.clone()
                self._update_ring_vertex(trial, normalized_vertex_ref, x, y)
                free_drag_valid = GeometryService.is_annotation_geometry_valid(trial)
                actual_x, actual_y = (x, y) if free_drag_valid else (
                    (current_point[0], current_point[1]) if current_point is not None else (x, y)
                )

                updated = annotation.clone()
                snapped_x, snapped_y, snap_meta = self._apply_snapping(actual_x, actual_y, annotation.id, normalized_vertex_ref)
                if snap_meta is None:
                    self.snap_indicator_changed.emit(None, None)
                else:
                    self.snap_indicator_changed.emit(snap_meta["type"], snap_meta["position"])
                self._update_ring_vertex(updated, normalized_vertex_ref, snapped_x, snapped_y)
                self.selected_vertex_index = normalized_vertex_ref
                if not GeometryService.is_annotation_geometry_valid(updated):
                    updated = trial if free_drag_valid else annotation.clone()
                    if not free_drag_valid and not GeometryService.is_annotation_geometry_valid(updated):
                        return
                GeometryService.refresh_annotation_metadata(updated)
                self._drag_last_annotation = updated.clone()
                self._last_edited_vertex_ref = normalized_vertex_ref
                self.geometry_changed.emit(annotation.id, updated)
                return
            if self._distance(self._selection_box_start, [x, y]) >= 3:
                self._selection_box_active = True
                x0, y0 = self._selection_box_start
                self.draft_changed.emit("selection_box", GeometryService.rectangle_to_polygon(x0, y0, x, y))

    def handle_release(self, payload) -> None:
        """handle_release。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            payload (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
                    final_annotation = self._drag_last_annotation.clone()
                    if self.selected_vertex_index is not None:
                        current_annotation = self._find_annotation(self.selected_annotation_id)
                        if current_annotation is not None:
                            self.selected_vertex_index = self._normalize_vertex_ref(current_annotation, self.selected_vertex_index)
                        snapped_x, snapped_y, snap_meta = self._apply_snapping(
                            x, y, self.selected_annotation_id, self.selected_vertex_index
                        )
                        if snap_meta is not None:
                            self._update_ring_vertex(final_annotation, self.selected_vertex_index, snapped_x, snapped_y)
                            if GeometryService.is_annotation_geometry_valid(final_annotation):
                                GeometryService.refresh_annotation_metadata(final_annotation)
                                self._drag_last_annotation = final_annotation.clone()
                    
                    if self.selected_vertex_index is not None:
                        self._last_edited_vertex_ref = self.selected_vertex_index            
                                
                    self.geometry_committed.emit(
                        self.selected_annotation_id,
                        self._drag_origin_annotation.clone(),
                        self._drag_last_annotation.clone(),
                    )
                self._selection_box_start = None
                self._selection_box_active = False
                self._selection_box_additive = False
                self._selection_box_click_target = None
                self.snap_indicator_changed.emit(None, None)
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
        self.snap_indicator_changed.emit(None, None)

    def finish_polygon(self) -> None:
        """finish_polygon。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        复杂度:
            时间和空间复杂度与输入规模线性或近线性相关。
        """
        if len(self._polygon_points) < 3:
            self._polygon_points = []
            self.draft_changed.emit("clear", None)
            return
        polygon = GeometryService.ensure_closed(self._polygon_points)
        self._polygon_points = []
        self.draft_changed.emit("clear", None)
        self.polygon_finished.emit(polygon)

    def delete_selected(self) -> str | None:
        """delete_selected。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            str | None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        selected = self.selected_annotation_id
        self.selected_annotation_id = None
        self.selected_annotation_ids.clear()
        self.selected_vertex_index = None
        self.selection_changed.emit(set())
        self._last_edited_vertex_ref = None
        return selected

    def remove_selected_vertex(self) -> AnnotationObject | None:
        """remove_selected_vertex。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            AnnotationObject | None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
        """editable_annotation_id。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            str | None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if self.active_tool != self.TOOL_BROWSE:
            return None
        if len(self.selected_annotation_ids) != 1 or self.selected_annotation_id is None:
            return None
        return self.selected_annotation_id

    def is_node_edit_active(self) -> bool:
        """is_node_edit_active。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            bool: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        return self.editable_annotation_id() is not None

    def _append_polygon_point(self, x: float, y: float) -> None:
        """_append_polygon_point。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            x (float): 输入参数。
            y (float): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        复杂度:
            时间和空间复杂度与输入规模线性或近线性相关。
        """
        point = [x, y]
        if not self._polygon_points or self._distance(self._polygon_points[-1], point) >= 1:
            self._polygon_points.append(point)

    def _handle_selection_press(self, x: float, y: float, additive: bool = False) -> None:
        """_handle_selection_press。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            x (float): 输入参数。
            y (float): 输入参数。
            additive (bool): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
        """_find_annotation。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotation_id (str | None): 输入参数。
        返回:
            AnnotationObject | None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if annotation_id is None:
            return None
        for annotation in self.annotations:
            if annotation.id == annotation_id:
                return annotation
        return None

    def _hit_vertex(self, annotation: AnnotationObject, x: float, y: float):
        """_hit_vertex。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotation (AnnotationObject): 输入参数。
            x (float): 输入参数。
            y (float): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        tolerance = self._screen_distance_to_image("vertex", fallback=6.0)
        candidates = []

        for index, point in enumerate(annotation.exterior[:-1]):
            dist = self._distance(point, [x, y])
            if dist <= tolerance:
                candidates.append((dist, ("exterior", -1, index)))

        for hole_index, hole in enumerate(annotation.holes):
            for index, point in enumerate(hole[:-1]):
                dist = self._distance(point, [x, y])
                if dist <= tolerance:
                    candidates.append((dist, ("hole", hole_index, index)))

        if not candidates:
            return None

        # 先按距离排序
        candidates.sort(key=lambda item: item[0])

        # 1. 优先返回当前选中的顶点
        if annotation.id == self.selected_annotation_id and self.selected_vertex_index is not None:
            normalized_selected = self._normalize_vertex_ref(annotation, self.selected_vertex_index)
            for _, ref in candidates:
                if ref == normalized_selected:
                    return ref

        # 2. 再优先返回最近一次编辑的顶点
        if annotation.id == self.selected_annotation_id and self._last_edited_vertex_ref is not None:
            normalized_last = self._normalize_vertex_ref(annotation, self._last_edited_vertex_ref)
            for _, ref in candidates:
                if ref == normalized_last:
                    return ref

        # 3. 否则返回最近的那个
        return candidates[0][1]

    def _hit_segment(self, annotation: AnnotationObject, x: float, y: float):
        """_hit_segment。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotation (AnnotationObject): 输入参数。
            x (float): 输入参数。
            y (float): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        rings = [("exterior", -1, annotation.exterior)] + [
            ("hole", idx, hole) for idx, hole in enumerate(annotation.holes)
        ]
        for ring_type, hole_index, points in rings:
            for index in range(len(points) - 1):
                dist = self._distance_to_segment([x, y], points[index], points[index + 1])
                if dist <= self._screen_distance_to_image("edge", fallback=4.0):
                    return (ring_type, hole_index, index)
        return None

    def _handle_right_click_insert(self, x: float, y: float) -> None:
        """_handle_right_click_insert。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            x (float): 输入参数。
            y (float): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
        """_distance_to_segment。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            point (Any): 输入参数。
            start (Any): 输入参数。
            end (Any): 输入参数。
        返回:
            float: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
        """_distance。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            p1 (Any): 输入参数。
            p2 (Any): 输入参数。
        返回:
            float: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        return math.hypot(p1[0] - p2[0], p1[1] - p2[1])

    def _apply_snapping(self, x: float, y: float, annotation_id: str, vertex_ref) -> tuple[float, float, dict | None]:
        """_apply_snapping。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            x (float): 输入参数。
            y (float): 输入参数。
            annotation_id (str): 输入参数。
            vertex_ref (Any): 输入参数。
        返回:
            tuple[float, float, dict | None]: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        snapped_x, snapped_y = x, y
        vertex_tolerance = self._screen_distance_to_image("vertex")
        edge_tolerance = self._screen_distance_to_image("edge")

        connected_edges = self._connected_segment_refs(annotation_id, vertex_ref)
        best_vertex_distance = float("inf")
        best_vertex = None

        for annotation in self.annotations:
            rings = [("exterior", -1, annotation.exterior)] + [
                ("hole", idx, hole) for idx, hole in enumerate(annotation.holes)
            ]
            for ring_type, hole_index, ring in rings:
                for index, point in enumerate(ring[:-1]):
                    if annotation.id == annotation_id:
                        current_ref = self._normalize_vertex_ref(annotation, vertex_ref)
                        candidate_ref = (ring_type, hole_index, index)
                        if candidate_ref == current_ref:
                            continue
                    distance = self._distance(point, [x, y])
                    if distance < best_vertex_distance and distance <= vertex_tolerance:
                        best_vertex_distance = distance
                        best_vertex = point

        if best_vertex is not None:
            return best_vertex[0], best_vertex[1], {
                "type": "vertex",
                "position": (best_vertex[0], best_vertex[1]),
            }

        best_distance = float("inf")
        snap_meta = None

        for annotation in self.annotations:
            rings = [("exterior", -1, annotation.exterior)] + [("hole", idx, hole) for idx, hole in enumerate(annotation.holes)]
            for ring_type, hole_index, ring in rings:
                for index in range(len(ring) - 1):
                    start = ring[index]
                    end = ring[index + 1]
                    segment_ref = (annotation.id, ring_type, hole_index, index)
                    if segment_ref in connected_edges:
                        continue
                    projection = self._project_point_to_segment([x, y], start, end)
                    if projection is None:
                        continue
                    distance = self._distance(projection, [x, y])
                    if distance < best_distance and distance <= edge_tolerance:
                        snapped_x, snapped_y = projection[0], projection[1]
                        best_distance = distance
                        snap_meta = {"type": "edge", "position": (projection[0], projection[1])}

        return snapped_x, snapped_y, snap_meta

    def _project_point_to_segment(self, point, start, end):
        """_project_point_to_segment。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            point (Any): 输入参数。
            start (Any): 输入参数。
            end (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
        """_clear_selection_for_new_drawing。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if self.selected_annotation_ids or self.selected_vertex_index is not None:
            self.selected_annotation_id = None
            self.selected_annotation_ids.clear()
            self.selected_vertex_index = None
            self._dragging_vertex = False
            self.selection_changed.emit(set())
            self._last_edited_vertex_ref = None
        self.snap_indicator_changed.emit(None, None)

    def select_all(self) -> None:
        """select_all。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self.selected_annotation_ids = {annotation.id for annotation in self.annotations}
        self.selected_annotation_id = next(iter(self.selected_annotation_ids), None)
        self.selected_vertex_index = None
        self._dragging_vertex = False
        self.selection_changed.emit(set(self.selected_annotation_ids))
        self.snap_indicator_changed.emit(None, None)

    def _apply_click_selection(self, annotation_id: str | None, additive: bool) -> None:
        """_apply_click_selection。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotation_id (str | None): 输入参数。
            additive (bool): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
        self.snap_indicator_changed.emit(None, None)
        self._last_edited_vertex_ref = None

    def _apply_box_selection(self, start: tuple[float, float], end: tuple[float, float], additive: bool) -> None:
        """_apply_box_selection。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            start (tuple[float, float]): 输入参数。
            end (tuple[float, float]): 输入参数。
            additive (bool): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
        self.snap_indicator_changed.emit(None, None)

    def _get_ring_points(self, annotation: AnnotationObject, vertex_ref):
        """_get_ring_points。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotation (AnnotationObject): 输入参数。
            vertex_ref (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        ring_type, hole_index, _ = vertex_ref
        return annotation.exterior if ring_type == "exterior" else annotation.holes[hole_index]

    def _set_ring_points(self, annotation: AnnotationObject, vertex_ref, points: list[list[float]]) -> None:
        """_set_ring_points。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotation (AnnotationObject): 输入参数。
            vertex_ref (Any): 输入参数。
            points (list[list[float]]): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        ring_type, hole_index, _ = vertex_ref
        if ring_type == "exterior":
            annotation.exterior = points
        else:
            annotation.holes[hole_index] = points

    def _update_ring_vertex(self, annotation: AnnotationObject, vertex_ref, x: float, y: float) -> None:
        """_update_ring_vertex。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotation (AnnotationObject): 输入参数。
            vertex_ref (Any): 输入参数。
            x (float): 输入参数。
            y (float): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        ring_type, hole_index, vertex_index = self._normalize_vertex_ref(annotation, vertex_ref)
        points = [point[:] for point in self._get_ring_points(annotation, (ring_type, hole_index, vertex_index))]
        if not points:
            return
        if len(points) == 1:
            self._set_ring_points(annotation, (ring_type, hole_index, vertex_index), [[x, y]])
            return

        unique_points = [point[:] for point in points[:-1]]
        if not unique_points:
            return

        vertex_index = max(0, min(vertex_index, len(unique_points) - 1))
        unique_points[vertex_index] = [x, y]

        # 闭合环始终显式追加首点，避免当首点与其它点重合时被 ensure_closed 意外压缩。
        closed_points = unique_points + [unique_points[0][:]]
        self._set_ring_points(annotation, (ring_type, hole_index, vertex_index), closed_points)

    def _insert_ring_vertex(self, annotation: AnnotationObject, segment_ref, x: float, y: float) -> None:
        """_insert_ring_vertex。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotation (AnnotationObject): 输入参数。
            segment_ref (Any): 输入参数。
            x (float): 输入参数。
            y (float): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        ring_type, hole_index, segment_index = segment_ref
        base_ref = (ring_type, hole_index, 0)
        points = [point[:] for point in self._get_ring_points(annotation, base_ref)[:-1]]
        points.insert(segment_index + 1, [x, y])
        self._set_ring_points(annotation, base_ref, GeometryService.ensure_closed(points))

    def _remove_ring_vertex(self, annotation: AnnotationObject, vertex_ref) -> bool:
        """_remove_ring_vertex。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotation (AnnotationObject): 输入参数。
            vertex_ref (Any): 输入参数。
        返回:
            bool: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        base_ref = (vertex_ref[0], vertex_ref[1], 0)
        points = [point[:] for point in self._get_ring_points(annotation, base_ref)[:-1]]
        if len(points) <= 3:
            return False
        del points[vertex_ref[2]]
        self._set_ring_points(annotation, base_ref, GeometryService.ensure_closed(points))
        return True

    def _screen_distance_to_image(self, snap_type: str, fallback: float | None = None) -> float:
        """_screen_distance_to_image。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            snap_type (str): 输入参数。
            fallback (float | None): 输入参数。
        返回:
            float: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        base = self._screen_snap_tolerance.get(snap_type, fallback if fallback is not None else 8.0)
        pixel_scale = max(
            float(getattr(self._viewport_state, "scale_x", 1.0) or 1.0),
            float(getattr(self._viewport_state, "scale_y", 1.0) or 1.0),
        )
        return max(base * pixel_scale, pixel_scale)

    def _connected_segment_refs(self, annotation_id: str, vertex_ref) -> set[tuple[str, str, int, int]]:
        """_connected_segment_refs。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotation_id (str): 输入参数。
            vertex_ref (Any): 输入参数。
        返回:
            set[tuple[str, str, int, int]]: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        annotation = self._find_annotation(annotation_id)
        if annotation is None:
            return set()
        ring_type, hole_index, vertex_index = vertex_ref
        ring = annotation.exterior if ring_type == "exterior" else annotation.holes[hole_index]
        if len(ring) < 2:
            return set()
        unique_count = max(len(ring) - 1, 1)
        prev_index = (vertex_index - 1) % unique_count
        next_index = vertex_index % unique_count
        return {
            (annotation_id, ring_type, hole_index, prev_index),
            (annotation_id, ring_type, hole_index, next_index),
        }

    def _current_vertex_point(self, annotation_id: str, vertex_ref):
        """_current_vertex_point。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotation_id (str): 输入参数。
            vertex_ref (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        annotation = self._find_annotation(annotation_id)
        if annotation is None:
            return None
        ring_type, hole_index, vertex_index = self._normalize_vertex_ref(annotation, vertex_ref)
        ring = annotation.exterior if ring_type == "exterior" else annotation.holes[hole_index]
        if 0 <= vertex_index < len(ring):
            return ring[vertex_index]
        return None

    def _normalize_vertex_ref(self, annotation: AnnotationObject, vertex_ref):
        """_normalize_vertex_ref。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotation (AnnotationObject): 输入参数。
            vertex_ref (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        ring_type, hole_index, vertex_index = vertex_ref
        ring = annotation.exterior if ring_type == "exterior" else annotation.holes[hole_index]
        if not ring:
            return ring_type, hole_index, 0
        unique_count = max(len(ring) - 1, 1)
        normalized_index = max(0, min(int(vertex_index), unique_count - 1))
        return ring_type, hole_index, normalized_index
