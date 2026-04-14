"""
矢量导出。
"""

from __future__ import annotations

from osgeo import ogr, osr

from ..geometry_service import GeometryService
from ..models import SegmentationProject


def export_vector_file(project: SegmentationProject, output_path: str, driver_name: str) -> None:
    driver = ogr.GetDriverByName(driver_name)
    if driver is None:
        raise ValueError(f"不支持的矢量驱动: {driver_name}")
    driver.DeleteDataSource(output_path)
    datasource = driver.CreateDataSource(output_path)
    if datasource is None:
        raise RuntimeError(f"无法创建矢量文件: {output_path}")

    spatial_ref = None
    if project.image_asset and project.image_asset.crs_wkt:
        spatial_ref = osr.SpatialReference()
        spatial_ref.ImportFromWkt(project.image_asset.crs_wkt)

    layer = datasource.CreateLayer("annotations", spatial_ref, ogr.wkbPolygon)
    layer.CreateField(ogr.FieldDefn("label_id", ogr.OFTInteger))
    layer.CreateField(ogr.FieldDefn("label", ogr.OFTString))
    labels = {label.id: label.name for label in project.labels}
    definition = layer.GetLayerDefn()
    for annotation in project.annotations:
        polygon = GeometryService.annotation_to_polygon(annotation)
        if polygon is None:
            continue
        feature = ogr.Feature(definition)
        feature.SetField("label_id", annotation.label_id)
        feature.SetField("label", labels.get(annotation.label_id, str(annotation.label_id)))
        geometry = ogr.CreateGeometryFromWkb(polygon.wkb)
        feature.SetGeometry(geometry)
        layer.CreateFeature(feature)
        feature = None
    datasource = None
