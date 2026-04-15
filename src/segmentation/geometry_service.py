"""
几何修复与掩膜互转。
"""

from __future__ import annotations

from typing import Iterable

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

try:
    from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
    from shapely.ops import unary_union
    from shapely.geometry.base import BaseGeometry
except Exception:  # pragma: no cover
    Polygon = None
    MultiPolygon = None
    GeometryCollection = None
    unary_union = None
    BaseGeometry = None

from .models import AnnotationObject


class GeometryService:
    @staticmethod
    def rectangle_to_polygon(x1: float, y1: float, x2: float, y2: float) -> list[list[float]]:
        left, right = sorted((x1, x2))
        top, bottom = sorted((y1, y2))
        return [
            [left, top],
            [right, top],
            [right, bottom],
            [left, bottom],
            [left, top],
        ]

    @staticmethod
    def ensure_closed(points: list[list[float]]) -> list[list[float]]:
        if not points:
            return []
        if points[0] != points[-1]:
            return points + [points[0]]
        return points

    @staticmethod
    def _polygon_from_ring(exterior: list[list[float]], holes: list[list[list[float]]] | None = None):
        if Polygon is None:
            return None
        polygon = Polygon(exterior, holes or [])
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        return polygon

    @staticmethod
    def annotation_to_polygon(annotation: AnnotationObject):
        return GeometryService._polygon_from_ring(annotation.exterior, annotation.holes)

    @staticmethod
    def annotations_union(annotations: Iterable[AnnotationObject]):
        polygons = [GeometryService.annotation_to_polygon(item) for item in annotations]
        polygons = [item for item in polygons if item is not None and not item.is_empty]
        if not polygons or unary_union is None:
            return None
        return unary_union(polygons)

    @staticmethod
    def polygon_to_annotation_objects(polygon, label_id: int, source_tool: str) -> list[AnnotationObject]:
        if Polygon is None or polygon is None or polygon.is_empty:
            return []
        polygons = [polygon] if isinstance(polygon, Polygon) else list(polygon.geoms)
        results = []
        for item in polygons:
            exterior = [[float(x), float(y)] for x, y in item.exterior.coords]
            holes = [
                [[float(x), float(y)] for x, y in interior.coords]
                for interior in item.interiors
            ]
            results.append(
                AnnotationObject.from_polygon(
                    label_id=label_id,
                    exterior=exterior,
                    holes=holes,
                    source_tool=source_tool,
                )
            )
        return results

    @staticmethod
    def mask_to_annotations(
        mask: np.ndarray,
        bbox: tuple[int, int, int, int],
        label_id: int,
        simplify: bool = True,
        vector_smoothness: int = 0,
        source_tool: str = "magic_wand",
    ) -> list[AnnotationObject]:
        if cv2 is None:
            raise RuntimeError("mask_to_annotations 需要 opencv-python 依赖")

        binary = (mask > 0).astype(np.uint8)
        contours, hierarchy = cv2.findContours(binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None:
            return []

        x0, y0, _, _ = bbox
        polygons = []
        for index, contour in enumerate(contours):
            if hierarchy[0][index][3] != -1:
                continue
            exterior = [
                [float(point[0][0] + x0), float(point[0][1] + y0)]
                for point in contour
            ]
            exterior = GeometryService._postprocess_ring(exterior, simplify, vector_smoothness)
            holes = []
            child = hierarchy[0][index][2]
            while child != -1:
                hole_contour = contours[child]
                holes.append(
                    GeometryService._postprocess_ring(
                        [
                            [float(point[0][0] + x0), float(point[0][1] + y0)]
                            for point in hole_contour
                        ],
                        simplify,
                        vector_smoothness,
                    )
                )
                child = hierarchy[0][child][0]

            polygon = GeometryService._polygon_from_ring(exterior, holes)
            polygons.extend(
                GeometryService.polygon_to_annotation_objects(polygon, label_id, source_tool)
            )

        return polygons

    @staticmethod
    def rasterize_annotations(
        annotations: Iterable[AnnotationObject],
        width: int,
        height: int,
        binary_label_id: int | None = None,
    ) -> np.ndarray:
        if cv2 is None:
            raise RuntimeError("rasterize_annotations 需要 opencv-python 依赖")

        mask = np.zeros((height, width), dtype=np.uint16)
        for annotation in annotations:
            value = binary_label_id if binary_label_id is not None else annotation.label_id
            exterior = np.array(annotation.exterior, dtype=np.int32)
            if exterior.size == 0:
                continue
            cv2.fillPoly(mask, [exterior], int(value))
            for hole in annotation.holes:
                interior = np.array(hole, dtype=np.int32)
                if interior.size > 0:
                    cv2.fillPoly(mask, [interior], 0)
        return mask

    @staticmethod
    def bbox_intersects(bbox_a: list[float] | tuple[float, float, float, float] | None, bbox_b: tuple[float, float, float, float] | None) -> bool:
        if bbox_a is None or bbox_b is None:
            return False
        ax0, ay0, ax1, ay1 = bbox_a
        bx0, by0, bw, bh = bbox_b
        bx1 = bx0 + bw
        by1 = by0 + bh
        return not (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0)

    @staticmethod
    def _postprocess_ring(points: list[list[float]], simplify: bool, vector_smoothness: int) -> list[list[float]]:
        if not points:
            return []
        ring = GeometryService.ensure_closed(points)
        if vector_smoothness > 0 and len(ring) > 4:
            ring = GeometryService._smooth_ring(ring, vector_smoothness)
        if simplify and len(ring) > 4:
            ring = GeometryService._simplify_ring(ring)
        return GeometryService.ensure_closed(ring[:-1] if ring and ring[0] == ring[-1] else ring)

    @staticmethod
    def _smooth_ring(points: list[list[float]], radius: int) -> list[list[float]]:
        closed = GeometryService.ensure_closed(points)
        coords = np.array(closed[:-1], dtype=np.float32)
        if len(coords) < 4:
            return closed
        kernel_radius = max(1, radius)
        padded = np.concatenate([coords[-kernel_radius:], coords, coords[:kernel_radius]], axis=0)
        kernel_size = kernel_radius * 2 + 1
        xs = cv2.GaussianBlur(padded[:, 0].reshape(-1, 1), (kernel_size, 1), 0).reshape(-1)
        ys = cv2.GaussianBlur(padded[:, 1].reshape(-1, 1), (kernel_size, 1), 0).reshape(-1)
        smoothed = np.stack([xs[kernel_radius:-kernel_radius], ys[kernel_radius:-kernel_radius]], axis=1)
        return GeometryService.ensure_closed(smoothed.tolist())

    @staticmethod
    def _simplify_ring(points: list[list[float]]) -> list[list[float]]:
        contour = np.array(points, dtype=np.float32).reshape(-1, 1, 2)
        epsilon = 1.0
        simplified = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2).tolist()
        return GeometryService.ensure_closed(simplified)

    @staticmethod
    def colorize_mask(mask: np.ndarray, label_lookup: dict[int, object]) -> np.ndarray:
        rgba = np.zeros((mask.shape[0], mask.shape[1], 4), dtype=np.uint8)
        for label_id, label in label_lookup.items():
            if label_id == 0:
                continue
            if hasattr(label, "visible") and not label.visible:
                continue
            color = label.color.lstrip("#")
            if len(color) != 6:
                continue
            rgb = [int(color[i:i + 2], 16) for i in (0, 2, 4)]
            rgba[mask == label_id] = [rgb[0], rgb[1], rgb[2], 96]
        return rgba

    @staticmethod
    def merge_mask_bbox(
        base_mask: np.ndarray | None,
        base_bbox: tuple[int, int, int, int] | None,
        incoming_mask: np.ndarray,
        incoming_bbox: tuple[int, int, int, int],
        mode: str,
    ) -> tuple[np.ndarray, tuple[int, int, int, int]]:
        if base_mask is None or base_bbox is None or mode == "replace":
            return incoming_mask.copy(), incoming_bbox

        bx, by, bw, bh = base_bbox
        ix, iy, iw, ih = incoming_bbox
        min_x = min(bx, ix)
        min_y = min(by, iy)
        max_x = max(bx + bw, ix + iw)
        max_y = max(by + bh, iy + ih)
        width = max_x - min_x
        height = max_y - min_y

        merged_base = np.zeros((height, width), dtype=np.uint8)
        merged_incoming = np.zeros((height, width), dtype=np.uint8)
        merged_base[by - min_y:by - min_y + bh, bx - min_x:bx - min_x + bw] = (base_mask > 0).astype(np.uint8)
        merged_incoming[iy - min_y:iy - min_y + ih, ix - min_x:ix - min_x + iw] = (incoming_mask > 0).astype(np.uint8)

        if mode == "add":
            merged = np.maximum(merged_base, merged_incoming)
        elif mode == "subtract":
            merged = merged_base.copy()
            merged[merged_incoming > 0] = 0
        elif mode == "intersect":
            merged = ((merged_base > 0) & (merged_incoming > 0)).astype(np.uint8)
        else:
            merged = merged_incoming

        ys, xs = np.where(merged > 0)
        if len(xs) == 0 or len(ys) == 0:
            return np.zeros((1, 1), dtype=np.uint8), (0, 0, 0, 0)
        out_x = int(xs.min())
        out_y = int(ys.min())
        out_w = int(xs.max() - out_x + 1)
        out_h = int(ys.max() - out_y + 1)
        cropped = merged[out_y:out_y + out_h, out_x:out_x + out_w].astype(np.uint8)
        return cropped, (min_x + out_x, min_y + out_y, out_w, out_h)
