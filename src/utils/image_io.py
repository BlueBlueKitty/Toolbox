'''
Author: Yibo Yuan 2633669459@qq.com
Description: 图像读取工具模块

提供统一的图像读取接口，支持：
- TIFF文件（使用GDAL，支持金字塔/Overview）
- 普通图像文件（使用PIL，支持PNG、JPEG等）
- HDF5文件（使用h5py）
- GAMMA二进制文件

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved.
'''

import os
import numpy as np
from PIL import Image
from osgeo import gdal
import h5py
import traceback
from typing import Optional, Tuple, List, Union, Any

from src.utils.gamma_file_process import (
    read_gamma_binary,
    read_gamma_downsampled,
    read_gamma_region,
    read_gamma_pixel,
    complex_to_phase,
)


# ============================================================================
# TIFF文件读取
# ============================================================================

def read_tiff(file_path: str) -> Tuple[Optional[np.ndarray], Optional[float], Optional[Tuple[int, int]]]:
    """
    使用GDAL读取TIFF文件（完整读取）
    
    Args:
        file_path: TIFF文件路径
        
    Returns:
        tuple: (图像数据, nodata值, 原始尺寸(width, height)) 或 (None, None, None)
    """
    try:
        ds = gdal.Open(file_path)
        if ds is None:
            return None, None, None
        
        original_width = ds.RasterXSize
        original_height = ds.RasterYSize
        original_size = (original_width, original_height)
        band_count = ds.RasterCount
        
        # 获取Nodata值（从第一个波段）
        band1 = ds.GetRasterBand(1)
        nodata_value = band1.GetNoDataValue()
        
        if band_count == 1:
            data = band1.ReadAsArray()
        else:
            data = []
            for i in range(1, band_count + 1):
                band = ds.GetRasterBand(i)
                data.append(band.ReadAsArray())
            data = np.stack(data, axis=-1)
        
        ds = None
        return data, nodata_value, original_size
        
    except Exception as e:
        print(f"读取TIFF失败 {file_path}: {e}")
        traceback.print_exc()
        return None, None, None


def get_tiff_info(file_path: str) -> Tuple[Optional[Tuple[int, int]], Optional[int], Optional[float]]:
    """
    获取TIFF文件的基本信息（尺寸、波段数、nodata值），不读取实际数据
    
    Args:
        file_path: TIFF文件路径
        
    Returns:
        tuple: ((width, height), band_count, nodata_value) 或 (None, None, None)
    """
    try:
        ds = gdal.Open(file_path)
        if ds is None:
            return None, None, None
        
        width = ds.RasterXSize
        height = ds.RasterYSize
        band_count = ds.RasterCount
        nodata_value = ds.GetRasterBand(1).GetNoDataValue()
        
        ds = None
        return (width, height), band_count, nodata_value
        
    except Exception as e:
        print(f"获取TIFF信息失败 {file_path}: {e}")
        return None, None, None


def get_image_info(file_path: str) -> Tuple[Optional[Tuple[int, int]], Optional[int]]:
    """
    获取普通图像文件的基本信息（尺寸、通道数），不完全读取数据
    
    Args:
        file_path: 图像文件路径
        
    Returns:
        tuple: ((width, height), channels) 或 (None, None)
    """
    try:
        img = Image.open(file_path)
        width, height = img.size
        # 获取通道数
        mode_to_channels = {
            'L': 1,
            'LA': 2,
            'RGB': 3,
            'RGBA': 4,
            'CMYK': 4,
            'P': 1,
        }
        channels = mode_to_channels.get(img.mode, len(img.getbands()))
        img.close()
        return (width, height), channels
        
    except Exception as e:
        print(f"获取图像信息失败 {file_path}: {e}")
        return None, None


