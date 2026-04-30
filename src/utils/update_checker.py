'''
Author: Yibo Yuan 2633669459@qq.com
Description: 自动更新检查器
    使用 GitHub Releases API 检测新版本，并支持自动下载和安装。

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
import sys
import tempfile
import subprocess
import platform
from pathlib import Path
from typing import Optional, Dict, Any, Callable
from packaging import version as pkg_version

import requests

from src.version import __version__, VERSION_JSON_URL, GITHUB_RELEASES_URL, APP_NAME, GITHUB_REPO


class UpdateError(Exception):
    """更新过程中的错误"""
    pass


class NetworkError(UpdateError):
    """网络连接错误（如无法访问GitHub）"""
    pass


class UpdateChecker:
    """
    自动更新检查器
    
    使用 GitHub Releases API 检测新版本，支持自动下载和安装。
    """
    
    # 请求超时时间（秒）
    TIMEOUT = 10
    
    def __init__(self):
        """__init__。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self.current_version = __version__
        self.latest_release_info: Optional[Dict[str, Any]] = None
    
    def check_for_updates(self) -> Optional[Dict[str, Any]]:
        """
        检查是否有新版本可用
        
        从仓库中的 version.json 文件读取版本信息（通过 raw.githubusercontent.com）
        这种方式避免了 GitHub API 的速率限制问题。
        
        Returns:
            如果有新版本，返回包含以下键的字典：
            - version: 新版本号
            - name: Release 名称
            - body: 更新日志（Markdown）
            - html_url: 浏览器打开的 Release 页面
            - download_url: 当前平台对应的下载链接
            如果没有新版本或检查失败，返回 None
            
        Raises:
            NetworkError: 无法连接到 GitHub（如网络问题）
        """
        try:
            # 从仓库的 version.json 文件读取版本信息
            response = requests.get(
                VERSION_JSON_URL,
                timeout=self.TIMEOUT
            )
            response.raise_for_status()
            
            version_data = response.json()
            self.latest_release_info = version_data
            
            latest_version = version_data.get('version', '').lstrip('v')
            
            # 版本比较
            if self._is_newer_version(latest_version):
                # 获取当前平台对应的下载链接
                download_url = self._get_download_url_for_platform_from_json(version_data)
                
                return {
                    'version': latest_version,
                    'name': version_data.get('name', f'v{latest_version}'),
                    'body': version_data.get('changelog', ''),
                    'html_url': version_data.get('release_url', GITHUB_RELEASES_URL + '/latest'),
                    'download_url': download_url,
                }
            
            return None
            
        except requests.exceptions.Timeout:
            raise NetworkError("连接 GitHub 超时，请检查网络连接。")
        except requests.exceptions.ConnectionError:
            raise NetworkError("无法连接到 GitHub，请检查网络连接。\n如果您在中国大陆，可能需要使用代理。")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise NetworkError(f"未找到版本信息文件。\n请确保仓库中存在 version.json 文件。\n\n您可以访问：{GITHUB_RELEASES_URL}")
            raise NetworkError(f"获取版本信息失败: HTTP {e.response.status_code}\n\n您可以访问：{GITHUB_RELEASES_URL}")
        except ValueError as e:
            raise UpdateError(f"版本信息文件格式错误: {e}")
        except Exception as e:
            raise UpdateError(f"检查更新时发生错误: {e}")
    
    def _is_newer_version(self, latest_version: str) -> bool:
        """比较版本号，判断是否有更新"""
        try:
            return pkg_version.parse(latest_version) > pkg_version.parse(self.current_version)
        except Exception:
            # 如果版本解析失败，简单字符串比较
            return latest_version != self.current_version
    
    def _get_download_url_for_platform_from_json(self, version_data: dict) -> Optional[str]:
        """从 version.json 数据中根据当前平台获取对应的下载链接"""
        system = platform.system().lower()
        
        # 从 downloads 字段获取平台对应的下载链接
        downloads = version_data.get('downloads', {})
        
        if system == 'windows':
            return downloads.get('windows')
        elif system == 'linux':
            return downloads.get('linux')
        elif system == 'darwin':
            return downloads.get('mac') or downloads.get('macos')
        
        # 如果没有找到特定平台的下载链接，返回通用下载链接
        return downloads.get('universal')
    
    def download_update(
        self, 
        url: str, 
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> str:
        """
        下载更新文件
        
        Args:
            url: 下载链接
            progress_callback: 进度回调函数，参数为 (已下载字节数, 总字节数)
            
        Returns:
            下载文件的本地路径
            
        Raises:
            NetworkError: 下载失败
        """
        try:
            # 从URL获取文件名
            filename = url.split('/')[-1]
            
            # 使用临时目录
            temp_dir = tempfile.gettempdir()
            dest_path = os.path.join(temp_dir, filename)
            
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(dest_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_size > 0:
                            progress_callback(downloaded, total_size)
            
            return dest_path
            
        except requests.exceptions.Timeout:
            raise NetworkError("下载超时，请检查网络连接。")
        except requests.exceptions.ConnectionError:
            raise NetworkError("下载失败，无法连接到服务器。")
        except Exception as e:
            raise UpdateError(f"下载更新失败: {e}")
    
    def apply_update(self, downloaded_file: str) -> bool:
        """
        应用更新（启动安装程序或替换AppImage）
        
        Args:
            downloaded_file: 下载的更新文件路径
            
        Returns:
            True 如果启动成功（应用将退出以完成更新）
        """
        # 检测是否有其他实例在运行，避免安装被占用
        if self._has_other_instance_running():
            raise UpdateError("检测到另一个 Toolbox 正在运行，请先关闭后再安装更新。")

        system = platform.system().lower()
        
        if system == 'windows':
            return self._apply_update_windows(downloaded_file)
        elif system == 'linux':
            return self._apply_update_linux(downloaded_file)
        else:
            raise UpdateError(f"不支持的操作系统: {system}")
    
    def _apply_update_windows(self, installer_path: str) -> bool:
        """Windows: 启动安装程序"""
        try:
            # 使用 subprocess.Popen 启动安装程序，不等待
            subprocess.Popen([installer_path], shell=True)
            return True
        except Exception as e:
            raise UpdateError(f"启动安装程序失败: {e}")
    
    def _apply_update_linux(self, appimage_path: str) -> bool:
        """Linux: 替换 AppImage 并重启"""
        try:
            current_appimage = os.environ.get('APPIMAGE')
            
            if not current_appimage:
                # 不是以 AppImage 运行，直接打开下载的文件所在目录
                raise UpdateError("当前不是以 AppImage 运行，请手动安装下载的文件。")
            
            # 赋予执行权限
            os.chmod(appimage_path, 0o755)
            
            # 创建一个脚本来替换和重启
            # 这个脚本会在当前进程退出后执行
            script_content = f'''#!/bin/bash
