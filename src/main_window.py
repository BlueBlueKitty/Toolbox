'''
Author: Yibo Yuan 2633669459@qq.com
Date: 2025-03-24 21:01:28
LastEditors: Yibo Yuan 2633669459@qq.com
LastEditTime: 2026-01-22
FilePath: \Toolbox\src\main_window.py
Description: 主窗口 - 使用纯Python代码创建UI

Copyright (c) 2025 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
from PySide6.QtWidgets import (QMainWindow, QApplication, QMessageBox, QDialog,
                               QWidget, QVBoxLayout, QGridLayout, QPushButton, QLabel,
                               QGroupBox, QScrollArea)
from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt
import sys
import traceback

# 导入自定义对话框
from src.dialogs import (TiffBoundarySettingsDialog, PixelTimeSeriesViewerDialog,
                         LocalImageViewerDialog, DEMAcquisitionDialog)

# 导入工具函数
from src.utils import tiff_boundary_to_vector


class MainWindow(QMainWindow):
    def __init__(self):
        super(MainWindow, self).__init__()
        
        # 设置窗口属性
        self.setWindowTitle("遥感工具箱")
        self.resize(800, 600)
        
        # 获取项目根目录
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        
        # 设置应用程序图标
        icon_path = os.path.join(project_root, 'resources', 'toolbox.ico')
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        # 创建UI
        self._create_ui()

    def _create_ui(self):
        """创建用户界面"""
        # 创建中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 主布局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)
        
        # 标题
        title_label = QLabel("遥感工具箱")
        title_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #2c3e50;")
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)
        
        # 副标题
        subtitle_label = QLabel("Remote Sensing Toolbox")
        subtitle_label.setStyleSheet("font-size: 14px; color: #7f8c8d;")
        subtitle_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(subtitle_label)
        
        main_layout.addSpacing(20)
        
        # 创建滚动区域
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.NoFrame)
        
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(20)
        
        # =============== 图像分析工具组 ===============
        image_analysis_group = QGroupBox("图像分析工具")
        image_analysis_group.setStyleSheet("""
            QGroupBox {
                font-size: 16px;
                font-weight: bold;
                border: 2px solid #3498db;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 5px;
                color: #3498db;
            }
        """)
        
        image_analysis_layout = QGridLayout()
        image_analysis_layout.setSpacing(10)
        
        # 图像局部查看器按钮
        self.button_local_image_viewer = QPushButton("图像局部查看器")
        self.button_local_image_viewer.setMinimumHeight(50)
        self.button_local_image_viewer.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QPushButton:pressed {
                background-color: #21618c;
            }
        """)
        self.button_local_image_viewer.clicked.connect(self.on_button_local_image_viewer_click)
        image_analysis_layout.addWidget(self.button_local_image_viewer, 0, 0)
        
        # 像素时序查看器按钮
        self.button_pixel_time_series_viewer = QPushButton("像素时序查看器")
        self.button_pixel_time_series_viewer.setMinimumHeight(50)
        self.button_pixel_time_series_viewer.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QPushButton:pressed {
                background-color: #21618c;
            }
        """)
        self.button_pixel_time_series_viewer.clicked.connect(self.on_button_pixel_time_series_viewer_click)
        image_analysis_layout.addWidget(self.button_pixel_time_series_viewer, 0, 1)
        
        image_analysis_group.setLayout(image_analysis_layout)
        scroll_layout.addWidget(image_analysis_group)
        
        # =============== 栅格处理工具组 ===============
        raster_tools_group = QGroupBox("栅格处理工具")
        raster_tools_group.setStyleSheet("""
            QGroupBox {
                font-size: 16px;
                font-weight: bold;
                border: 2px solid #27ae60;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 5px;
                color: #27ae60;
            }
        """)
        
        raster_tools_layout = QGridLayout()
        raster_tools_layout.setSpacing(10)
        
        # TIFF边界转矢量按钮
        self.button_tiff_boundary_to_vector = QPushButton("TIFF边界转矢量")
        self.button_tiff_boundary_to_vector.setMinimumHeight(50)
        self.button_tiff_boundary_to_vector.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #229954;
            }
            QPushButton:pressed {
                background-color: #1e8449;
            }
        """)
        self.button_tiff_boundary_to_vector.clicked.connect(self.on_button_tiff_boundary_to_vector_click)
        raster_tools_layout.addWidget(self.button_tiff_boundary_to_vector, 0, 0)
        
        # DEM数据获取按钮
        self.button_dem_acquisition = QPushButton("DEM数据获取")
        self.button_dem_acquisition.setMinimumHeight(50)
        self.button_dem_acquisition.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #229954;
            }
            QPushButton:pressed {
                background-color: #1e8449;
            }
        """)
        self.button_dem_acquisition.clicked.connect(self.on_button_dem_acquisition_click)
        raster_tools_layout.addWidget(self.button_dem_acquisition, 0, 1)
        
        raster_tools_group.setLayout(raster_tools_layout)
        scroll_layout.addWidget(raster_tools_group)
        
        # 添加弹性空间
        scroll_layout.addStretch()
        
        scroll_area.setWidget(scroll_widget)
        main_layout.addWidget(scroll_area)
        
        # 底部信息
        info_label = QLabel("© 2026 Yibo Yuan. All Rights Reserved.")
        info_label.setStyleSheet("font-size: 12px; color: #95a5a6;")
        info_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(info_label)
    
    def on_button_local_image_viewer_click(self):
        """
        图像局部查看器按钮点击事件
        """
        try:
            # 创建并显示图像局部查看器对话框
            dialog = LocalImageViewerDialog(self)
            dialog.exec()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开图像局部查看器失败: {str(e)}")
            traceback.print_exc()
    
    def on_button_pixel_time_series_viewer_click(self):
        """
        像素时序查看器按钮点击事件
        """
        try:
            # 创建并显示像素时序查看器对话框
            dialog = PixelTimeSeriesViewerDialog(self)
            dialog.exec()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开像素时序查看器失败: {str(e)}")
            traceback.print_exc()
    
    def on_button_tiff_boundary_to_vector_click(self):
        """
        按钮点击事件的处理逻辑，弹出参数设置对话框并执行转换（执行逻辑已迁移到对话框类）
        """
        try:
            dialog = TiffBoundarySettingsDialog(self)
            result = dialog.exec()
            if result == QDialog.Accepted:
                dialog.execute_conversion()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"发生异常: {str(e)}")
            traceback.print_exc()
    
    def on_button_dem_acquisition_click(self):
        """
        DEM数据获取按钮点击事件
        """
        try:
            # 创建并显示DEM数据获取对话框
            dialog = DEMAcquisitionDialog(self)
            dialog.exec()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开DEM数据获取工具失败: {str(e)}")
            traceback.print_exc()


if __name__ == "__main__":
    # 创建应用程序实例
    app = QApplication(sys.argv)

    # 创建主窗口实例
    window = MainWindow()
    window.show()

    # 运行应用程序
    sys.exit(app.exec())