def read_tiff_downsampled(
    file_path: str, 
    max_size: int = 2048
) -> Tuple[Optional[np.ndarray], Optional[float], Optional[Tuple[int, int]], int]:
    """
    使用GDAL降采样读取TIFF，优先使用金字塔（Overview）
    
    Args:
        file_path: TIFF文件路径
        max_size: 最大边长（像素），默认2048
        
    Returns:
        tuple: (降采样后的图像数据, nodata值, 原始尺寸(width, height), 降采样因子) 
               或 (None, None, None, 1)
    """
    try:
        ds = gdal.Open(file_path)
        if ds is None:
            return None, None, None, 1
        
        original_width = ds.RasterXSize
        original_height = ds.RasterYSize
        original_size = (original_width, original_height)
        band_count = ds.RasterCount
        
        # 获取Nodata值
        band1 = ds.GetRasterBand(1)
        nodata_value = band1.GetNoDataValue()
        
        # 计算目标尺寸
        max_dim = max(original_width, original_height)
        if max_dim <= max_size:
            # 不需要降采样
            if band_count == 1:
                data = band1.ReadAsArray()
            else:
                data = []
                for i in range(1, band_count + 1):
                    band = ds.GetRasterBand(i)
                    data.append(band.ReadAsArray())
                data = np.stack(data, axis=-1)
            ds = None
            return data, nodata_value, original_size, 1
        
        # 计算降采样因子
        downsample_factor = int(np.ceil(max_dim / max_size))
        
        # 计算降采样后的尺寸
        target_width = original_width // downsample_factor
        target_height = original_height // downsample_factor
        
        # 检查是否有金字塔（Overview）
        overview_count = band1.GetOverviewCount()
        
        if overview_count > 0:
            # 找到最适合的金字塔级别
            best_overview = find_best_overview(band1, target_width, target_height)
            if best_overview is not None:
                # 从金字塔读取
                if band_count == 1:
                    data = best_overview.ReadAsArray(
                        buf_xsize=target_width, 
                        buf_ysize=target_height
                    )
                else:
                    data = []
                    for i in range(1, band_count + 1):
                        band = ds.GetRasterBand(i)
                        ov = find_best_overview(band, target_width, target_height)
                        if ov:
                            band_data = ov.ReadAsArray(
                                buf_xsize=target_width, 
                                buf_ysize=target_height
                            )
                        else:
                            band_data = band.ReadAsArray(
                                buf_xsize=target_width, 
                                buf_ysize=target_height
                            )
                        data.append(band_data)
                    data = np.stack(data, axis=-1)
                ds = None
                return data, nodata_value, original_size, downsample_factor
        
        # 没有金字塔，直接降采样读取
        if band_count == 1:
            data = band1.ReadAsArray(
                buf_xsize=target_width, 
                buf_ysize=target_height
            )
        else:
            data = []
            for i in range(1, band_count + 1):
                band = ds.GetRasterBand(i)
                band_data = band.ReadAsArray(
                    buf_xsize=target_width, 
                    buf_ysize=target_height
                )
                data.append(band_data)
            data = np.stack(data, axis=-1)
        
        ds = None
        return data, nodata_value, original_size, downsample_factor
        
    except Exception as e:
        print(f"降采样读取TIFF失败 {file_path}: {e}")
        traceback.print_exc()
        return None, None, None, 1


def read_tiff_region(
    file_path: str, 
    x1: int, y1: int, x2: int, y2: int
) -> Optional[np.ndarray]:
    """
    从TIFF文件读取指定区域
    
    Args:
        file_path: TIFF文件路径
        x1, y1: 左上角坐标
        x2, y2: 右下角坐标
        
    Returns:
        区域图像数据，或None
    """
    try:
        ds = gdal.Open(file_path)
        if ds is None:
            return None
        
        width = x2 - x1
        height = y2 - y1
        
        if width <= 0 or height <= 0:
            ds = None
            return None
        
        band_count = ds.RasterCount
        if band_count == 1:
            band = ds.GetRasterBand(1)
            region_data = band.ReadAsArray(x1, y1, width, height)
        else:
            data = []
            for i in range(1, band_count + 1):
                band = ds.GetRasterBand(i)
                data.append(band.ReadAsArray(x1, y1, width, height))
            region_data = np.stack(data, axis=-1)
        
        ds = None
        return region_data
        
    except Exception as e:
        print(f"读取TIFF区域失败 {file_path}: {e}")
        traceback.print_exc()
        return None


def read_tiff_pixel(file_path: str, x: int, y: int) -> Optional[Union[float, np.ndarray]]:
    """
    从TIFF文件读取指定像素值
    
    Args:
        file_path: TIFF文件路径
        x, y: 像素坐标
        
    Returns:
        像素值（单波段返回标量，多波段返回数组），或None
    """
    try:
        ds = gdal.Open(file_path)
        if ds is None:
            return None
        
        band_count = ds.RasterCount
        if band_count == 1:
            band = ds.GetRasterBand(1)
            value = band.ReadAsArray(x, y, 1, 1)[0, 0]
        else:
            values = []
            for i in range(1, band_count + 1):
                band = ds.GetRasterBand(i)
                values.append(band.ReadAsArray(x, y, 1, 1)[0, 0])
            value = np.array(values)
        
        ds = None
        return value
        
    except Exception as e:
        print(f"读取TIFF像素失败 {file_path}: {e}")
        traceback.print_exc()
        return None


