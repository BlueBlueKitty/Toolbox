"""
几何修复与掩膜互转。
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

try:
    from osgeo import gdal, ogr, osr
except Exception:  # pragma: no cover
    gdal = None
    ogr = None
    osr = None

try:
    from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
    from shapely import wkb as shapely_wkb
    from shapely.affinity import translate as shapely_translate
    from shapely.ops import unary_union
    from shapely.geometry.base import BaseGeometry
    from shapely.validation import make_valid
except Exception:  # pragma: no cover
    Polygon = None
    MultiPolygon = None
    GeometryCollection = None
    shapely_wkb = None
    shapely_translate = None
    unary_union = None
    BaseGeometry = None
    make_valid = None

from .models import AnnotationObject


class GeometryService:
    _CV_SUBPIXEL_SHIFT = 8
    _SIMPLIFY_EPSILON = 1.0

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
        shell = GeometryService.ensure_closed(exterior)
        if len(shell) < 4:
            return None
        interior_rings = []
        if holes:
            for hole in holes:
                ring = GeometryService.ensure_closed(hole)
                if len(ring) >= 4:
                    interior_rings.append(ring)
        polygon = Polygon(shell, holes=interior_rings or None)
        if polygon.is_empty:
            return None
        if not polygon.is_valid:
            # print("警告: 构建多边形时发现无效几何，正在尝试修复...")
            polygon = make_valid(polygon) if make_valid is not None else polygon.buffer(0)
        return polygon

    @staticmethod
    def _simplify_polygon_geometry(geometry):
        if geometry is None or getattr(geometry, "is_empty", True):
            return geometry
        simplified = geometry.simplify(GeometryService._SIMPLIFY_EPSILON, preserve_topology=True)
        if simplified.is_empty:
            return geometry
        if not simplified.is_valid:
            simplified = make_valid(simplified) if make_valid is not None else simplified.buffer(0)
        return simplified

    @staticmethod
    def annotation_to_polygon(annotation: AnnotationObject):
        return GeometryService._polygon_from_ring(annotation.exterior, annotation.holes)

    @staticmethod
    def is_annotation_geometry_valid(annotation: AnnotationObject) -> bool:
        if Polygon is None:
            return bool(annotation.exterior and len(annotation.exterior) >= 4)
        shell = GeometryService.ensure_closed(annotation.exterior)
        if len(shell) < 4:
            return False
        holes = [GeometryService.ensure_closed(hole) for hole in annotation.holes]
        try:
            polygon = Polygon(shell, holes=holes or None)
        except Exception:
            return False
        return not polygon.is_empty and polygon.is_valid and polygon.area > 0

    @staticmethod
    def refresh_annotation_metadata(annotation: AnnotationObject) -> None:
        annotation.exterior = [[round(float(pt[0]), 3), round(float(pt[1]), 3)] for pt in annotation.exterior]
        annotation.holes = [
            [[round(float(pt[0]), 3), round(float(pt[1]), 3)] for pt in hole]
            for hole in annotation.holes
        ]
        if not annotation.exterior:
            annotation.bbox = None
            return
        xs = [pt[0] for pt in annotation.exterior]
        ys = [pt[1] for pt in annotation.exterior]
        annotation.bbox = [min(xs), min(ys), max(xs), max(ys)]
        annotation.updated_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

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
        polygons = GeometryService._extract_polygon_geometries(polygon)
        results = []
        for item in polygons:
            if not isinstance(item, Polygon) or item.is_empty:
                continue
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
    def _extract_polygon_geometries(geometry) -> list:
        if Polygon is None or geometry is None:
            return []
        if isinstance(geometry, Polygon):
            return [geometry]
        if isinstance(geometry, MultiPolygon):
            return list(geometry.geoms)
        if isinstance(geometry, GeometryCollection):
            polygons = []
            for geom in geometry.geoms:
                polygons.extend(GeometryService._extract_polygon_geometries(geom))
            return polygons
        return []

    @staticmethod
    def mask_to_annotations(
        mask: np.ndarray,
        bbox: tuple[int, int, int, int],
        label_id: int,
        simplify: bool = True,
        vector_smoothness: int = 0,
        connectivity: int = 8,
        source_tool: str = "magic_wand",
    ) -> list[AnnotationObject]:
        annotations = GeometryService._mask_to_annotations_gdal(
            mask,
            bbox,
            label_id,
            simplify,
            vector_smoothness,
            connectivity,
            source_tool,
        )
        if annotations is None:
            raise RuntimeError("mask_to_annotations 需要 GDAL 和 Shapely 依赖")
        return annotations

    @staticmethod
    def fill_small_holes(mask: np.ndarray, max_hole_area: int) -> np.ndarray:
        binary = (mask > 0).astype(np.uint8)
        if max_hole_area <= 0 or cv2 is None or binary.size == 0:
            return binary

        inverted = 1 - binary
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
            inverted, connectivity=8
        )

        if num_labels <= 1:
            return binary

        height, width = binary.shape[:2]

        # 直接从 stats 里取连通域属性，避免对每个连通域做 np.where
        left = stats[:, cv2.CC_STAT_LEFT]
        top = stats[:, cv2.CC_STAT_TOP]
        comp_width = stats[:, cv2.CC_STAT_WIDTH]
        comp_height = stats[:, cv2.CC_STAT_HEIGHT]
        area = stats[:, cv2.CC_STAT_AREA]

        # 是否接触图像边界：接触边界的不是“孔洞”，不能填
        touches_border = (
            (left == 0) |
            (top == 0) |
            (left + comp_width >= width) |
            (top + comp_height >= height)
        )

        # 需要填补的 label
        fill_lut = (area <= max_hole_area) & (~touches_border)

        # label 0 是 inverted 的背景，不处理
        fill_lut[0] = False

        # 一次性回填，避免每个 label 都扫描整张 labels 图
        binary[fill_lut[labels]] = 1

        return binary

    @staticmethod
    def fill_all_holes(mask: np.ndarray) -> np.ndarray:
        binary = (mask > 0).astype(np.uint8)
        if cv2 is None or binary.size == 0:
            return binary

        height, width = binary.shape[:2]
        padded = np.pad(binary, 1, mode="constant", constant_values=0)
        inverted = (1 - padded).astype(np.uint8)
        flood_mask = np.zeros((height + 4, width + 4), dtype=np.uint8)
        cv2.floodFill(inverted, flood_mask, seedPoint=(0, 0), newVal=0, flags=4)

        # 剩余的前景 1 即为被完全包围的孔洞，一次性并回原区域。
        holes = inverted[1:-1, 1:-1]
        if not np.any(holes):
            return binary
        filled = binary.copy()
        filled[holes > 0] = 1
        return filled

    @staticmethod
    def _mask_to_annotations_gdal(
        mask: np.ndarray,
        bbox: tuple[int, int, int, int],
        label_id: int,
        simplify: bool,
        vector_smoothness: int,
        connectivity: int,
        source_tool: str,
    ) -> list[AnnotationObject] | None:
        if gdal is None or ogr is None or shapely_wkb is None:
            return None
        try:
            binary = GeometryService._prepare_binary_mask(mask, vector_smoothness)
            height, width = binary.shape[:2]
            mem_driver = gdal.GetDriverByName("MEM")
            raster = mem_driver.Create("", width, height, 1, gdal.GDT_Byte)
            raster.GetRasterBand(1).WriteArray(binary)

            vector_driver = ogr.GetDriverByName("Memory")
            datasource = vector_driver.CreateDataSource("")
            layer = datasource.CreateLayer("mask", geom_type=ogr.wkbPolygon)
            field = ogr.FieldDefn("value", ogr.OFTInteger)
            layer.CreateField(field)
            polygonize_options = ["8CONNECTED=8"] if int(connectivity) == 8 else []
            gdal.Polygonize(raster.GetRasterBand(1), None, layer, 0, polygonize_options, callback=None)

            x0, y0, _, _ = bbox
            annotations = []
            layer.ResetReading()
            for feature in layer:
                if feature.GetField("value") != 1:
                    continue
                geom = feature.GetGeometryRef()
                if geom is None:
                    continue
                shapely_geom = shapely_wkb.loads(bytes(geom.ExportToWkb()))
                if shapely_translate is not None and (x0 != 0 or y0 != 0):
                    shapely_geom = shapely_translate(
                        shapely_geom,
                        xoff=x0,
                        yoff=y0,
                    )
                for polygon in GeometryService._extract_polygon_geometries(shapely_geom):
                    if polygon.is_empty:
                        continue
                    if not polygon.is_valid:
                        polygon = make_valid(polygon) if make_valid is not None else polygon.buffer(0)
                    if not simplify and vector_smoothness <= 0:
                        exterior = [[float(x), float(y)] for x, y in polygon.exterior.coords]
                        holes = [
                            [[float(x), float(y)] for x, y in ring.coords]
                            for ring in polygon.interiors
                        ]
                        annotations.append(
                            AnnotationObject.from_polygon(
                                label_id=label_id,
                                exterior=exterior,
                                holes=holes,
                                source_tool=source_tool,
                            )
                        )
                        continue
                    rebuilt = polygon
                    if simplify:
                        rebuilt = GeometryService._simplify_polygon_geometry(polygon)
                    annotations.extend(
                        GeometryService.polygon_to_annotation_objects(rebuilt, label_id, source_tool)
                    )
            return annotations
        except Exception:
            return None

    @staticmethod
    def rasterize_annotations(
        annotations: Iterable[AnnotationObject],
        width: int,
        height: int,
        binary_label_id: int | None = None,
    ) -> np.ndarray:
        if gdal is None or ogr is None or osr is None:
            raise RuntimeError("rasterize_annotations 需要 GDAL 依赖")

        mem_driver = gdal.GetDriverByName("MEM")
        raster = mem_driver.Create("", width, height, 1, gdal.GDT_UInt16)
        raster.SetGeoTransform((0.0, 1.0, 0.0, 0.0, 0.0, 1.0))
        local_srs = osr.SpatialReference()
        local_srs.SetLocalCS("pixel")
        raster.SetProjection(local_srs.ExportToWkt())
        band = raster.GetRasterBand(1)
        band.Fill(0)

        vector_driver = ogr.GetDriverByName("Memory")
        datasource = vector_driver.CreateDataSource("")
        layer = datasource.CreateLayer("annotations", srs=local_srs, geom_type=ogr.wkbPolygon)
        layer.CreateField(ogr.FieldDefn("value", ogr.OFTInteger))
        definition = layer.GetLayerDefn()

        for annotation in annotations:
            polygon = GeometryService.annotation_to_polygon(annotation)
            if polygon is None or polygon.is_empty:
                continue
            feature = ogr.Feature(definition)
            feature.SetField("value", int(binary_label_id if binary_label_id is not None else annotation.label_id))
            geometry = ogr.CreateGeometryFromWkb(polygon.wkb)
            feature.SetGeometry(geometry)
            layer.CreateFeature(feature)

        gdal.RasterizeLayer(raster, [1], layer, options=["ATTRIBUTE=value"])
        return band.ReadAsArray().astype(np.uint16)

    @staticmethod
    def affected_bbox_from_annotations(*annotations_or_lists) -> tuple[int, int, int, int] | None:
        boxes = []
        for item in annotations_or_lists:
            if item is None:
                continue
            if isinstance(item, AnnotationObject):
                if item.bbox is not None:
                    boxes.append(item.bbox)
                continue
            for annotation in item:
                if annotation is not None and annotation.bbox is not None:
                    boxes.append(annotation.bbox)
        if not boxes:
            return None
        min_x = int(np.floor(min(box[0] for box in boxes)))
        min_y = int(np.floor(min(box[1] for box in boxes)))
        max_x = int(np.ceil(max(box[2] for box in boxes)))
        max_y = int(np.ceil(max(box[3] for box in boxes)))
        return min_x, min_y, max(0, max_x - min_x + 1), max(0, max_y - min_y + 1)

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
    def _prepare_binary_mask(mask: np.ndarray, vector_smoothness: int) -> np.ndarray:
        binary = (mask > 0).astype(np.uint8)
        if vector_smoothness <= 0 or cv2 is None:
            return binary
        return GeometryService._gauss_blur_only_border(binary, int(vector_smoothness))

    @staticmethod
    def _gauss_blur_only_border(mask: np.ndarray, radius: int) -> np.ndarray:
        if radius <= 0 or mask.size == 0:
            return mask.astype(np.uint8, copy=True)

        binary = np.ascontiguousarray((mask > 0).astype(np.uint8))
        bounds = GeometryService._compute_mask_bounds(binary)
        if bounds is None:
            return binary
        border_mask = GeometryService._create_border_mask(binary, bounds)
        if not np.any(border_mask):
            return binary

        candidate_mask = GeometryService._expand_border_mask(border_mask, radius)
        weights = GeometryService._build_border_blur_weights(radius)
        binary_float = binary.astype(np.float32, copy=False)
        horizontal = cv2.filter2D(
            binary_float,
            ddepth=-1,
            kernel=weights.reshape(1, -1),
            borderType=cv2.BORDER_CONSTANT,
        )
        vertical = cv2.filter2D(
            binary_float,
            ddepth=-1,
            kernel=weights.reshape(-1, 1),
            borderType=cv2.BORDER_CONSTANT,
        )

        result = binary.copy()
        candidate = candidate_mask.astype(bool)
        combined = np.where(horizontal > 0.5, 1, np.where(horizontal + vertical > 0.5, 1, 0)).astype(np.uint8)
        result[candidate] = combined[candidate]
        return result

    @staticmethod
    def _compute_mask_bounds(mask: np.ndarray) -> tuple[int, int, int, int] | None:
        height, width = mask.shape[:2]
        if height == 0 or width == 0:
            return None
        ys, xs = np.where(mask > 0)
        if len(xs) == 0 or len(ys) == 0:
            return None
        return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())

    @staticmethod
    def _create_border_mask(
        mask: np.ndarray,
        bounds: tuple[int, int, int, int],
    ) -> np.ndarray:
        height, width = mask.shape[:2]
        if height == 0 or width == 0:
            return np.zeros((height, width), dtype=np.uint8)

        min_x, min_y, max_x, max_y = bounds
        border_mask = np.zeros((height, width), dtype=np.uint8)

        x0 = max(min_x, 1)
        x1 = min(max_x, width - 2)
        y0 = max(min_y, 1)
        y1 = min(max_y, height - 2)

        if x0 <= x1 and y0 <= y1:
            inner = mask[y0:y1 + 1, x0:x1 + 1] > 0
            neighbors_all = (
                (mask[y0 - 1:y1, x0 - 1:x1] > 0)
                & (mask[y0 - 1:y1, x0:x1 + 1] > 0)
                & (mask[y0 - 1:y1, x0 + 1:x1 + 2] > 0)
                & (mask[y0:y1 + 1, x0 - 1:x1] > 0)
                & (mask[y0:y1 + 1, x0 + 1:x1 + 2] > 0)
                & (mask[y0 + 1:y1 + 2, x0 - 1:x1] > 0)
                & (mask[y0 + 1:y1 + 2, x0:x1 + 1] > 0)
                & (mask[y0 + 1:y1 + 2, x0 + 1:x1 + 2] > 0)
            )
            border_mask[y0:y1 + 1, x0:x1 + 1] = (inner & ~neighbors_all).astype(np.uint8)

        if min_x == 0:
            border_mask[min_y:max_y + 1, 0] |= (mask[min_y:max_y + 1, 0] > 0).astype(np.uint8)
        if max_x == width - 1:
            border_mask[min_y:max_y + 1, max_x] |= (mask[min_y:max_y + 1, max_x] > 0).astype(np.uint8)
        if min_y == 0:
            border_mask[0, min_x:max_x + 1] |= (mask[0, min_x:max_x + 1] > 0).astype(np.uint8)
        if max_y == height - 1:
            border_mask[max_y, min_x:max_x + 1] |= (mask[max_y, min_x:max_x + 1] > 0).astype(np.uint8)
        return border_mask

    @staticmethod
    def _expand_border_mask(border_mask: np.ndarray, radius: int) -> np.ndarray:
        if radius <= 0:
            return border_mask.astype(np.uint8, copy=True)
        kernel_size = radius * 2 + 1
        kernel = np.zeros((kernel_size, kernel_size), dtype=np.uint8)
        kernel[radius, :] = 1
        kernel[:, radius] = 1
        return cv2.dilate(border_mask.astype(np.uint8), kernel, iterations=1)

    @staticmethod
    def _build_border_blur_weights(radius: int) -> np.ndarray:
        kernel_size = radius * 2 + 1
        if radius <= 0:
            return np.ones(1, dtype=np.float32)
        sigma_term = 2.0 * radius * radius
        weights = np.zeros(kernel_size, dtype=np.float32)
        total = 0.0
        for index in range(radius):
            distance_sq = float((radius - index) * (radius - index))
            weight = float(np.exp(-distance_sq / sigma_term) / np.pi)
            weights[radius + index] = weight
            weights[radius - index] = weight
            total += 2.0 * weight
        if total <= 0.0:
            weights.fill(1.0 / kernel_size)
            return weights
        return weights / total

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
            rgba[mask == label_id] = [rgb[0], rgb[1], rgb[2], 156]
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
