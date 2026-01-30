'''
Author: Yibo Yuan 2633669459@qq.com
Description: OpenTopography API客户端
    实现从OpenTopography下载DEM数据的功能

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
import requests
import time
from typing import Optional, Dict, Any, List, Callable


# DEM数据集配置
DATASETS_CONFIG = {
    'SRTMGL3': {'resolution': '90m', 'limit': 4050000, 'description': 'SRTM GL3 90m'},
    'SRTMGL1': {'resolution': '30m', 'limit': 450000, 'description': 'SRTM GL1 30m'},
    'SRTMGL1_E': {'resolution': '30m', 'limit': 450000, 'description': 'SRTM GL1 Ellipsoidal 30m'},
    'AW3D30': {'resolution': '30m', 'limit': 450000, 'description': 'ALOS World 3D 30m'},
    'AW3D30_E': {'resolution': '30m', 'limit': 450000, 'description': 'ALOS World 3D Ellipsoidal 30m'},
    'SRTM15Plus': {'resolution': '500m', 'limit': 125000000, 'description': 'SRTM15+ 500m'},
    'NASADEM': {'resolution': '30m', 'limit': 450000, 'description': 'NASADEM 30m'},
    'COP30': {'resolution': '30m', 'limit': 450000, 'description': 'Copernicus DEM 30m'},
    'COP90': {'resolution': '90m', 'limit': 4050000, 'description': 'Copernicus DEM 90m'},
    'EU_DTM': {'resolution': '30m', 'limit': 450000, 'description': 'EU DTM 30m'},
    'GEDI_L3': {'resolution': '1000m', 'limit': 500000000, 'description': 'GEDI L3 1000m'},
    'GEBCOIceTopo': {'resolution': '500m', 'limit': 125000000, 'description': 'GEBCO Ice Topo 500m'},
    'GEBCOSubIceTopo': {'resolution': '500m', 'limit': 125000000, 'description': 'GEBCO Sub-Ice Topo 500m'},
}


class OpenTopographyError(Exception):
    """OpenTopography API错误基类"""
    pass


class AuthenticationError(OpenTopographyError):
    """认证错误"""
    pass


class RateLimitError(OpenTopographyError):
    """速率限制错误"""
    pass


class OpenTopographyClient:
    """OpenTopography API客户端"""
    
    BASE_URL = "https://portal.opentopography.org/API"
    TIMEOUT = 60
    MAX_RETRIES = 3
    RETRY_DELAY = 5
    
    def __init__(self, api_key: str, download_path: Optional[str] = None):
        """
        初始化客户端
        
        Args:
            api_key: OpenTopography API密钥
            download_path: 下载文件保存路径
        """
        self.api_key = api_key
        self.download_path = download_path or os.path.expanduser("~/Downloads/DEM_downloads")
        self.session = requests.Session()
        self.session.headers.update({
            'accept': '*/*',
            'User-Agent': 'Toolbox-DEM-Downloader/1.0'
        })
    
    def get_available_datasets(self) -> Dict[str, Dict[str, Any]]:
        """获取所有可用的数据集信息"""
        return DATASETS_CONFIG.copy()
    
    def validate_area_for_dataset(
        self, 
        south: float, 
        north: float, 
        west: float, 
        east: float, 
        dataset_name: str
    ) -> Dict[str, Any]:
        """
        验证给定区域是否适合特定数据集
        """
        from .dem_utils import calculate_area_km2
        
        if dataset_name not in DATASETS_CONFIG:
            raise ValueError(f"未知的数据集: {dataset_name}")
        
        area = calculate_area_km2(south, north, west, east)
        limit = DATASETS_CONFIG[dataset_name]['limit']
        
        return {
            'area': area,
            'limit': limit,
            'is_within_limit': area <= limit,
            'excess_ratio': area / limit if limit > 0 else float('inf')
        }
    
    def download(
        self,
        dataset_name: str,
        south: float,
        north: float,
        west: float,
        east: float,
        output_path: Optional[str] = None,
        custom_filename: Optional[str] = None,
        gui_logger: Optional[Callable] = None,
        is_running: Optional[Callable] = None
    ) -> Optional[str]:
        """
        下载DEM数据
        
        Args:
            dataset_name: 数据集名称
            south, north, west, east: 边界坐标
            output_path: 输出文件路径
            custom_filename: 自定义文件名
            gui_logger: 日志回调函数
            is_running: 检查是否继续运行的回调函数
            
        Returns:
            下载的文件路径，失败返回None
        """
        if dataset_name not in DATASETS_CONFIG:
            raise ValueError(f"不支持的数据集: {dataset_name}")
        
        # 验证坐标
        if south >= north:
            raise ValueError("南纬度必须小于北纬度")
        if west >= east:
            raise ValueError("西经度必须小于东经度")
        
        # 准备请求参数
        params = {
            'demtype': dataset_name,
            'south': south,
            'north': north,
            'west': west,
            'east': east,
            'outputFormat': 'GTiff',
            'API_Key': self.api_key
        }
        
        url = f"{self.BASE_URL}/globaldem"
        
        if gui_logger:
            gui_logger(f"正在请求 {dataset_name} 数据...")
        
        # 执行请求
        for attempt in range(self.MAX_RETRIES):
            try:
                if is_running and not is_running():
                    if gui_logger:
                        gui_logger("下载已取消")
                    return None
                
                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.TIMEOUT,
                    stream=True
                )
                
                # 检查响应
                self._check_response(response)
                
                # 下载文件
                return self._download_file(
                    response, 
                    output_path, 
                    custom_filename,
                    dataset_name,
                    gui_logger,
                    is_running
                )
                
            except requests.exceptions.RequestException as e:
                if gui_logger:
                    gui_logger(f"请求失败 (尝试 {attempt + 1}/{self.MAX_RETRIES}): {e}")
                
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(self.RETRY_DELAY)
                else:
                    raise OpenTopographyError(f"请求失败: {e}")
    
    def _check_response(self, response: requests.Response):
        """检查API响应"""
        if response.status_code == 401:
            error_msg = response.text
            if "rate limit" in error_msg.lower():
                raise RateLimitError("API速率限制已达到")
            else:
                raise AuthenticationError("API密钥无效或认证失败")
        elif response.status_code == 400:
            raise OpenTopographyError(f"错误请求: {response.text}")
        elif response.status_code == 204:
            raise OpenTopographyError("该区域没有可用的DEM数据")
        elif response.status_code == 500:
            raise OpenTopographyError("服务器内部错误")
        elif response.status_code != 200:
            raise OpenTopographyError(f"API错误 {response.status_code}: {response.text}")
    
    def _download_file(
        self,
        response: requests.Response,
        output_path: Optional[str],
        custom_filename: Optional[str],
        dataset_name: str,
        gui_logger: Optional[Callable],
        is_running: Optional[Callable]
    ) -> str:
        """下载文件到本地"""
        # 确定文件名
        if output_path:
            filepath = output_path
        else:
            if custom_filename:
                filename = custom_filename
            else:
                # 尝试从响应头获取文件名
                content_disposition = response.headers.get('content-disposition', '')
                if 'filename=' in content_disposition:
                    filename = content_disposition.split('filename=')[1].strip('"')
                else:
                    filename = f"globaldem_{dataset_name}.tif"
            
            # 确保下载目录存在
            os.makedirs(self.download_path, exist_ok=True)
            filepath = os.path.join(self.download_path, filename)
        
        # 确保输出目录存在
        output_dir = os.path.dirname(filepath)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        
        if gui_logger:
            gui_logger(f"正在下载到: {filepath}")
        
        # 流式下载
        total_size = int(response.headers.get('content-length', 0))
        downloaded_size = 0
        
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    # 检查是否需要停止下载
                    if is_running is not None and not is_running():
                        f.close()
                        try:
                            os.remove(filepath)
                        except:
                            pass
                        raise OpenTopographyError("下载已停止")
                    
                    f.write(chunk)
                    downloaded_size += len(chunk)
                    
                    # 显示下载进度
                    if total_size > 0 and gui_logger:
                        progress = downloaded_size / total_size * 100
                        if downloaded_size % (1024 * 100) == 0:  # 每100KB更新一次
                            gui_logger(f"下载进度: {progress:.1f}%")
        
        if gui_logger:
            gui_logger(f"文件已下载: {filepath}")
        
        return filepath
    
    def validate_api_key(self) -> bool:
        """
        验证API密钥是否有效
        通过发送一个小范围请求来测试
        """
        try:
            params = {
                'demtype': 'SRTMGL3',
                'south': 50.0,
                'north': 50.01,
                'west': 14.35,
                'east': 14.36,
                'outputFormat': 'GTiff',
                'API_Key': self.api_key
            }
            
            response = self.session.get(
                f"{self.BASE_URL}/globaldem",
                params=params,
                timeout=30,
                stream=True
            )
            
            return response.status_code == 200
            
        except AuthenticationError:
            return False
        except Exception:
            # 其他错误可能是网络问题，不一定是API密钥问题
            return True