def find_best_overview(band, target_width: int, target_height: int):
    """
    找到最适合目标尺寸的金字塔级别
    
    Args:
        band: GDAL Band对象
        target_width: 目标宽度
        target_height: 目标高度
        
    Returns:
        最佳的Overview对象，如果没有合适的返回None
    """
    overview_count = band.GetOverviewCount()
    if overview_count == 0:
        return None
    
    best_overview = None
    best_size_diff = float('inf')
    
    for i in range(overview_count):
        ov = band.GetOverview(i)
        ov_width = ov.XSize
        ov_height = ov.YSize
        
        # 选择尺寸大于等于目标尺寸且最接近的金字塔
        if ov_width >= target_width and ov_height >= target_height:
            size_diff = (ov_width - target_width) + (ov_height - target_height)
            if size_diff < best_size_diff:
                best_size_diff = size_diff
                best_overview = ov
    
    # 如果没有找到大于目标的，选择最大的那个
    if best_overview is None and overview_count > 0:
        best_overview = band.GetOverview(0)  # 第一个通常是最大的
    
    return best_overview


def find_best_overview_by_factor(band, target_factor: int):
    """
    根据目标降采样因子查找最佳金字塔级别
    
    Args:
        band: GDAL Band对象
        target_factor: 目标降采样因子
        
    Returns:
        最佳金字塔级别索引（0-based），如果没有合适的则返回None
    """
    overview_count = band.GetOverviewCount()
    if overview_count == 0:
        return None
    
    original_width = band.XSize
    best_level = None
    best_ratio = float('inf')
    
    for i in range(overview_count):
        overview = band.GetOverview(i)
        overview_width = overview.XSize
        factor = original_width / overview_width
        
        # 选择最接近但不超过目标因子的层级
        if factor <= target_factor * 1.5:
            ratio_diff = abs(factor - target_factor)
            if ratio_diff < best_ratio:
                best_ratio = ratio_diff
                best_level = i
    
    return best_level


def check_tiff_needs_overview(file_path: str, threshold: int = 4096) -> Tuple[bool, int, int]:
    """
    检查TIFF文件是否需要金字塔
    
    Args:
        file_path: TIFF文件路径
        threshold: 尺寸阈值，超过此值才建议创建金字塔
        
    Returns:
        tuple: (是否需要金字塔, 宽度, 高度)
    """
    try:
        ds = gdal.Open(file_path)
        if ds is None:
            return False, 0, 0
        
        width = ds.RasterXSize
        height = ds.RasterYSize
        band1 = ds.GetRasterBand(1)
        has_overview = band1.GetOverviewCount() > 0
        
        ds = None
        
        max_dim = max(width, height)
        needs_overview = max_dim > threshold and not has_overview
        
        return needs_overview, width, height
        
    except Exception as e:
        print(f"检查金字塔失败 {file_path}: {e}")
        return False, 0, 0


def build_tiff_overviews(
    file_path: str, 
    resample_method: str = "NEAREST"
) -> Tuple[bool, List[int]]:
    """
    为TIFF文件创建金字塔
    
    Args:
        file_path: TIFF文件路径
        resample_method: 重采样方法（NEAREST, AVERAGE, CUBIC等）
        
    Returns:
        tuple: (是否成功, 创建的级别列表)
    """
    try:
        # 以更新模式打开
        ds = gdal.Open(file_path, gdal.GA_Update)
        if ds is None:
            return False, []
        
        # 获取图像尺寸
        width = ds.RasterXSize
        height = ds.RasterYSize
        
        # 计算金字塔级别（2, 4, 8, 16, ...直到最小边小于256）
        overview_levels = []
        level = 2
        min_dim = min(width, height)
        while min_dim / level > 256:
            overview_levels.append(level)
            level *= 2
        overview_levels.append(level)  # 添加最后一级
        
        # 创建金字塔
        ds.BuildOverviews(resample_method, overview_levels)
        
        ds = None
        return True, overview_levels
        
    except Exception as e:
        print(f"创建金字塔失败 {file_path}: {e}")
        traceback.print_exc()
        return False, []


# ============================================================================
# 普通图像文件读取（PIL）
# ============================================================================

