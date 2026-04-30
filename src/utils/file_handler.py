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
Description: 文件处理工具
    提供从矢量文件和栅格文件提取边界的功能

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import math
import os
import re
from typing import Optional, Tuple, List, Dict
from osgeo import ogr, gdal, osr
import numpy as np
from .gamma_file_process import parse_par_file


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


def extract_gamma_par_corners(file_path: str) -> Optional[Dict[str, Tuple[float, float]]]:
    """
    从GAMMA par文件提取四角点坐标（经纬度）。

    返回格式：
    {
        "UL": (lon, lat),
        "UR": (lon, lat),
        "LR": (lon, lat),
        "LL": (lon, lat),
    }
    """
    try:
        params = parse_par_file(file_path)
        lines = _read_text_lines(file_path)
        corners = _solve_gamma_corners(params, lines, terrain_height=300.0)
    except Exception as e:
        print(f"GAMMA par角点计算失败: {e}")
        return None
    return corners


def extract_bounding_box_from_gamma_par(file_path: str) -> Optional[Tuple[float, float, float, float]]:
    """
    从GAMMA par文件提取边界框（WGS84坐标）。
    返回 (min_lon, min_lat, max_lon, max_lat)
    """
    corners = extract_gamma_par_corners(file_path)
    if not corners:
        return None
    lons = [pt[0] for pt in corners.values()]
    lats = [pt[1] for pt in corners.values()]
    return min(lons), min(lats), max(lons), max(lats)


