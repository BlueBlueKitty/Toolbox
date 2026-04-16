"""
矢量导出。
"""

from __future__ import annotations

from osgeo import gdal, ogr, osr
from shapely.geometry import Polygon

from ..geometry_service import GeometryService
from ..models import SegmentationProject


def export_vector_file(
    project: SegmentationProject,
    output_path: str,
    driver_name: str,
    coordinate_mode: str = "image",
) -> None:
    driver = ogr.GetDriverByName(driver_name)
    if driver is None:
        raise ValueError(f"不支持的矢量驱动: {driver_name}")
    driver.DeleteDataSource(output_path)
    datasource = driver.CreateDataSource(output_path)
    if datasource is None:
        raise RuntimeError(f"无法创建矢量文件: {output_path}")

    spatial_ref = None
    if coordinate_mode == "geo" and project.image_asset and project.image_asset.crs_wkt:
        spatial_ref = osr.SpatialReference()
        spatial_ref.ImportFromWkt(project.image_asset.crs_wkt)
        spatial_ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        if driver_name == "GeoJSON":
            spatial_ref = osr.SpatialReference()
            spatial_ref.ImportFromEPSG(4326)
            spatial_ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    layer = datasource.CreateLayer("annotations", spatial_ref, ogr.wkbPolygon)
    layer.CreateField(ogr.FieldDefn("label_id", ogr.OFTInteger))
    layer.CreateField(ogr.FieldDefn("label", ogr.OFTString))
    labels = {label.id: label.name for label in project.labels}
    definition = layer.GetLayerDefn()
    for annotation in project.annotations:
        polygon = GeometryService.annotation_to_polygon(annotation)
        if polygon is None:
            continue
        if coordinate_mode == "geo":
            polygon = _polygon_to_geo_coords(polygon, project)
            if driver_name == "GeoJSON":
                polygon = _reproject_polygon_to_wgs84(polygon, project)
            polygon = _round_polygon_coords(polygon, geo=True)
        else:
            polygon = _round_polygon_coords(polygon, geo=False)
        feature = ogr.Feature(definition)
        feature.SetField("label_id", annotation.label_id)
        feature.SetField("label", labels.get(annotation.label_id, str(annotation.label_id)))
        geometry = ogr.CreateGeometryFromWkb(polygon.wkb)
        feature.SetGeometry(geometry)
        layer.CreateFeature(feature)
        feature = None
    datasource = None


def _polygon_to_geo_coords(polygon, project: SegmentationProject):
    if project.image_asset is None or project.image_asset.geotransform is None:
        return polygon
    from shapely.geometry import Polygon

    geotransform = project.image_asset.geotransform

    def transform_ring(coords):
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
    if polygon is None:
        return polygon

    def round_value(value: float) -> float:
        return float(f"{float(value):.6g}") if geo else round(float(value), 3)

    def round_ring(coords):
        return [(round_value(x), round_value(y)) for x, y in coords]

    return Polygon(
        round_ring(polygon.exterior.coords),
        [round_ring(interior.coords) for interior in polygon.interiors],
    )
