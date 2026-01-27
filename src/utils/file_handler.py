def transform_corners_to_wgs84(spatial_ref, corners):
    """
    将输入坐标系下的四个角点坐标转换为WGS84坐标系下的(min_lon, min_lat, max_lon, max_lat)
    Args:
        spatial_ref: 源osr.SpatialReference对象
        corners: [(x1, y1), (x2, y2), (x3, y3), (x4, y4)]
    Returns:
        (min_lon, min_lat, max_lon, max_lat)
    """
    from osgeo import osr, ogr, gdal
    
    # 启用 GDAL 异常，以便捕获错误
    gdal.UseExceptions()
    ogr.UseExceptions()
    
    # 1. 定义标准 WGS84 (EPSG:4326)
    target_srs = osr.SpatialReference()
    target_srs.ImportFromEPSG(4326)
    target_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    
    if spatial_ref is not None:
        spatial_ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        
        # 检查是否已经是 WGS84
        if spatial_ref.IsSame(target_srs):
            xs = [c[0] for c in corners]
            ys = [c[1] for c in corners]
            return (min(xs), min(ys), max(xs), max(ys))
        
        try:
            transform = osr.CoordinateTransformation(spatial_ref, target_srs)
            if transform is None:
                raise RuntimeError("无法创建坐标转换对象")
            
            lons = []
            lats = []
            for x, y in corners:
                point = ogr.Geometry(ogr.wkbPoint)
                point.AddPoint(x, y)
                err = point.Transform(transform)
                if err != 0:
                    raise RuntimeError(f"坐标转换失败，错误码: {err}")
                lons.append(point.GetX())
                lats.append(point.GetY())
            return (min(lons), min(lats), max(lons), max(lats))
        except Exception as e:
            print(f"坐标转换错误: {e}")
            print("提示: 请确保 PROJ_LIB 环境变量已正确设置，且 proj.db 文件存在")
            # 如果转换失败，返回原始坐标（假设已经是 WGS84 或近似）
            xs = [c[0] for c in corners]
            ys = [c[1] for c in corners]
            return (min(xs), min(ys), max(xs), max(ys))
    else:
        # 没有空间参考，直接返回原始坐标
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]
        return (min(xs), min(ys), max(xs), max(ys))
