"""
MVP 魔法棒算法。
"""

from __future__ import annotations

from collections import deque

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

from ..geometry_service import GeometryService
from ..models import MagicWandParams, PreviewSelection
from .base import BaseSegmenter


class MagicWandSegmenter(BaseSegmenter):
    def run(
        self,
        image: np.ndarray,
        seed_point: tuple[int, int],
        params: MagicWandParams,
    ) -> PreviewSelection:
        if cv2 is None:
            raise RuntimeError("魔法棒需要 opencv-python 依赖")

        if image.ndim == 3:
            working = cv2.cvtColor(image.astype(np.uint8), cv2.COLOR_RGB2GRAY)
        else:
            working = image.astype(np.float32)
        working = self._normalize(working)
        mask = self._region_grow(working, seed_point, params)

        if params.fill_holes:
            mask = self._fill_holes(mask)
        if params.smooth_radius > 0:
            kernel_size = params.smooth_radius * 2 + 1
            kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        ys, xs = np.where(mask > 0)
        if len(xs) == 0 or len(ys) == 0:
            bbox = (0, 0, 0, 0)
            contours = []
            polygon_preview = []
        else:
            x0, x1 = int(xs.min()), int(xs.max())
            y0, y1 = int(ys.min()), int(ys.max())
            bbox = (x0, y0, x1 - x0 + 1, y1 - y0 + 1)
            cropped = mask[y0 : y1 + 1, x0 : x1 + 1]
            polygon_preview = GeometryService.mask_to_annotations(
                cropped,
                bbox,
                label_id=1,
                min_area=params.min_area,
                smooth_radius=0,
                source_tool="magic_wand_preview",
            )
            contours = [annotation.exterior for annotation in polygon_preview]

        return PreviewSelection(
            seed_point=seed_point,
            params=params,
            bbox=bbox,
            mask=mask,
            contours=contours,
            polygon_preview=polygon_preview,
        )

    def _normalize(self, image: np.ndarray) -> np.ndarray:
        image = image.astype(np.float32)
        min_val = float(np.nanmin(image))
        max_val = float(np.nanmax(image))
        if max_val - min_val < 1e-6:
            return np.zeros_like(image, dtype=np.uint8)
        return (((image - min_val) / (max_val - min_val)) * 255).astype(np.uint8)

    def _region_grow(
        self,
        image: np.ndarray,
        seed_point: tuple[int, int],
        params: MagicWandParams,
    ) -> np.ndarray:
        height, width = image.shape[:2]
        sx, sy = seed_point
        if not (0 <= sx < width and 0 <= sy < height):
            return np.zeros((height, width), dtype=np.uint8)

        tolerance = max(0, int(params.tolerance))
        seed_value = int(image[sy, sx])
        mask = np.zeros((height, width), dtype=np.uint8)
        queue = deque([(sx, sy)])
        mask[sy, sx] = 1
        if params.connectivity == 4:
            directions = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        else:
            directions = [
                (1, 0), (-1, 0), (0, 1), (0, -1),
                (1, 1), (1, -1), (-1, 1), (-1, -1),
            ]

        while queue:
            x, y = queue.popleft()
            for dx, dy in directions:
                nx, ny = x + dx, y + dy
                if not (0 <= nx < width and 0 <= ny < height):
                    continue
                if mask[ny, nx]:
                    continue
                if abs(int(image[ny, nx]) - seed_value) <= tolerance:
                    mask[ny, nx] = 1
                    queue.append((nx, ny))

        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, params.connectivity)
        filtered = np.zeros_like(mask)
        seed_component = labels[sy, sx]
        for label in range(1, num_labels):
            area = stats[label, cv2.CC_STAT_AREA]
            if area < params.min_area:
                continue
            if params.seed_only and label != seed_component:
                continue
            filtered[labels == label] = 1
        return filtered

    def _fill_holes(self, mask: np.ndarray) -> np.ndarray:
        flood = mask.copy().astype(np.uint8)
        height, width = flood.shape[:2]
        fill_mask = np.zeros((height + 2, width + 2), dtype=np.uint8)
        cv2.floodFill(flood, fill_mask, (0, 0), 1)
        inverted = 1 - flood
        return np.maximum(mask, inverted).astype(np.uint8)
