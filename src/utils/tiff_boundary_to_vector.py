

import os
from osgeo import gdal, ogr, osr
import zipfile


def tiff_boundary_to_vector(input_tiff_path, output_vector_path, to_wgs84=False, output_format=None):
    """
    提取TIFF数据的边界，并将其转换为矢量文件，仅使用GDAL库
    
    Args:
        input_tiff_path (str): 输入TIFF文件的路径
        output_vector_path (str): 输出矢量文件的路径
        to_wgs84 (bool, optional): 是否转换为WGS84坐标系。默认为False，使用原TIFF坐标系
        output_format (str, optional): 输出格式，可以是'shp'、'kml'或'kmz'。
                                      如果为None，则自动根据输出文件后缀推断
    
    Returns:
        bool: 操作是否成功
    
    Raises:
        ValueError: 如果输入或输出文件格式不支持
        IOError: 如果文件读写出错
    """
    try:
        # 检查输入文件是否存在
        if not os.path.exists(input_tiff_path):
            raise FileNotFoundError(f"找不到输入文件: {input_tiff_path}")
        
        # 自动确定输出格式（如果未指定）
        if output_format is None:
            ext = os.path.splitext(output_vector_path)[1].lower()
            if ext == '.shp':
                output_format = 'shp'
            elif ext == '.kml':
                output_format = 'kml'
            elif ext == '.kmz':
                output_format = 'kmz'
            else:
                raise ValueError(f"不支持的输出格式: {ext}。请使用 .shp、.kml 或 .kmz")
        
        # 启用GDAL异常
        gdal.UseExceptions()
        ogr.UseExceptions()
        
        # 打开TIFF文件
        ds = gdal.Open(input_tiff_path)
        if ds is None:
            raise IOError(f"无法打开TIFF文件: {input_tiff_path}")
        
        # 获取地理变换参数
        gt = ds.GetGeoTransform()
        
        # 获取栅格尺寸
        cols = ds.RasterXSize
        rows = ds.RasterYSize
        
        # 获取坐标系
        src_srs = osr.SpatialReference()
        src_srs.ImportFromWkt(ds.GetProjection())
        
        # 从GDAL 3.0开始需要处理轴顺序问题
        src_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        
        # 判断是否有地理参考
        if src_srs.Validate() != 0:
            raise ValueError(f"TIFF文件 {input_tiff_path} 没有地理参考信息")
        
        # 计算四个角点的坐标 (左上、右上、右下、左下)
        ulx, uly = gt[0], gt[3]
        urx, ury = gt[0] + cols * gt[1], gt[3]
        lrx, lry = gt[0] + cols * gt[1], gt[3] + rows * gt[5]
        llx, lly = gt[0], gt[3] + rows * gt[5]
        
        # 创建目标坐标系
        if to_wgs84 or output_format in ['kml', 'kmz']:
            target_srs = osr.SpatialReference()
            target_srs.ImportFromEPSG(4326)  # WGS 84
            target_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        else:
            target_srs = src_srs
        
        # 准备坐标转换
        transform = None
        transform_failed = False
        if (to_wgs84 or output_format in ['kml', 'kmz']) and not src_srs.IsSame(target_srs):
            try:
                transform = osr.CoordinateTransformation(src_srs, target_srs)
                if transform is None:
                    print("警告: 无法创建坐标转换对象，将使用原始坐标")
                    transform_failed = True
            except Exception as e:
                print(f"警告: 创建坐标转换失败: {e}")
                print("提示: 请确保 PROJ_LIB 环境变量已正确设置，且 proj.db 文件存在")
                transform_failed = True
        
        # 创建输出驱动
        if output_format == 'shp':
            driver_name = 'ESRI Shapefile'
        elif output_format in ['kml', 'kmz']:
            driver_name = 'KML'
        else:
            raise ValueError(f"不支持的输出格式: {output_format}")
        
        # 如果是KMZ，先生成临时KML文件
        if output_format == 'kmz':
            temp_output = output_vector_path.replace('.kmz', '.kml')
        else:
            temp_output = output_vector_path
        
        # 创建矢量文件
        driver = ogr.GetDriverByName(driver_name)
        if driver is None:
            raise ValueError(f"找不到驱动: {driver_name}")
            
        # 创建输出文件
        if os.path.exists(temp_output):
            driver.DeleteDataSource(temp_output)
        try:
            data_source = driver.CreateDataSource(temp_output)
        except:
            raise ValueError('无法创建输出矢量文件, 请检查输出路径是否正确, 或者输出文件是否被占用')
        if data_source is None:
            raise ValueError('无法创建输出矢量文件, 请检查输出路径是否正确, 或者输出文件是否被占用')
        
        # 设置图层的空间参考
        layer = data_source.CreateLayer('boundary', target_srs, ogr.wkbPolygon)
        
        # 添加属性字段
        field_name = ogr.FieldDefn('source', ogr.OFTString)
        field_name.SetWidth(254)
        layer.CreateField(field_name)
        
        field_desc = ogr.FieldDefn('desc', ogr.OFTString)
        field_desc.SetWidth(254)
        layer.CreateField(field_desc)
        
        # 创建一个多边形特征
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetField('source', os.path.basename(input_tiff_path))
        feature.SetField('desc', f"Boundary of {os.path.basename(input_tiff_path)}")
        
        # 创建多边形环
        ring = ogr.Geometry(ogr.wkbLinearRing)
        
        # 添加角点（顺时针顺序）
        corners = [(ulx, uly), (urx, ury), (lrx, lry), (llx, lly), (ulx, uly)]
        
        # 存储转换后的点
        transformed_corners = []
        
        # 首先检查坐标是否需要转换，并转换所有点
        for x, y in corners:
            if transform:
                try:
                    x, y, _ = transform.TransformPoint(x, y)
                    
                    # 对于KML/KMZ格式，确保坐标在有效范围内
                    if output_format in ['kml', 'kmz']:
                        # 限制纬度范围在 -90 到 90 之间
                        y = max(-89.99999, min(89.99999, y))
                        # 限制经度范围在 -180 到 180 之间
                        while x > 180:
                            x -= 360
                        while x < -180:
                            x += 360
                        
                except Exception as e:
                    print(f"坐标转换警告: {e}, 使用原始坐标")
                
            transformed_corners.append((x, y))
        
        # 将转换后的点添加到环
        for x, y in transformed_corners:
            ring.AddPoint(x, y)
        
        # 创建多边形，添加环
        polygon = ogr.Geometry(ogr.wkbPolygon)
        polygon.AddGeometry(ring)
        
        # 设置特征的几何对象
        feature.SetGeometry(polygon)
        
        # 添加特征到图层
        layer.CreateFeature(feature)
        
        # 清理
        feature = None
        data_source = None
        
        # 如果是KMZ格式，需要将KML压缩成KMZ
        if output_format == 'kmz':
            with zipfile.ZipFile(output_vector_path, 'w', zipfile.ZIP_DEFLATED) as kmz:
                kmz.write(temp_output, os.path.basename(temp_output))
            # 删除临时KML文件
            os.remove(temp_output)
        
        print(f"成功将TIFF边界转换为{output_format}格式，保存至: {output_vector_path}")
        return True
        
    except Exception as e:
        print(f"转换失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    # 示例用法
    tiff_path = r"D:\Desktop\代码开发培训-苑艺博-20250820\示例数据和代码\SAR影像.tif"
    output_path = r"D:\Desktop\dsm_boundary.shp"
    success = tiff_boundary_to_vector(tiff_path, output_path, to_wgs84=True)
    print(f"转换结果: {'成功' if success else '失败'}")