'''
Author: Yibo Yuan 2633669459@qq.com
Date: 2026-01-26
Description: 文件处理工具
    提供从矢量文件和栅格文件提取边界的功能

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
from typing import Optional, Tuple, List
from osgeo import ogr, gdal, osr


def extract_bounding_box_from_vector(file_path: str) -> Optional[Tuple[float, float, float, float]]:
    """
    从矢量文件中提取边界框（WGS84坐标）
    
    支持的格式: GeoJSON, KML, Shapefile, GPKG等
    
    Args:
        file_path: 矢量文件路径
        
    Returns:
        (min_lon, min_lat, max_lon, max_lat) 即 (west, south, east, north) 或 None
    """

    from osgeo import ogr
    try:
        datasource = ogr.Open(file_path)
        if datasource is None:
            return None
        layer = datasource.GetLayer(0)
        extent = layer.GetExtent()  # (min_x, max_x, min_y, max_y)
        spatial_ref = layer.GetSpatialRef()
        # 四个角点
        corners = [
            (extent[0], extent[2]), (extent[0], extent[3]),
            (extent[1], extent[2]), (extent[1], extent[3])
        ]
        return transform_corners_to_wgs84(spatial_ref, corners)

    except Exception as e:
        print(f"处理文件 {file_path} 出错: {e}")
        return None
    finally:
        datasource = None


def extract_bounding_box_from_raster(file_path: str) -> Optional[Tuple[float, float, float, float]]:
    """
    从栅格文件中提取边界框（WGS84坐标）
    
    Args:
        file_path: 栅格文件路径
        
    Returns:
        (min_lon, min_lat, max_lon, max_lat) 或 None
    """

    try:
        dataset = gdal.Open(file_path)
        if dataset is None:
            print(f"无法打开栅格文件: {file_path}")
            return None
        geotransform = dataset.GetGeoTransform()
        width = dataset.RasterXSize
        height = dataset.RasterYSize
        min_x = geotransform[0]
        max_y = geotransform[3]
        max_x = min_x + width * geotransform[1]
        min_y = max_y + height * geotransform[5]
        proj = dataset.GetProjection()
        spatial_ref = osr.SpatialReference()
        spatial_ref.ImportFromWkt(proj)
        corners = [
            (min_x, min_y),
            (min_x, max_y),
            (max_x, min_y),
            (max_x, max_y),
        ]
        return transform_corners_to_wgs84(spatial_ref, corners)
    except Exception as e:
        print(f"从栅格文件提取边界失败: {e}")
        return None
    finally:
        dataset = None


def get_raster_info(file_path: str) -> Optional[dict]:
    """
    获取栅格文件的详细信息
    
    Args:
        file_path: 栅格文件路径
        
    Returns:
        包含栅格信息的字典
    """
    try:
        dataset = gdal.Open(file_path)
        if dataset is None:
            return None
        
        geotransform = dataset.GetGeoTransform()
        
        info = {
            'width': dataset.RasterXSize,
            'height': dataset.RasterYSize,
            'bands': dataset.RasterCount,
            'pixel_width': abs(geotransform[1]),
            'pixel_height': abs(geotransform[5]),
            'origin_x': geotransform[0],
            'origin_y': geotransform[3],
            'projection': dataset.GetProjection(),
            'driver': dataset.GetDriver().ShortName,
        }
        
        # 获取数据类型
        band = dataset.GetRasterBand(1)
        info['data_type'] = gdal.GetDataTypeName(band.DataType)
        info['nodata'] = band.GetNoDataValue()
        
        return info
        
    except Exception as e:
        print(f"获取栅格信息失败: {e}")
        return None
    finally:
        dataset = None


def get_vector_layer_info(file_path: str) -> Optional[dict]:
    """
    获取矢量文件的图层信息
    
    Args:
        file_path: 矢量文件路径
        
    Returns:
        包含矢量信息的字典
    """
    try:
        datasource = ogr.Open(file_path)
        if datasource is None:
            return None
        
        layer = datasource.GetLayer(0)
        if layer is None:
            return None
        
        info = {
            'layer_count': datasource.GetLayerCount(),
            'layer_name': layer.GetName(),
            'feature_count': layer.GetFeatureCount(),
            'geometry_type': ogr.GeometryTypeToName(layer.GetGeomType()),
            'extent': layer.GetExtent(),
        }
        
        # 获取空间参考信息
        spatial_ref = layer.GetSpatialRef()
        if spatial_ref:
            info['srs_name'] = spatial_ref.GetName() if hasattr(spatial_ref, 'GetName') else str(spatial_ref)
            info['srs_epsg'] = spatial_ref.GetAuthorityCode(None)
        
        # 获取字段信息
        layer_defn = layer.GetLayerDefn()
        fields = []
        for i in range(layer_defn.GetFieldCount()):
            field_defn = layer_defn.GetFieldDefn(i)
            fields.append({
                'name': field_defn.GetName(),
                'type': field_defn.GetTypeName(),
            })
        info['fields'] = fields
        
        return info
        
    except Exception as e:
        print(f"获取矢量信息失败: {e}")
        return None
    finally:
        datasource = None


def get_supported_vector_extensions() -> List[str]:
    """获取支持的矢量文件扩展名"""
    return ['.shp', '.geojson', '.json', '.kml', '.kmz', '.gpkg', '.gml']


def get_supported_raster_extensions() -> List[str]:
    """获取支持的栅格文件扩展名"""
    return ['.tif', '.tiff', '.img', '.hgt', '.dem', '.asc']


def is_vector_file(file_path: str) -> bool:
    """检查是否为支持的矢量文件"""
    ext = os.path.splitext(file_path)[1].lower()
    return ext in get_supported_vector_extensions()


def is_raster_file(file_path: str) -> bool:
    """检查是否为支持的栅格文件"""
    ext = os.path.splitext(file_path)[1].lower()
    return ext in get_supported_raster_extensions()