def _read_text_lines(file_path: str) -> List[str]:
    """_read_text_lines。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        file_path (str): 输入参数。
    返回:
        List[str]: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read().splitlines()


def _extract_numeric_values_after_colon(line: str) -> List[float]:
    """_extract_numeric_values_after_colon。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        line (str): 输入参数。
    返回:
        List[float]: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    _, value = line.split(":", 1)
    return [float(x) for x in re.findall(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", value)]


def _get_gamma_state_vectors(lines: List[str], count: int) -> Tuple[List[np.ndarray], List[np.ndarray]]:
    """_get_gamma_state_vectors。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        lines (List[str]): 输入参数。
        count (int): 输入参数。
    返回:
        Tuple[List[np.ndarray], List[np.ndarray]]: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    positions: List[np.ndarray] = []
    velocities: List[np.ndarray] = []
    for idx in range(1, count + 1):
        pos_line = next((line for line in lines if line.startswith(f"state_vector_position_{idx}:")), None)
        vel_line = next((line for line in lines if line.startswith(f"state_vector_velocity_{idx}:")), None)
        if pos_line is None or vel_line is None:
            raise ValueError(f"缺少第 {idx} 个状态矢量")
        positions.append(np.array(_extract_numeric_values_after_colon(pos_line)[:3], dtype=float))
        velocities.append(np.array(_extract_numeric_values_after_colon(vel_line)[:3], dtype=float))
    return positions, velocities


def _hermite_interpolate(
    p0: np.ndarray, v0: np.ndarray, p1: np.ndarray, v1: np.ndarray, dt: float, u: float
) -> Tuple[np.ndarray, np.ndarray]:
    """_hermite_interpolate。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        p0 (np.ndarray): 输入参数。
        v0 (np.ndarray): 输入参数。
        p1 (np.ndarray): 输入参数。
        v1 (np.ndarray): 输入参数。
        dt (float): 输入参数。
        u (float): 输入参数。
    返回:
        Tuple[np.ndarray, np.ndarray]: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    复杂度:
        时间和空间复杂度与输入规模线性或近线性相关。
    """
    h00 = 2 * u ** 3 - 3 * u ** 2 + 1
    h10 = u ** 3 - 2 * u ** 2 + u
    h01 = -2 * u ** 3 + 3 * u ** 2
    h11 = u ** 3 - u ** 2

    pos = h00 * p0 + h10 * dt * v0 + h01 * p1 + h11 * dt * v1

    dh00 = 6 * u ** 2 - 6 * u
    dh10 = 3 * u ** 2 - 4 * u + 1
    dh01 = -6 * u ** 2 + 6 * u
    dh11 = 3 * u ** 2 - 2 * u
    vel = (dh00 * p0 + dh10 * dt * v0 + dh01 * p1 + dh11 * dt * v1) / dt
    return pos, vel


def _interpolate_sensor_state(params: Dict, lines: List[str], az_time: float) -> Tuple[np.ndarray, np.ndarray]:
    """_interpolate_sensor_state。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        params (Dict): 输入参数。
        lines (List[str]): 输入参数。
        az_time (float): 输入参数。
    返回:
        Tuple[np.ndarray, np.ndarray]: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    复杂度:
        时间和空间复杂度与输入规模线性或近线性相关。
    """
    nstate = int(params["number_of_state_vectors"])
    time0 = float(params["time_of_first_state_vector"])
    interval = float(params["state_vector_interval"])
    positions, velocities = _get_gamma_state_vectors(lines, nstate)

    index = max(0, min(nstate - 2, int(math.floor((az_time - time0) / interval))))
    u = (az_time - (time0 + index * interval)) / interval
    return _hermite_interpolate(
        positions[index], velocities[index], positions[index + 1], velocities[index + 1], interval, u
    )


def _gamma_doppler_frequency(params: Dict, lines: List[str], slant_range: float, az_time: float) -> float:
    """_gamma_doppler_frequency。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        params (Dict): 输入参数。
        lines (List[str]): 输入参数。
        slant_range (float): 输入参数。
        az_time (float): 输入参数。
    返回:
        float: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    coeffs = [0.0, 0.0, 0.0, 0.0]
    dot_coeffs = [0.0, 0.0, 0.0, 0.0]
    ddot_coeffs = [0.0, 0.0, 0.0, 0.0]
    for line in lines:
        if line.startswith("doppler_polynomial:"):
            coeffs = _extract_numeric_values_after_colon(line)[:4]
        elif line.startswith("doppler_poly_dot:"):
            dot_coeffs = _extract_numeric_values_after_colon(line)[:4]
        elif line.startswith("doppler_poly_ddot:"):
            ddot_coeffs = _extract_numeric_values_after_colon(line)[:4]

    dt = az_time - float(params.get("center_time", az_time))
    coeffs_t = [
        coeffs[i] + dot_coeffs[i] * dt + 0.5 * ddot_coeffs[i] * dt * dt
        for i in range(4)
    ]
    dr = slant_range - float(params["center_range_slc"])
    return (
        coeffs_t[0]
        + coeffs_t[1] * dr
        + coeffs_t[2] * dr * dr
        + coeffs_t[3] * dr * dr * dr
    )


def _ecef_to_llh(xyz: np.ndarray) -> Tuple[float, float, float]:
    """_ecef_to_llh。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        xyz (np.ndarray): 输入参数。
    返回:
        Tuple[float, float, float]: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    source = osr.SpatialReference()
    source.ImportFromEPSG(4978)
    source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target = osr.SpatialReference()
    target.ImportFromEPSG(4326)
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(source, target)
    lon, lat, height = transform.TransformPoint(float(xyz[0]), float(xyz[1]), float(xyz[2]))
    return lon, lat, height


def _llh_to_ecef(lat_deg: float, lon_deg: float, height: float, semi_major: float, semi_minor: float) -> np.ndarray:
    """_llh_to_ecef。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        lat_deg (float): 输入参数。
        lon_deg (float): 输入参数。
        height (float): 输入参数。
        semi_major (float): 输入参数。
        semi_minor (float): 输入参数。
    返回:
        np.ndarray: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    e2 = 1.0 - (semi_minor * semi_minor) / (semi_major * semi_major)
    n = semi_major / math.sqrt(1.0 - e2 * math.sin(lat) ** 2)
    x = (n + height) * math.cos(lat) * math.cos(lon)
    y = (n + height) * math.cos(lat) * math.sin(lon)
    z = (n * (1.0 - e2) + height) * math.sin(lat)
    return np.array([x, y, z], dtype=float)


def _initial_local_sphere_guess(
    params: Dict,
    lines: List[str],
    az_time: float,
    slant_range: float,
    terrain_height: float,
    look_sign: float,
) -> Tuple[float, float]:
    """_initial_local_sphere_guess。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        params (Dict): 输入参数。
        lines (List[str]): 输入参数。
        az_time (float): 输入参数。
        slant_range (float): 输入参数。
        terrain_height (float): 输入参数。
        look_sign (float): 输入参数。
    返回:
        Tuple[float, float]: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    sat_pos, sat_vel = _interpolate_sensor_state(params, lines, az_time)
    sat_speed = np.linalg.norm(sat_vel)
    sat_dir = sat_vel / sat_speed
    sat_dist = np.linalg.norm(sat_pos)
    earth_radius = float(params.get("earth_radius_below_sensor", 6371000.0))
    n_hat = -sat_pos / sat_dist
    c_hat = np.cross(n_hat, sat_dir)
    c_hat = c_hat / np.linalg.norm(c_hat)
    t_hat = np.cross(c_hat, n_hat)

    gamma = (sat_dist * sat_dist + slant_range * slant_range - (earth_radius + terrain_height) ** 2) / (2.0 * sat_dist)
    wavelength = 299792458.0 / float(params["radar_frequency"])
    fd = _gamma_doppler_frequency(params, lines, slant_range, az_time)
    alpha = fd * wavelength * slant_range / (2.0 * sat_speed)
    alpha -= gamma * (np.dot(n_hat, sat_dir) / np.dot(t_hat, sat_dir))
    beta2 = max(0.0, slant_range * slant_range - gamma * gamma - alpha * alpha)
    beta = look_sign * math.sqrt(beta2)
    target_xyz = sat_pos + alpha * t_hat + beta * c_hat + gamma * n_hat
    lon, lat, _ = _ecef_to_llh(target_xyz)
    return lat, lon


def _solve_corner_latlon(
    params: Dict,
    lines: List[str],
    az_time: float,
    slant_range: float,
    terrain_height: float,
    look_sign: float,
) -> Tuple[float, float]:
    """_solve_corner_latlon。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        params (Dict): 输入参数。
        lines (List[str]): 输入参数。
        az_time (float): 输入参数。
        slant_range (float): 输入参数。
        terrain_height (float): 输入参数。
        look_sign (float): 输入参数。
    返回:
        Tuple[float, float]: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    复杂度:
        时间和空间复杂度与输入规模线性或近线性相关。
    """
    semi_major = float(params["earth_semi_major_axis"])
    semi_minor = float(params["earth_semi_minor_axis"])
    sat_pos, sat_vel = _interpolate_sensor_state(params, lines, az_time)
    doppler = _gamma_doppler_frequency(params, lines, slant_range, az_time)
    wavelength = 299792458.0 / float(params["radar_frequency"])

    lat, lon = _initial_local_sphere_guess(params, lines, az_time, slant_range, terrain_height, look_sign)
    for _ in range(20):
        xyz = _llh_to_ecef(lat, lon, terrain_height, semi_major, semi_minor)
        diff = xyz - sat_pos
        current_range = np.linalg.norm(diff)
        if current_range <= 0.0:
            break
        f1 = current_range - slant_range
        f2 = np.dot(sat_vel, diff) - doppler * wavelength * current_range / 2.0
        residual = np.array([f1, f2], dtype=float)
        if np.linalg.norm(residual) < 1e-6:
            break

        eps = 1e-6
        jacobian = np.zeros((2, 2), dtype=float)
        for col, (dlat, dlon) in enumerate(((eps, 0.0), (0.0, eps))):
            xyz2 = _llh_to_ecef(lat + dlat, lon + dlon, terrain_height, semi_major, semi_minor)
            diff2 = xyz2 - sat_pos
            range2 = np.linalg.norm(diff2)
            residual2 = np.array(
                [
                    range2 - slant_range,
                    np.dot(sat_vel, diff2) - doppler * wavelength * range2 / 2.0,
                ],
                dtype=float,
            )
            jacobian[:, col] = (residual2 - residual) / eps

        step = np.linalg.solve(jacobian, -residual)
        lat += float(step[0])
        lon += float(step[1])
        if np.linalg.norm(step) < 1e-10:
            break

    return lon, lat


def _sort_corner_points(points: List[Tuple[float, float]]) -> Dict[str, Tuple[float, float]]:
    """_sort_corner_points。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        points (List[Tuple[float, float]]): 输入参数。
    返回:
        Dict[str, Tuple[float, float]]: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    复杂度:
        时间和空间复杂度与输入规模线性或近线性相关。
    """
    sorted_by_lat = sorted(points, key=lambda p: p[1], reverse=True)
    upper = sorted(sorted_by_lat[:2], key=lambda p: p[0])
    lower = sorted(sorted_by_lat[2:], key=lambda p: p[0])
    ul, ur = upper
    ll, lr = lower
    return {"UL": ul, "UR": ur, "LR": lr, "LL": ll}


def _solve_gamma_corners(params: Dict, lines: List[str], terrain_height: float = 300.0) -> Dict[str, Tuple[float, float]]:
    """_solve_gamma_corners。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        params (Dict): 输入参数。
        lines (List[str]): 输入参数。
        terrain_height (float): 输入参数。
    返回:
        Dict[str, Tuple[float, float]]: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    复杂度:
        时间和空间复杂度与输入规模线性或近线性相关。
    """
    required = [
        "start_time",
        "end_time",
        "near_range_slc",
        "far_range_slc",
        "center_latitude",
        "center_longitude",
        "earth_semi_major_axis",
        "earth_semi_minor_axis",
        "radar_frequency",
        "number_of_state_vectors",
        "time_of_first_state_vector",
        "state_vector_interval",
    ]
    missing = [key for key in required if key not in params]
    if missing:
        raise ValueError(f"par文件缺少必要字段: {', '.join(missing)}")

    center_lon = float(params["center_longitude"])
    center_lat = float(params["center_latitude"])
    corner_specs = [
        (float(params["start_time"]), float(params["near_range_slc"])),
        (float(params["start_time"]), float(params["far_range_slc"])),
        (float(params["end_time"]), float(params["near_range_slc"])),
        (float(params["end_time"]), float(params["far_range_slc"])),
    ]

    solved_points: List[Tuple[float, float]] = []
    for az_time, slant_range in corner_specs:
        candidates = [
            _solve_corner_latlon(params, lines, az_time, slant_range, terrain_height, look_sign=1.0),
            _solve_corner_latlon(params, lines, az_time, slant_range, terrain_height, look_sign=-1.0),
        ]
        best = min(
            candidates,
            key=lambda pt: (pt[0] - center_lon) ** 2 + (pt[1] - center_lat) ** 2,
        )
        solved_points.append(best)

    return _sort_corner_points(solved_points)


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
