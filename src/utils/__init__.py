'''
Author: Yibo Yuan 2633669459@qq.com
Date: 2026-01-26
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
]