def read_image(file_path: str) -> Tuple[Optional[np.ndarray], Optional[Tuple[int, int]]]:
    """
    使用PIL读取普通图像文件
    
    Args:
        file_path: 图像文件路径
        
    Returns:
        tuple: (图像数据, 原始尺寸(width, height)) 或 (None, None)
    """
    try:
        img = Image.open(file_path)
        original_size = img.size  # (width, height)
        data = np.array(img)
        
        # 如果有alpha通道，去掉
        if data.ndim == 3 and data.shape[2] == 4:
            data = data[:, :, :3]
        
        return data, original_size
        
    except Exception as e:
        print(f"读取图像失败 {file_path}: {e}")
        traceback.print_exc()
        return None, None


def read_image_downsampled(
    file_path: str, 
    max_size: int = 2048
) -> Tuple[Optional[np.ndarray], Optional[Tuple[int, int]], int]:
    """
    使用PIL降采样读取普通图像
    
    Args:
        file_path: 图像文件路径
        max_size: 最大边长（像素）
        
    Returns:
        tuple: (降采样后的图像数据, 原始尺寸(width, height), 降采样因子) 
               或 (None, None, 1)
    """
    try:
        img = Image.open(file_path)
        original_size = img.size  # (width, height)
        original_width, original_height = original_size
        
        # 计算降采样因子
        max_dim = max(original_width, original_height)
        if max_dim > max_size:
            downsample_factor = int(np.ceil(max_dim / max_size))
            # 计算目标尺寸
            target_width = original_width // downsample_factor
            target_height = original_height // downsample_factor
            # 使用LANCZOS进行高质量降采样
            img = img.resize((target_width, target_height), Image.LANCZOS)
        else:
            downsample_factor = 1
        
        data = np.array(img)
        
        # 如果有alpha通道，去掉
        if data.ndim == 3 and data.shape[2] == 4:
            data = data[:, :, :3]
        
        return data, original_size, downsample_factor
        
    except Exception as e:
        print(f"降采样读取图像失败 {file_path}: {e}")
        traceback.print_exc()
        return None, None, 1


def read_image_region(
    file_path: str, 
    x1: int, y1: int, x2: int, y2: int
) -> Optional[np.ndarray]:
    """
    从普通图像文件读取指定区域
    
    Args:
        file_path: 图像文件路径
        x1, y1: 左上角坐标
        x2, y2: 右下角坐标
        
    Returns:
        区域图像数据，或None
    """
    try:
        img = Image.open(file_path)
        img = img.crop((x1, y1, x2, y2))
        data = np.array(img)
        
        if data.ndim == 3 and data.shape[2] == 4:
            data = data[:, :, :3]
        
        return data
        
    except Exception as e:
        print(f"读取图像区域失败 {file_path}: {e}")
        traceback.print_exc()
        return None


# ============================================================================
# 通用图像读取（自动检测格式）
# ============================================================================

def read_any_image(file_path: str) -> Tuple[Optional[np.ndarray], Optional[float], Optional[Tuple[int, int]]]:
    """
    自动检测文件格式并读取图像
    
    Args:
        file_path: 图像文件路径
        
    Returns:
        tuple: (图像数据, nodata值, 原始尺寸(width, height)) 或 (None, None, None)
    """
    ext = os.path.splitext(file_path)[1].lower()
    
    if ext in ['.tif', '.tiff']:
        return read_tiff(file_path)
    else:
        data, size = read_image(file_path)
        return data, None, size


def read_any_image_downsampled(
    file_path: str, 
    max_size: int = 2048
) -> Tuple[Optional[np.ndarray], Optional[float], Optional[Tuple[int, int]], int]:
    """
    自动检测文件格式并降采样读取图像
    
    Args:
        file_path: 图像文件路径
        max_size: 最大边长（像素）
        
    Returns:
        tuple: (降采样后的图像数据, nodata值, 原始尺寸(width, height), 降采样因子) 
               或 (None, None, None, 1)
    """
    ext = os.path.splitext(file_path)[1].lower()
    
    if ext in ['.tif', '.tiff']:
        return read_tiff_downsampled(file_path, max_size)
    else:
        data, size, factor = read_image_downsampled(file_path, max_size)
        return data, None, size, factor


