"""矢量导出。"""

from __future__ import annotations

import numpy as np
from pathlib import Path
from osgeo import gdal, ogr, osr
from shapely.geometry import Polygon
from shapely import wkb as shapely_wkb

from ..geometry_service import GeometryService

from ..models import SegmentationProject


def export_vector_file(
    project: SegmentationProject,
    output_path: str,
    driver_name: str,
    coordinate_mode: str = "image",
) -> None:
    """export_vector_file。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        project (SegmentationProject): 输入参数。
        output_path (str): 输入参数。
        driver_name (str): 输入参数。
        coordinate_mode (str): 输入参数。
    返回:
        None: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    if project.image_asset is None:
        raise RuntimeError("缺少图像信息，无法导出矢量。")
    if project.mask_data is None:
        raise RuntimeError("当前项目中没有 Mask，无法导出矢量。")

    driver = ogr.GetDriverByName(driver_name)
    if driver is None:
        raise ValueError(f"不支持的矢量驱动: {driver_name}")
    _delete_existing_vector_output(output_path, driver_name, driver)
    datasource = driver.CreateDataSource(output_path)
    if datasource is None:
        raise RuntimeError(f"无法创建矢量文件: {output_path}")

    spatial_ref = None
    if coordinate_mode == "geo" and project.image_asset.crs_wkt:
        spatial_ref = osr.SpatialReference()
        spatial_ref.ImportFromWkt(project.image_asset.crs_wkt)
        spatial_ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    layer_options = ["ENCODING=UTF-8"] if driver_name == "ESRI Shapefile" else []
    layer = datasource.CreateLayer("annotations", spatial_ref, ogr.wkbPolygon, options=layer_options)
    layer.CreateField(ogr.FieldDefn("label_id", ogr.OFTInteger))
    layer.CreateField(ogr.FieldDefn("label", ogr.OFTString))
    labels = {label.id: label.name for label in project.labels}
    mem_driver = gdal.GetDriverByName("MEM")
    raster = mem_driver.Create("", project.image_asset.width, project.image_asset.height, 1, gdal.GDT_UInt16)
    raster.SetGeoTransform((0.0, 1.0, 0.0, 0.0, 0.0, 1.0))
    local_srs = osr.SpatialReference()
    local_srs.SetLocalCS("pixel")
    raster.SetProjection(local_srs.ExportToWkt())
    raster.GetRasterBand(1).WriteArray(np.asarray(project.mask_data, dtype=np.uint16))

    temp_ds = ogr.GetDriverByName("Memory").CreateDataSource("")
    temp_layer = temp_ds.CreateLayer("polygonize", spatial_ref, ogr.wkbPolygon)
    temp_layer.CreateField(ogr.FieldDefn("value", ogr.OFTInteger))
    gdal.Polygonize(raster.GetRasterBand(1), None, temp_layer, 0, [], callback=None)

    definition = layer.GetLayerDefn()
    if driver_name == "GPKG":
        layer.StartTransaction()
    temp_layer.ResetReading()
    for feature in temp_layer:
        label_id = int(feature.GetField("value") or 0)
        if label_id <= 0:
            continue
        geom = feature.GetGeometryRef()
        if geom is None:
            continue
        shapely_geom = shapely_wkb.loads(bytes(geom.ExportToWkb()))
        for polygon in GeometryService._extract_polygon_geometries(shapely_geom):
            if polygon.is_empty:
                continue
            if coordinate_mode == "geo":
                polygon = _polygon_to_geo_coords(polygon, project)
                if driver_name == "GeoJSON":
                    polygon = _reproject_polygon_to_wgs84(polygon, project)
                polygon = _round_polygon_coords(polygon, geo=True)
            else:
                polygon = _round_polygon_coords(polygon, geo=False)
            out_feature = ogr.Feature(definition)
            out_feature.SetField("label_id", label_id)
            out_feature.SetField("label", labels.get(label_id, str(label_id)))
            out_feature.SetGeometry(ogr.CreateGeometryFromWkb(polygon.wkb))
            layer.CreateFeature(out_feature)
            out_feature = None
    if driver_name == "GPKG":
        layer.CommitTransaction()
    temp_ds = None
    datasource = None


def _delete_existing_vector_output(output_path: str, driver_name: str, driver) -> None:
    """_delete_existing_vector_output。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        output_path (str): 输入参数。
        driver_name (str): 输入参数。
        driver (Any): 输入参数。
    返回:
        None: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    path = Path(output_path)
    if driver_name == "ESRI Shapefile":
        stem = path.with_suffix("")
        candidates = [stem.with_suffix(ext) for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix")]
        existing = [item for item in candidates if item.exists()]
        if not existing:
            return
    else:
        if not path.exists():
            return
    driver.DeleteDataSource(str(path))


def _polygon_to_geo_coords(polygon, project: SegmentationProject):
    """_polygon_to_geo_coords。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        polygon (Any): 输入参数。
        project (SegmentationProject): 输入参数。
    返回:
        None: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    复杂度:
        时间和空间复杂度与输入规模线性或近线性相关。
    """
    if project.image_asset is None or project.image_asset.geotransform is None:
        return polygon

    geotransform = project.image_asset.geotransform

    def transform_ring(coords):
        """transform_ring。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            coords (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        transformed = []
        for x, y in coords:
            map_x, map_y = gdal.ApplyGeoTransform(geotransform, x, y)
            transformed.append((map_x, map_y))
        return transformed

    return Polygon(
        transform_ring(polygon.exterior.coords),
        [transform_ring(interior.coords) for interior in polygon.interiors],
    )


def _reproject_polygon_to_wgs84(polygon, project: SegmentationProject):
    """_reproject_polygon_to_wgs84。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        polygon (Any): 输入参数。
        project (SegmentationProject): 输入参数。
    返回:
        None: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    复杂度:
        时间和空间复杂度与输入规模线性或近线性相关。
    """
    if project.image_asset is None or not project.image_asset.crs_wkt:
        return polygon
    source_ref = osr.SpatialReference()
    source_ref.ImportFromWkt(project.image_asset.crs_wkt)
    source_ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target_ref = osr.SpatialReference()
    target_ref.ImportFromEPSG(4326)
    target_ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(source_ref, target_ref)

    def transform_ring(coords):
        """transform_ring。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            coords (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        result = []
        for x, y in coords:
            tx, ty, _tz = transform.TransformPoint(float(x), float(y))
            result.append((tx, ty))
        return result

    return Polygon(
        transform_ring(polygon.exterior.coords),
        [transform_ring(interior.coords) for interior in polygon.interiors],
    )


def _round_polygon_coords(polygon, geo: bool):
    """_round_polygon_coords。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        polygon (Any): 输入参数。
        geo (bool): 输入参数。
    返回:
        None: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    复杂度:
        时间和空间复杂度与输入规模线性或近线性相关。
    """
    if polygon is None:
        return polygon

    def round_value(value: float) -> float:
        """round_value。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            value (float): 输入参数。
        返回:
            float: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        return round(float(value), 9) if geo else round(float(value), 3)

    def round_ring(coords):
        """round_ring。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            coords (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        return [(round_value(x), round_value(y)) for x, y in coords]

    return Polygon(
        round_ring(polygon.exterior.coords),
        [round_ring(interior.coords) for interior in polygon.interiors],
    )
