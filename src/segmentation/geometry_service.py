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
    from shapely.geometry import MultiPolygon, Polygon
except Exception:  # pragma: no cover
    Polygon = None
    MultiPolygon = None

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
        min_area: int = 16,
        smooth_radius: int = 0,
        source_tool: str = "magic_wand",
    ) -> list[AnnotationObject]:
        if cv2 is None:
            raise RuntimeError("mask_to_annotations 需要 opencv-python 依赖")

        binary = (mask > 0).astype(np.uint8)
        if smooth_radius > 0:
            kernel_size = max(1, smooth_radius * 2 + 1)
            kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
            binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
            binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

        contours, hierarchy = cv2.findContours(binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None:
            return []

        x0, y0, _, _ = bbox
        polygons = []
        for index, contour in enumerate(contours):
            if cv2.contourArea(contour) < min_area:
                continue
            if hierarchy[0][index][3] != -1:
                continue
            exterior = [
                [float(point[0][0] + x0), float(point[0][1] + y0)]
                for point in contour
            ]
            exterior = GeometryService.ensure_closed(exterior)
            holes = []
            child = hierarchy[0][index][2]
            while child != -1:
                hole_contour = contours[child]
                if cv2.contourArea(hole_contour) >= min_area:
                    holes.append(
                        GeometryService.ensure_closed(
                            [
                                [float(point[0][0] + x0), float(point[0][1] + y0)]
                                for point in hole_contour
                            ]
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
