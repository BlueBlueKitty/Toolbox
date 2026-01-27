'''
Author: Yibo Yuan 2633669459@qq.com
Date: 2025-03-24 20:57:49
LastEditors: Yibo Yuan 2633669459@qq.com
Description: 

Copyright (c) 2025 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''
import os
import sys

# 【重要】设置 QtWebEngine 标志必须在任何 Qt 相关模块导入之前
# --ignore-certificate-errors 用于解决地图瓦片加载时的 SSL 握手失败问题
# --single-process 解决渲染进程崩溃问题
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--single-process --ignore-certificate-errors --ignore-ssl-errors"

# 以下导入虽然没用到，但是打包时需要用到
from osgeo import gdal, ogr, osr
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

# 将项目根目录添加到Python模块搜索路径
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.append(project_root)

# 然后再导入模块
from src.main_window import MainWindow
from PySide6.QtWidgets import QApplication

def main():
    """
    应用程序的入口函数
    """
    # 创建应用程序实例
    app = QApplication(sys.argv)

    # 创建主窗口实例
    window = MainWindow()
    window.show()

    # 运行应用程序事件循环
    sys.exit(app.exec())

if __name__ == "__main__":
    main()