sleep 1
cp "{appimage_path}" "{current_appimage}"
chmod +x "{current_appimage}"
"{current_appimage}" &
'''
            script_path = os.path.join(tempfile.gettempdir(), 'update_appimage.sh')
            with open(script_path, 'w') as f:
                f.write(script_content)
            os.chmod(script_path, 0o755)
            
            # 启动替换脚本
            subprocess.Popen(['bash', script_path], start_new_session=True)
            
            return True
            
        except UpdateError:
            raise
        except Exception as e:
            raise UpdateError(f"应用更新失败: {e}")

    def _has_other_instance_running(self) -> bool:
        """检查是否有其他 Toolbox 实例在运行（不包含当前进程）"""
        system = platform.system().lower()
        current_pid = os.getpid()
        try:
            if system == 'windows':
                # 使用 PowerShell 获取进程 PID 列表
                result = subprocess.run(
                    ["powershell", "-Command", "Get-Process Toolbox_win -ErrorAction SilentlyContinue | Select-Object -Expand Id"],
                    capture_output=True,
                    text=True
                )
                pids = [int(line.strip()) for line in result.stdout.splitlines() if line.strip().isdigit()]
                return any(pid != current_pid for pid in pids)
            elif system == 'linux':
                # 使用 pgrep 获取 PID 列表（进程名为 Toolbox_linux 或 AppImage 名称）
                patterns = ["Toolbox_linux"]
                appimage_path = os.environ.get("APPIMAGE", "")
                if appimage_path:
                    patterns.append(os.path.basename(appimage_path))
                pids = set()
                for pattern in patterns:
                    result = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
                    for line in result.stdout.splitlines():
                        if line.strip().isdigit():
                            pids.add(int(line.strip()))
                return any(pid != current_pid for pid in pids)
        except Exception:
            # 检测失败则不阻止更新
            return False
        return False
