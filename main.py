'''
Author: Yibo Yuan 2633669459@qq.com
Date: 2025-03-24 20:57:49
LastEditors: Yibo Yuan 2633669459@qq.com
LastEditTime: 2025-03-25 01:32:02
FilePath: \Toolbox\main.py
Description: 

Copyright (c) 2025 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''
import os
import sys
from osgeo import gdal, ogr, osr

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
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()