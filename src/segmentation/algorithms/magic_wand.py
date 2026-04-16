"""
基于种子点颜色相似性与连通性的区域生长魔法棒。
"""

from __future__ import annotations

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

from ..geometry_service import GeometryService
from ..models import MagicWandParams, PreviewSelection
from .base import BaseSegmenter


class MagicWandSegmenter(BaseSegmenter):
    def __init__(self):
        self._flood_mask: np.ndarray | None = None
        self._flood_mask_shape: tuple[int, int] | None = None

    def run(
        self,
        image: np.ndarray,
        seed_point: tuple[int, int],
        params: MagicWandParams,
    ) -> PreviewSelection:
        if cv2 is None:
            raise RuntimeError("魔法棒需要 opencv-python 依赖")

        height, width = image.shape[:2]
        sx, sy = seed_point
        if not (0 <= sx < width and 0 <= sy < height):
            return PreviewSelection(seed_point, params, (0, 0, 0, 0), np.zeros((1, 1), dtype=np.uint8))

        working = self.prepare_image(image, params)
        return self.run_prepared(working, seed_point, params)

    def run_prepared(
        self,
        prepared_image: np.ndarray,
        seed_point: tuple[int, int],
        params: MagicWandParams,
    ) -> PreviewSelection:
        if cv2 is None:
            raise RuntimeError("魔法棒需要 opencv-python 依赖")

        height, width = prepared_image.shape[:2]
        sx, sy = seed_point
        if not (0 <= sx < width and 0 <= sy < height):
            return PreviewSelection(seed_point, params, (0, 0, 0, 0), np.zeros((1, 1), dtype=np.uint8))

        mask = self._grow_region(prepared_image, seed_point, params)
        area = int(mask.sum())
        if area < params.min_area:
            return PreviewSelection(seed_point, params, (0, 0, 0, 0), np.zeros((1, 1), dtype=np.uint8))

        ys, xs = np.where(mask > 0)
        if len(xs) == 0 or len(ys) == 0:
            return PreviewSelection(seed_point, params, (0, 0, 0, 0), np.zeros((1, 1), dtype=np.uint8))

        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        local_binary = mask[y0:y1 + 1, x0:x1 + 1].astype(np.uint8)
        if params.fill_all_holes:
            local_binary = GeometryService.fill_all_holes(local_binary)
        elif params.fill_small_holes:
            local_binary = GeometryService.fill_small_holes(local_binary, params.min_area)
        alpha_mask = np.where(local_binary > 0, 255, 0).astype(np.uint8)
        bbox = (x0, y0, x1 - x0 + 1, y1 - y0 + 1)
        return PreviewSelection(
            seed_point=seed_point,
            params=params,
            bbox=bbox,
            mask=alpha_mask,
            contours=[],
            polygon_preview=[],
        )

    def prepare_image(self, image: np.ndarray, params: MagicWandParams) -> np.ndarray:
        return self._prepare_image(image, params)

    def _prepare_image(self, image: np.ndarray, params: MagicWandParams) -> np.ndarray:
        if image.ndim == 2:
            base = image[..., None]
        else:
            base = image

        mode = params.similarity_mode
        rgb = base[..., :3] if base.shape[-1] >= 3 else np.repeat(base[..., :1], 3, axis=-1)
        if mode == "rgb":
            return rgb
        if mode in {"r", "g", "b"}:
            index = {"r": 0, "g": 1, "b": 2}[mode]
            return rgb[..., index:index + 1]
        if mode in {"h", "s", "v"}:
            hsv = cv2.cvtColor(self._as_uint8_contiguous(rgb), cv2.COLOR_RGB2HSV)
            index = {"h": 0, "s": 1, "v": 2}[mode]
            return hsv[..., index:index + 1]
        return rgb

    def _grow_region(self, image: np.ndarray, seed_point: tuple[int, int], params: MagicWandParams) -> np.ndarray:
        threshold = int(max(0.0, float(params.tolerance)))
        if threshold > 0:
            threshold += 1
        return self._grow_region_flood_fill(image, seed_point, threshold, params.connectivity)

    def _grow_region_flood_fill(self, image: np.ndarray, seed_point: tuple[int, int], threshold: int, connectivity: int) -> np.ndarray:
        height, width = image.shape[:2]
        work = self._as_uint8_contiguous(image)
        flood_mask = self._reusable_flood_mask(height, width)
        flags = connectivity | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8)
        if work.ndim == 2 or (work.ndim == 3 and work.shape[2] == 1):
            diff = threshold
        else:
            channels = work.shape[2]
            diff = tuple([threshold] * channels)
        cv2.floodFill(work.copy(), flood_mask, seedPoint=seed_point, newVal=0, loDiff=diff, upDiff=diff, flags=flags)
        return (flood_mask[1:-1, 1:-1] > 0).astype(np.uint8)

    def _as_uint8_contiguous(self, image: np.ndarray) -> np.ndarray:
        if image.dtype == np.uint8 and image.flags["C_CONTIGUOUS"]:
            return image
        if image.dtype == np.uint8:
            return np.ascontiguousarray(image)
        return np.ascontiguousarray(image.astype(np.uint8, copy=False))

    def _reusable_flood_mask(self, height: int, width: int) -> np.ndarray:
        shape = (height + 2, width + 2)
        if self._flood_mask is None or self._flood_mask_shape != shape:
            self._flood_mask = np.zeros(shape, dtype=np.uint8)
            self._flood_mask_shape = shape
        else:
            self._flood_mask.fill(0)
        return self._flood_mask
