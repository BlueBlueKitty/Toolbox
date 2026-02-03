'''
Author: Yibo Yuan 2633669459@qq.com
Description: 工具模块

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

from .dem_utils import LocalDEMProcessor, calculate_area_km2
from .opentopography_client import (
    OpenTopographyClient, 
    DATASETS_CONFIG, 
    OpenTopographyError,
    AuthenticationError,
    RateLimitError
)
from .file_handler import (
    extract_bounding_box_from_vector, 
    extract_bounding_box_from_raster,
    get_raster_info,
    get_vector_layer_info,
    get_supported_vector_extensions,
    get_supported_raster_extensions,
    is_vector_file,
    is_raster_file
)

# 行政区划选择器需要数据库文件，单独导入
try:
    from .administrative_boundary import AdministrativeBoundarySelector
    ADMIN_BOUNDARY_AVAILABLE = True
except Exception as e:
    print(f"[WARNING] 无法导入 AdministrativeBoundarySelector: {e}")
    ADMIN_BOUNDARY_AVAILABLE = False
    AdministrativeBoundarySelector = None

# GAMMA二进制文件处理
from .gamma_file_process import (
    freadbkB,
    parse_par_file,
    get_dimensions_from_par,
    validate_dimensions,
    find_matching_par_file,
    find_valid_par_for_binary,
    read_gamma_binary,
    read_gamma_region,
    read_gamma_pixel,
    read_gamma_downsampled,
    complex_to_phase,
    complex_to_amplitude,
    complex_to_intensity,
    is_gamma_binary_file,
    get_gamma_file_info,
    GAMMA_FORMATS,
)

# 图像读取工具
from .image_io import (
    # TIFF读取
    read_tiff,
    read_tiff_downsampled,
    read_tiff_region,
    read_tiff_pixel,
    get_tiff_info,
    find_best_overview,
    find_best_overview_by_factor,
    check_tiff_needs_overview,
    build_tiff_overviews,
    # 普通图像读取
    read_image,
    read_image_downsampled,
    read_image_region,
    get_image_info,
    # 通用图像读取
    read_any_image,
    read_any_image_downsampled,
    read_any_image_region,
    read_any_image_pixel,
    # HDF5读取
    list_h5_datasets,
    read_h5_dataset,
    read_h5_dataset_downsampled,
    read_h5_timeseries_metadata,
    read_h5_timeseries_frame,
    read_h5_timeseries_pixel,
)

from .appimage_installer import AppImageInstaller
from .update_checker import UpdateChecker, UpdateError, NetworkError

__all__ = [
    'LocalDEMProcessor',
    'calculate_area_km2',
    'OpenTopographyClient',
    'DATASETS_CONFIG',
    'OpenTopographyError',
    'AuthenticationError',
    'RateLimitError',
    'extract_bounding_box_from_vector',
    'extract_bounding_box_from_raster',
    'get_raster_info',
    'get_vector_layer_info',
    'get_supported_vector_extensions',
    'get_supported_raster_extensions',
    'is_vector_file',
    'is_raster_file',
    'AdministrativeBoundarySelector',
    'ADMIN_BOUNDARY_AVAILABLE',
    # GAMMA相关
    'freadbkB',
    'parse_par_file',
    'get_dimensions_from_par',
    'validate_dimensions',
    'find_matching_par_file',
    'find_valid_par_for_binary',
    'read_gamma_binary',
    'read_gamma_region',
    'read_gamma_pixel',
    'read_gamma_downsampled',
    'complex_to_phase',
    'complex_to_amplitude',
    'complex_to_intensity',
    'is_gamma_binary_file',
    'get_gamma_file_info',
    'GAMMA_FORMATS',
    # 图像读取工具
    'read_tiff',
    'read_tiff_downsampled',
    'read_tiff_region',
    'read_tiff_pixel',
    'get_tiff_info',
    'find_best_overview',
    'find_best_overview_by_factor',
    'check_tiff_needs_overview',
    'build_tiff_overviews',
    'read_image',
    'read_image_downsampled',
    'read_image_region',
    'get_image_info',
    'read_any_image',
    'read_any_image_downsampled',
    'read_any_image_region',
    'read_any_image_pixel',
    'list_h5_datasets',
    'read_h5_dataset',
    'read_h5_dataset_downsampled',
    'read_h5_timeseries_metadata',
    'read_h5_timeseries_frame',
    'read_h5_timeseries_pixel',
    'AppImageInstaller',
    # 更新检查
    'UpdateChecker',
    'UpdateError',
    'NetworkError',
]
