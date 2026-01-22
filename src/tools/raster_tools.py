import os
import subprocess
import zipfile
from osgeo import gdal, osr



def get_vector_wgs84_extent(vector_file):
    '''
    打开矢量文件，返回其WGS84下的最小外接矩形范围(lon_min, lon_max, lat_min, lat_max)
    '''
    from osgeo import ogr
    vector_ds = ogr.Open(vector_file)
    if vector_ds is None:
        raise ValueError('无法打开矢量文件')
    layer = vector_ds.GetLayer()
    source_srs = layer.GetSpatialRef()
    wgs84_srs = osr.SpatialReference()
    wgs84_srs.ImportFromEPSG(4326)
    extent = layer.GetExtent()  # (minX, maxX, minY, maxY)
    if source_srs is None or not source_srs.IsSame(wgs84_srs):
        transform = osr.CoordinateTransformation(source_srs, wgs84_srs)
        min_x, max_x, min_y, max_y = extent
        point1 = transform.TransformPoint(min_x, min_y)
        point2 = transform.TransformPoint(max_x, max_y)
        point3 = transform.TransformPoint(min_x, max_y)
        point4 = transform.TransformPoint(max_x, min_y)
        lon_min = min(point1[0], point2[0], point3[0], point4[0])
        lon_max = max(point1[0], point2[0], point3[0], point4[0])
        lat_min = min(point1[1], point2[1], point3[1], point4[1])
        lat_max = max(point1[1], point2[1], point3[1], point4[1])
    else:
        lon_min, lon_max, lat_min, lat_max = extent
    vector_ds = None
    return lon_min, lon_max, lat_min, lat_max

def get_int_latlon_ranges(lon_min, lon_max, lat_min, lat_max):
    '''
    根据经纬度范围生成整数经纬度区间
    '''
    lat_min_int = int(lat_min) - 1 if lat_min < 0 else int(lat_min)
    lat_max_int = int(lat_max) if lat_max <= 0 else int(lat_max) + 1
    lon_min_int = int(lon_min) - 1 if lon_min < 0 else int(lon_min)
    lon_max_int = int(lon_max) if lon_max <= 0 else int(lon_max) + 1
    lat_range_int = range(lat_min_int, lat_max_int)
    lon_range_int = range(lon_min_int, lon_max_int)
    return lat_range_int, lon_range_int

def get_srtm_file_by_vector(vector_file, srtm_folder, output_file):
    '''
    根据矢量文件的范围获取对应的SRTM数据
    '''
    srtm_lat_range = [-56, 59]
    temp_folder = os.path.join(os.path.dirname(vector_file), 'temp')
    if not os.path.exists(temp_folder):
        os.makedirs(temp_folder)
    lon_min, lon_max, lat_min, lat_max = get_vector_wgs84_extent(vector_file)
    lat_range_int, lon_range_int = get_int_latlon_ranges(lon_min, lon_max, lat_min, lat_max)
    input_files = []
    for lat in lat_range_int:
        for lon in lon_range_int:
            lat_lon_name = f'N{str(abs(lat)).zfill(2)}E{str(abs(lon)).zfill(3)}'
            if lat < 0:
                lat_lon_name = lat_lon_name.replace('N', 'S')
            if lon < 0:
                lat_lon_name = lat_lon_name.replace('E', 'W')
            srtm_file = f'{lat_lon_name}.SRTMGL1.hgt.zip'
            srtm_path = os.path.join(srtm_folder, srtm_file)
            if os.path.exists(srtm_path):
                temp_srtm_path = os.path.join(temp_folder, srtm_file)
                with open(srtm_path, 'rb') as src, open(temp_srtm_path, 'wb') as dst:
                    dst.write(src.read())
                with zipfile.ZipFile(temp_srtm_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_folder)
                unzip_file = f'{lat_lon_name}.hgt'
                unzip_file = os.path.join(temp_folder, unzip_file)
                input_files.append(unzip_file)
                os.remove(temp_srtm_path)
            else:
                if lat < min(srtm_lat_range) or lat > max(srtm_lat_range):
                    for file in input_files:
                        if os.path.exists(file):
                            os.remove(file)
                    if os.path.exists(temp_folder) and os.listdir(temp_folder) == []:
                        os.rmdir(temp_folder)
                    return False, srtm_path
                else:
                    continue
    if not input_files:
        if os.path.exists(temp_folder) and os.listdir(temp_folder) == []:
            os.rmdir(temp_folder)
        return False, '未找到对应的SRTM文件'
    merge_file = os.path.join(temp_folder, 'srtm_merge.tif')
    command = ['python', 'gdal_merge.py', '-o', merge_file, '-of', 'GTiff'] + input_files
    subprocess.run(command, check=True)
    gdal.Warp(output_file, merge_file, 
              outputBounds=(lon_min, lat_min, lon_max, lat_max),
              format='GTiff',
              dstSRS='EPSG:4326')
    os.remove(merge_file)
    for file in input_files:
        os.remove(file)
    if os.listdir(temp_folder) == []:
        os.rmdir(temp_folder)
    return True, None

if __name__ == '__main__':
    vector_file = r'test\flood_harvey_aoi.kml'
    srtm_folder = r'D:\Global_Data\SRTM30M\data'
    output_file = r'test\harvey_dem.tif'
    
    success, error = get_srtm_file_by_vector(vector_file, srtm_folder, output_file)
    if success:
        print('DEM文件生成成功:', output_file)
    else:
        print('错误:', error)