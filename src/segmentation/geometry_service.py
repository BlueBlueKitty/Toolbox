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
        """rectangle_to_polygon。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            x1 (float): 输入参数。
            y1 (float): 输入参数。
            x2 (float): 输入参数。
            y2 (float): 输入参数。
        返回:
            list[list[float]]: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        复杂度:
            时间和空间复杂度与输入规模线性或近线性相关。
        """
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
        """ensure_closed。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            points (list[list[float]]): 输入参数。
        返回:
            list[list[float]]: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if not points:
            return []
        if points[0] != points[-1]:
            return points + [points[0]]
        return points

    @staticmethod
    def _polygon_from_ring(exterior: list[list[float]], holes: list[list[list[float]]] | None = None):
        """_polygon_from_ring。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            exterior (list[list[float]]): 输入参数。
            holes (list[list[list[float]]] | None): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        复杂度:
            时间和空间复杂度与输入规模线性或近线性相关。
        """
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
    def _simplify_polygon_geometry(geometry, tolerance: float | None = None):
        """_simplify_polygon_geometry。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            geometry (Any): 输入参数。
            tolerance (float | None): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        复杂度:
            时间和空间复杂度与输入规模线性或近线性相关。
        """
        if geometry is None or getattr(geometry, "is_empty", True):
            return geometry
        epsilon = GeometryService._SIMPLIFY_EPSILON if tolerance is None else max(0.0, float(tolerance))
        if epsilon <= 0:
            return geometry
        simplified = geometry.simplify(epsilon, preserve_topology=True)
        if simplified.is_empty:
            return geometry
        if not simplified.is_valid:
            simplified = make_valid(simplified) if make_valid is not None else simplified.buffer(0)
        return simplified

    @staticmethod
    def annotation_to_polygon(annotation: AnnotationObject):
        """annotation_to_polygon。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotation (AnnotationObject): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        复杂度:
            时间和空间复杂度与输入规模线性或近线性相关。
        """
        return GeometryService._polygon_from_ring(annotation.exterior, annotation.holes)

    @staticmethod
    def is_annotation_geometry_valid(annotation: AnnotationObject) -> bool:
        """is_annotation_geometry_valid。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotation (AnnotationObject): 输入参数。
        返回:
            bool: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
        """refresh_annotation_metadata。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotation (AnnotationObject): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
        """annotations_union。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotations (Iterable[AnnotationObject]): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        polygons = [GeometryService.annotation_to_polygon(item) for item in annotations]
        polygons = [item for item in polygons if item is not None and not item.is_empty]
        if not polygons or unary_union is None:
            return None
        return unary_union(polygons)

    @staticmethod
    def polygon_to_annotation_objects(polygon, label_id: int, source_tool: str) -> list[AnnotationObject]:
        """polygon_to_annotation_objects。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            polygon (Any): 输入参数。
            label_id (int): 输入参数。
            source_tool (str): 输入参数。
        返回:
            list[AnnotationObject]: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        复杂度:
            时间和空间复杂度与输入规模线性或近线性相关。
        """
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
        """_extract_polygon_geometries。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            geometry (Any): 输入参数。
        返回:
            list: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        复杂度:
            时间和空间复杂度与输入规模线性或近线性相关。
        """
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
        connectivity: int = 8,
        source_tool: str = "magic_wand",
    ) -> list[AnnotationObject]:
        """mask_to_annotations。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            mask (np.ndarray): 输入参数。
            bbox (tuple[int, int, int, int]): 输入参数。
            label_id (int): 输入参数。
            connectivity (int): 输入参数。
            source_tool (str): 输入参数。
        返回:
            list[AnnotationObject]: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        annotations = GeometryService._mask_to_annotations_gdal(
            mask,
            bbox,
            label_id,
            connectivity,
            source_tool,
        )
        if annotations is None:
            raise RuntimeError("mask_to_annotations 需要 GDAL + Shapely 依赖")
        return annotations

    @staticmethod
    def fill_small_holes(mask: np.ndarray, max_hole_area: int) -> np.ndarray:
        """fill_small_holes。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            mask (np.ndarray): 输入参数。
            max_hole_area (int): 输入参数。
        返回:
            np.ndarray: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
        """fill_all_holes。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            mask (np.ndarray): 输入参数。
        返回:
            np.ndarray: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
        connectivity: int,
        source_tool: str,
    ) -> list[AnnotationObject] | None:
        """_mask_to_annotations_gdal。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            mask (np.ndarray): 输入参数。
            bbox (tuple[int, int, int, int]): 输入参数。
            label_id (int): 输入参数。
            connectivity (int): 输入参数。
            source_tool (str): 输入参数。
        返回:
            list[AnnotationObject] | None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if gdal is None or ogr is None or shapely_wkb is None:
            return None
        try:
            binary = (mask > 0).astype(np.uint8)
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
                    # if not polygon.is_valid:
                    #     polygon = make_valid(polygon) if make_valid is not None else polygon.buffer(0)
                    annotations.extend(
                        GeometryService.polygon_to_annotation_objects(polygon, label_id, source_tool)
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
        """rasterize_annotations。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotations (Iterable[AnnotationObject]): 输入参数。
            width (int): 输入参数。
            height (int): 输入参数。
            binary_label_id (int | None): 输入参数。
        返回:
            np.ndarray: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
        """affected_bbox_from_annotations。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            *annotations_or_lists (Any): 输入参数。
        返回:
            tuple[int, int, int, int] | None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
        """bbox_intersects。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            bbox_a (list[float] | tuple[float, float, float, float] | None): 输入参数。
            bbox_b (tuple[float, float, float, float] | None): 输入参数。
        返回:
            bool: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if bbox_a is None or bbox_b is None:
            return False
        ax0, ay0, ax1, ay1 = bbox_a
        bx0, by0, bw, bh = bbox_b
        bx1 = bx0 + bw
        by1 = by0 + bh
        return not (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0)

    @staticmethod
    def colorize_mask(mask: np.ndarray, label_lookup: dict[int, object]) -> np.ndarray:
        """colorize_mask。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            mask (np.ndarray): 输入参数。
            label_lookup (dict[int, object]): 输入参数。
        返回:
            np.ndarray: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
            rgba[mask == label_id] = [rgb[0], rgb[1], rgb[2], 255]
        return rgba

    @staticmethod
    def merge_mask_bbox(
        base_mask: np.ndarray | None,
        base_bbox: tuple[int, int, int, int] | None,
        incoming_mask: np.ndarray,
        incoming_bbox: tuple[int, int, int, int],
        mode: str,
    ) -> tuple[np.ndarray, tuple[int, int, int, int]]:
        """merge_mask_bbox。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            base_mask (np.ndarray | None): 输入参数。
            base_bbox (tuple[int, int, int, int] | None): 输入参数。
            incoming_mask (np.ndarray): 输入参数。
            incoming_bbox (tuple[int, int, int, int]): 输入参数。
            mode (str): 输入参数。
        返回:
            tuple[np.ndarray, tuple[int, int, int, int]]: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