def read_any_image_region(
    file_path: str, 
    x1: int, y1: int, x2: int, y2: int,
    is_gamma: bool = False,
    gamma_format: str = "float32",
    gamma_width: int = None,
    gamma_height: int = None
) -> Optional[np.ndarray]:
    """
    自动检测文件格式并读取指定区域
    
    Args:
        file_path: 图像文件路径
        x1, y1: 左上角坐标
        x2, y2: 右下角坐标
        is_gamma: 是否为GAMMA二进制文件
        gamma_format: GAMMA数据格式
        gamma_width: GAMMA图像宽度
        gamma_height: GAMMA图像高度
        
    Returns:
        区域图像数据，或None
    """
    if is_gamma:
        data = read_gamma_region(file_path, x1, y1, x2, y2, gamma_width, gamma_height, gamma_format)
        if gamma_format.startswith('cpx'):
            data = complex_to_phase(data)
        return data
    
    ext = os.path.splitext(file_path)[1].lower()
    
    if ext in ['.tif', '.tiff']:
        return read_tiff_region(file_path, x1, y1, x2, y2)
    else:
        return read_image_region(file_path, x1, y1, x2, y2)


def read_any_image_pixel(
    file_path: str, 
    x: int, y: int,
    is_gamma: bool = False,
    gamma_format: str = "float32",
    gamma_width: int = None,
    gamma_height: int = None
) -> Optional[Union[float, np.ndarray]]:
    """
    自动检测文件格式并读取指定像素
    
    Args:
        file_path: 图像文件路径
        x, y: 像素坐标
        is_gamma: 是否为GAMMA二进制文件
        gamma_format: GAMMA数据格式
        gamma_width: GAMMA图像宽度
        gamma_height: GAMMA图像高度
        
    Returns:
        像素值，或None
    """
    if is_gamma:
        value = read_gamma_pixel(file_path, x, y, gamma_width, gamma_height, gamma_format)
        if gamma_format.startswith('cpx'):
            value = np.angle(value)
        return value
    
    ext = os.path.splitext(file_path)[1].lower()
    
    if ext in ['.tif', '.tiff']:
        return read_tiff_pixel(file_path, x, y)
    else:
        # 对于普通图像，读取单像素区域
        region = read_image_region(file_path, x, y, x + 1, y + 1)
        if region is not None:
            if region.ndim == 2:
                return region[0, 0]
            else:
                return region[0, 0, :]
        return None


# ============================================================================
# HDF5文件读取
# ============================================================================

def list_h5_datasets(file_path: str, min_ndim: int = 2) -> List[Tuple[str, str, Tuple]]:
    """
    列出HDF5文件中的所有数据集
    
    Args:
        file_path: HDF5文件路径
        min_ndim: 最小维度要求
        
    Returns:
        数据集列表，每项为(名称, 形状字符串, 形状元组)
    """
    datasets = []
    
    try:
        with h5py.File(file_path, 'r') as h5f:
            def find_datasets(name, obj):
                if isinstance(obj, h5py.Dataset):
                    if obj.ndim >= min_ndim:
                        shape_str = f"({', '.join(map(str, obj.shape))})"
                        datasets.append((name, shape_str, obj.shape))
            
            h5f.visititems(find_datasets)
            
    except Exception as e:
        print(f"列出h5数据集失败 {file_path}: {e}")
        traceback.print_exc()
    
    return datasets


def read_h5_dataset(
    file_path: str, 
    dataset_name: str,
    frame_index: Optional[int] = None
) -> Tuple[Optional[np.ndarray], Optional[Tuple]]:
    """
    读取HDF5文件中的指定数据集
    
    Args:
        file_path: HDF5文件路径
        dataset_name: 数据集名称
        frame_index: 如果是3D时序数据，指定要读取的帧索引
        
    Returns:
        tuple: (数据, 原始形状) 或 (None, None)
    """
    try:
        with h5py.File(file_path, 'r') as h5f:
            if dataset_name not in h5f:
                return None, None
            
            dataset = h5f[dataset_name]
            original_shape = dataset.shape
            
            if dataset.ndim == 2:
                data = dataset[:].astype(np.float32)
            elif dataset.ndim == 3:
                if frame_index is not None:
                    # 读取指定帧
                    data = dataset[frame_index, :, :].astype(np.float32)
                elif dataset.shape[0] < dataset.shape[1] and dataset.shape[0] < dataset.shape[2]:
                    # 可能是多波段图像 (bands, height, width) -> (height, width, bands)
                    data = np.moveaxis(dataset[:], 0, -1).astype(np.float32)
                else:
                    # 默认读取第一帧
                    data = dataset[0, :, :].astype(np.float32)
            else:
                return None, None
            
            return data, original_shape
            
    except Exception as e:
        print(f"读取h5数据集失败 {file_path}/{dataset_name}: {e}")
        traceback.print_exc()
        return None, None


