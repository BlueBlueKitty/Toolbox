'''
Author: Yibo Yuan 2633669459@qq.com
Description: DEM处理工具模块
    包含本地DEM处理、面积计算等功能

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
import math
import tempfile
import zipfile
from typing import Optional, Tuple, List

# GDAL相关导入
try:
    from osgeo import gdal, ogr, osr
    GDAL_AVAILABLE = True
except ImportError:
    GDAL_AVAILABLE = False
    gdal = None
    ogr = None
    osr = None


def _resolve_resample_alg(method: str):
    if not GDAL_AVAILABLE:
        return None
    mapping = {
        "双线性插值": gdal.GRA_Bilinear,
        "最邻近": gdal.GRA_NearestNeighbour,
        "三次卷积": gdal.GRA_Cubic,
        "三次样条": gdal.GRA_CubicSpline,
        "Lanczos": gdal.GRA_Lanczos,
        "平均值": gdal.GRA_Average,
        "众数": gdal.GRA_Mode,
        "bilinear": gdal.GRA_Bilinear,
        "nearest": gdal.GRA_NearestNeighbour,
        "cubic": gdal.GRA_Cubic,
        "cubicspline": gdal.GRA_CubicSpline,
        "average": gdal.GRA_Average,
        "mode": gdal.GRA_Mode,
    }
    return mapping.get((method or "").strip(), gdal.GRA_Bilinear)


def calculate_area_km2(south: float, north: float, west: float, east: float) -> float:
    """
    计算区域面积（平方公里）
    
    Args:
        south: 南纬度
        north: 北纬度
        west: 西经度
        east: 东经度
        
    Returns:
        面积（平方公里）
    """
    lat_diff = abs(north - south)
    lon_diff = abs(east - west)

    # 经度距离随纬度变化，使用平均纬度计算
    avg_lat = (north + south) / 2
    lat_distance_per_degree = 111.32  # km
    lon_distance_per_degree = 111.32 * math.cos(math.radians(avg_lat))  # km

    area = (lat_diff * lat_distance_per_degree) * (lon_diff * lon_distance_per_degree)
    return area


class LocalDEMProcessor:
    """本地DEM数据处理器"""
    
    @staticmethod
    def get_srtm_tiles(south: float, north: float, west: float, east: float) -> List[str]:
        """
        根据经纬度范围获取需要的SRTM瓦片名称列表
        SRTM文件命名格式: S11E014.SRTMGL1.hgt.zip
        """
        tiles = []
        
        lat_min = int(math.floor(south))
        lat_max = int(math.floor(north))
        lon_min = int(math.floor(west))
        lon_max = int(math.floor(east))
        
        for lat in range(lat_min, lat_max + 1):
            for lon in range(lon_min, lon_max + 1):
                lat_prefix = 'N' if lat >= 0 else 'S'
                lon_prefix = 'E' if lon >= 0 else 'W'
                tile_name = f"{lat_prefix}{abs(lat):02d}{lon_prefix}{abs(lon):03d}"
                tiles.append(tile_name)
        
        return tiles
    
    @staticmethod
    def get_copernicus_tiles(south: float, north: float, west: float, east: float) -> List[str]:
        """
        根据经纬度范围获取需要的Copernicus DEM瓦片名称列表
        Copernicus DEM文件命名格式: Copernicus_DSM_COG_10_N00_00_E006_00_DEM
        """
        tiles = []
        
        lat_min = int(math.floor(south))
        lat_max = int(math.floor(north))
        lon_min = int(math.floor(west))
        lon_max = int(math.floor(east))
        
        for lat in range(lat_min, lat_max + 1):
            for lon in range(lon_min, lon_max + 1):
                lat_prefix = 'N' if lat >= 0 else 'S'
                lon_prefix = 'E' if lon >= 0 else 'W'
                tile_name = f"Copernicus_DSM_COG_10_{lat_prefix}{abs(lat):02d}_00_{lon_prefix}{abs(lon):03d}_00_DEM"
                tiles.append(tile_name)
        
        return tiles
    
    @staticmethod
    def find_srtm_files(folder: str, tiles: List[str]) -> Tuple[List[str], List[str]]:
        """
        在SRTM文件夹中查找对应的文件
        返回: (找到的文件路径列表, 未找到的瓦片名称列表)
        """
        found_files = []
        missing_tiles = []
        
        for tile in tiles:
            found = False
            # 尝试多种命名格式
            patterns = [
                f"{tile}.SRTMGL1.hgt.zip",
                f"{tile}.SRTMGL3.hgt.zip",
                f"{tile}.hgt.zip",
                f"{tile}.hgt",
                f"{tile}.SRTMGL1.hgt",
                f"{tile}.SRTMGL3.hgt"
            ]
            
            for pattern in patterns:
                full_path = os.path.join(folder, pattern)
                if os.path.exists(full_path):
                    found_files.append(full_path)
                    found = True
                    break
            
            if not found:
                missing_tiles.append(tile)
        
        return found_files, missing_tiles
    
    @staticmethod
    def find_copernicus_files(folder: str, tiles: List[str]) -> Tuple[List[str], List[str]]:
        """
        在Copernicus DEM文件夹中查找对应的文件
        返回: (找到的文件路径列表, 未找到的瓦片名称列表)
        """
        found_files = []
        missing_tiles = []
        
        for tile in tiles:
            # Copernicus DEM的文件夹结构: folder/tile_name/tile_name.tif
            tile_folder = os.path.join(folder, tile)
            tile_tif = os.path.join(tile_folder, f"{tile}.tif")
            
            if os.path.exists(tile_tif):
                found_files.append(tile_tif)
            else:
                # 尝试直接在根目录查找
                direct_tif = os.path.join(folder, f"{tile}.tif")
                if os.path.exists(direct_tif):
                    found_files.append(direct_tif)
                else:
                    missing_tiles.append(tile)
        
        return found_files, missing_tiles
    
    @staticmethod
    def extract_hgt_from_zip(zip_path: str, temp_dir: str) -> Optional[str]:
        """从zip文件中提取hgt文件"""
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                for file_name in zip_ref.namelist():
                    if file_name.endswith('.hgt'):
                        zip_ref.extract(file_name, temp_dir)
                        return os.path.join(temp_dir, file_name)
        except Exception as e:
            print(f"解压文件失败: {zip_path}, 错误: {e}")
        return None
    
    @staticmethod
    def get_raster_extent_wgs84(raster_path: str) -> Tuple[float, float, float, float]:
        """
        获取栅格文件在WGS84坐标系下的范围
        返回: (south, north, west, east)
        """
        if not GDAL_AVAILABLE:
            raise ImportError("GDAL未安装，无法处理栅格文件")
        
        ds = gdal.Open(raster_path)
        if ds is None:
            raise ValueError(f"无法打开栅格文件: {raster_path}")
        
        gt = ds.GetGeoTransform()
        cols = ds.RasterXSize
        rows = ds.RasterYSize
        
        srs = osr.SpatialReference()
        srs.ImportFromWkt(ds.GetProjection())
        
        corners = [
            (gt[0], gt[3]),
            (gt[0] + cols * gt[1], gt[3]),
            (gt[0], gt[3] + rows * gt[5]),
            (gt[0] + cols * gt[1], gt[3] + rows * gt[5])
        ]
        
        wgs84 = osr.SpatialReference()
        wgs84.ImportFromEPSG(4326)
        
        if not srs.IsSame(wgs84):
            transform = osr.CoordinateTransformation(srs, wgs84)
            corners_wgs84 = []
            for x, y in corners:
                point = ogr.Geometry(ogr.wkbPoint)
                point.AddPoint(x, y)
                point.Transform(transform)
                corners_wgs84.append((point.GetX(), point.GetY()))
            corners = corners_wgs84
        
        lons = [c[0] for c in corners]
        lats = [c[1] for c in corners]
        
        ds = None
        
        return min(lats), max(lats), min(lons), max(lons)
    
    @staticmethod
    def merge_dem_tiles(input_files: List[str], output_path: str) -> bool:
        """合并多个DEM瓦片"""
        if not GDAL_AVAILABLE:
            print("GDAL未安装，无法合并栅格")
            return False
        
        if len(input_files) == 1:
            ds = gdal.Open(input_files[0])
            if ds is None:
                return False
            driver = gdal.GetDriverByName('GTiff')
            driver.CreateCopy(output_path, ds)
            ds = None
            return True
        
        try:
            options = gdal.WarpOptions(format='GTiff', dstNodata=-9999)
            gdal.Warp(output_path, input_files, options=options)
            return True
        except Exception as e:
            print(f"合并栅格失败: {e}")
            return False
    
    @staticmethod
    def clip_and_resample_to_reference(
        dem_path: str, 
        reference_path: str, 
        output_path: str,
        resample_method: str = "双线性插值",
    ) -> bool:
        """
        将DEM裁剪并重采样至与参考栅格相同的范围、分辨率和坐标系
        """
        if not GDAL_AVAILABLE:
            print("GDAL未安装，无法裁剪栅格")
            return False
        
        try:
            ref_ds = gdal.Open(reference_path)
            if ref_ds is None:
                raise ValueError(f"无法打开参考栅格: {reference_path}")
            
            ref_gt = ref_ds.GetGeoTransform()
            ref_cols = ref_ds.RasterXSize
            ref_rows = ref_ds.RasterYSize
            ref_proj = ref_ds.GetProjection()
            
            minx = ref_gt[0]
            maxy = ref_gt[3]
            maxx = minx + ref_cols * ref_gt[1]
            miny = maxy + ref_rows * ref_gt[5]
            
            options = gdal.WarpOptions(
                format='GTiff',
                outputBounds=[minx, miny, maxx, maxy],
                width=ref_cols,
                height=ref_rows,
                dstSRS=ref_proj,
                resampleAlg=_resolve_resample_alg(resample_method),
                dstNodata=-9999
            )
            
            result = gdal.Warp(output_path, dem_path, options=options)
            ref_ds = None
            
            return result is not None
            
        except Exception as e:
            print(f"裁剪重采样失败: {e}")
            return False    
    @staticmethod
    def clip_to_bounds(
        dem_path: str, 
        output_path: str,
        south: float,
        north: float,
        west: float,
        east: float,
        resample_method: str = "双线性插值",
    ) -> bool:
        """
        将DEM裁剪至指定的经纬度范围
        
        Args:
            dem_path: 输入DEM文件路径
            output_path: 输出文件路径
            south: 南纬度
            north: 北纬度
            west: 西经度
            east: 东经度
            
        Returns:
            是否成功
        """
        if not GDAL_AVAILABLE:
            print("GDAL未安装，无法裁剪栅格")
            return False
        
        try:
            options = gdal.WarpOptions(
                format='GTiff',
                outputBounds=[west, south, east, north],
                dstSRS='EPSG:4326',
                resampleAlg=_resolve_resample_alg(resample_method),
                dstNodata=-9999
            )
            
            result = gdal.Warp(output_path, dem_path, options=options)
            
            return result is not None
            
        except Exception as e:
            print(f"裁剪失败: {e}")
            return False
