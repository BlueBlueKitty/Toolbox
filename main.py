'''
Author: Yibo Yuan 2633669459@qq.com
Description: 应用程序入口

Copyright (c) 2025 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''
import os
import sys

# =====================================================================
# 【关键】PROJ/GDAL 环境变量设置 - 必须在导入 GDAL 之前完成
# =====================================================================
_proj_path = None

# 1. 打包环境下的路径设置
if hasattr(sys, "_MEIPASS"):
    base_path = sys._MEIPASS
    
    # PROJ - 记录路径，稍后使用 osr.SetPROJSearchPaths 设置
    proj_candidates = [
        os.path.join(base_path, "proj_data"),
        os.path.join(base_path, "proj"),
    ]
    for p in proj_candidates:
        if os.path.exists(os.path.join(p, "proj.db")):
            _proj_path = p
            os.environ["PROJ_LIB"] = p
            os.environ["PROJ_DATA"] = p
            break
            
    # GDAL
    gdal_candidates = [
        os.path.join(base_path, "gdal_data"),
    ]
    for p in gdal_candidates:
        if os.path.exists(p):
            os.environ["GDAL_DATA"] = p
            break
else:
    # 2. 开发环境下的路径设置（.venv虚拟环境）
    # 检查虚拟环境中的 PROJ 数据路径
    venv_proj_candidates = [
        os.path.join(sys.prefix, "Lib", "site-packages", "osgeo", "data", "proj"),
        os.path.join(sys.prefix, "Library", "share", "proj"),
        os.path.join(sys.prefix, "share", "proj"),
    ]
    
    for p in venv_proj_candidates:
        if os.path.exists(os.path.join(p, "proj.db")):
            _proj_path = p
            # 显式设置环境变量，确保PROJ能找到数据文件
            os.environ["PROJ_LIB"] = p
            os.environ["PROJ_DATA"] = p
            break
    
    # GDAL 数据路径
    venv_gdal_candidates = [
        os.path.join(sys.prefix, "Lib", "site-packages", "osgeo", "data", "gdal"),
        os.path.join(sys.prefix, "Library", "share", "gdal"),
        os.path.join(sys.prefix, "share", "gdal"),
    ]
    
    for p in venv_gdal_candidates:
        if os.path.exists(p):
            os.environ["GDAL_DATA"] = p
            break

# 将项目根目录添加到Python模块搜索路径
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.append(project_root)

from osgeo import gdal, ogr, osr

# 在打包环境下，使用 osr.SetPROJSearchPaths() 设置 PROJ 搜索路径
# 这是确保 PROJ 能找到 proj.db 的关键步骤
if _proj_path:
    osr.SetPROJSearchPaths([_proj_path])

# 启用 GDAL/OGR 异常（GDAL 4.0 将默认启用）
gdal.UseExceptions()
ogr.UseExceptions()

# 以下导入虽然没用到，但是打包时需要用到
import numpy as np
try:
    import matplotlib.cm as cm
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    print("警告: matplotlib未安装，部分colormap功能将不可用")
from PIL import Image
from osgeo import gdal
import traceback
import h5py
import requests
from src.main_window import MainWindow
from PySide6.QtWidgets import QApplication
from src.utils.font_config import configure_matplotlib_font, configure_pyside6_font
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebChannel import QWebChannel
WEBENGINE_AVAILABLE = True
import sqlite3

def main():
    """
    应用程序的入口函数
    """
    
    # 在Linux系统上配置Qt和Chromium环境变量
    # 这些配置与 build_linux.sh 中的 AppRun 脚本保持一致，确保开发环境和打包环境行为一致
    if sys.platform.startswith('linux'):
        print("检测到Linux系统，正在配置Qt和Chromium环境变量... ", sys.platform)    
        # Qt 兼容性设置 - 解决不同系统上的图形渲染问题
        os.environ['QT_XCB_GL_INTEGRATION'] = 'none'
        os.environ['LIBGL_ALWAYS_SOFTWARE'] = '1'
        os.environ['QT_QUICK_BACKEND'] = 'software'
        
        # QtWebEngine 设置 - 禁用 GPU 加速
        os.environ['QTWEBENGINE_DISABLE_SANDBOX'] = '1'
        os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = '--disable-gpu --disable-software-rasterizer --no-sandbox --disable-dev-shm-usage'
        
        # 强制使用 xcb 平台插件（X11）
        os.environ['QT_QPA_PLATFORM'] = 'xcb'
    
    # 创建应用程序实例
    app = QApplication(sys.argv)
    
    # 配置字体（必须在创建 QApplication 后）
    configure_pyside6_font(app)
    if MATPLOTLIB_AVAILABLE:
        configure_matplotlib_font()

    # 创建主窗口实例
    window = MainWindow()
    window.show()

    # 运行应用程序事件循环
    sys.exit(app.exec())

if __name__ == "__main__":
    main()