def read_h5_timeseries_metadata(file_path: str) -> Tuple[Optional[List[str]], Optional[Tuple[int, int, int]], int]:
    """
    读取HDF5时序数据的元信息（不加载实际数据）
    
    Args:
        file_path: HDF5文件路径
        
    Returns:
        tuple: (日期列表, 数据形状(frames, height, width), 起始帧索引) 
               或 (None, None, 0)
    """
    try:
        with h5py.File(file_path, 'r') as h5f:
            # 读取日期列表
            if 'date' not in h5f:
                return None, None, 0
            
            if 'timeseries' not in h5f:
                return None, None, 0
            
            # 读取日期
            dates = h5f['date'][:]
            if dates.dtype.kind == 'S' or dates.dtype.kind == 'U':
                date_list = [d.decode('utf-8') if isinstance(d, bytes) else str(d) for d in dates]
            else:
                date_list = [str(d) for d in dates]
            
            # 获取形状
            timeseries_shape = h5f['timeseries'].shape
            
            # 检查第一帧是否为参考帧（全0）
            start_index = 0
            if len(timeseries_shape) == 3:
                num_frames = timeseries_shape[0]
                num_dates = len(date_list)
                
                # 检查第一帧是否全为0（参考帧）
                first_frame = h5f['timeseries'][0, :, :]
                if np.all(first_frame == 0):
                    # 第一帧是参考帧，从第1帧开始读取
                    start_index = 1
                    print(f"检测到第0帧为参考帧（全0），将从第1帧开始读取")
                    print(f"总帧数：{num_frames}，有效数据帧：{num_frames - 1}")
                    print(f"日期数：{num_dates}")
            
            return date_list, timeseries_shape, start_index
            
    except Exception as e:
        print(f"读取h5时序元信息失败 {file_path}: {e}")
        traceback.print_exc()
        return None, None, 0


def read_h5_timeseries_frame(
    file_path: str, 
    frame_index: int,
    max_size: int = 2048
) -> Tuple[Optional[np.ndarray], Optional[Tuple[int, int]]]:
    """
    按需读取HDF5时序数据的指定帧（支持降采样）
    
    Args:
        file_path: HDF5文件路径
        frame_index: 帧索引
        max_size: 最大边长（像素）
        
    Returns:
        tuple: (图像数据, 原始尺寸(width, height)) 或 (None, None)
    """
    try:
        with h5py.File(file_path, 'r') as h5f:
            if 'timeseries' not in h5f:
                return None, None
            
            dataset = h5f['timeseries']
            if frame_index >= dataset.shape[0]:
                return None, None
            
            height, width = dataset.shape[1], dataset.shape[2]
            original_size = (width, height)
            
            # 读取指定帧
            data = dataset[frame_index, :, :].astype(np.float32)
            
            # 降采样
            max_dim = max(width, height)
            if max_dim > max_size:
                scale = max_size / max_dim
                new_width = int(width * scale)
                new_height = int(height * scale)
                
                # 使用PIL进行降采样
                img = Image.fromarray(data)
                img = img.resize((new_width, new_height), Image.LANCZOS)
                data = np.array(img)
            
            return data, original_size
            
    except Exception as e:
        print(f"读取h5时序帧失败 {file_path}[{frame_index}]: {e}")
        traceback.print_exc()
        return None, None


def read_h5_timeseries_pixel(
    file_path: str, 
    x: int, y: int,
    start_index: int = 0
) -> Optional[np.ndarray]:
    """
    读取HDF5时序数据中指定像素的所有时序值
    
    Args:
        file_path: HDF5文件路径
        x, y: 像素坐标
        start_index: 起始帧索引
        
    Returns:
        时序值数组，或None
    """
    try:
        with h5py.File(file_path, 'r') as h5f:
            if 'timeseries' not in h5f:
                return None
            
            dataset = h5f['timeseries']
            
            # 检查坐标是否有效
            if y >= dataset.shape[1] or x >= dataset.shape[2]:
                return None
            
            # 读取该像素在所有时间点的值
            values = dataset[start_index:, y, x].astype(np.float32)
            
            return values
            
    except Exception as e:
        print(f"读取h5时序像素失败 {file_path}[{x},{y}]: {e}")
        traceback.print_exc()
        return None
