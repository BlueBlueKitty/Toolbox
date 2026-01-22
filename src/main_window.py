'''
Author: Yibo Yuan 2633669459@qq.com
Date: 2025-03-24 21:01:28
LastEditors: Yibo Yuan 2633669459@qq.com
LastEditTime: 2025-03-25 09:29:00
FilePath: \Toolbox\src\main_window.py
Description: 

Copyright (c) 2025 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
from PySide6.QtWidgets import QMainWindow, QApplication, QMessageBox, QDialog
from PySide6.QtUiTools import QUiLoader
from PySide6.QtGui import QIcon
import sys
import traceback

# 导入自定义对话框
from src.dialogs import TiffBoundarySettingsDialog, PixelTimeSeriesViewerDialog

# 导入工具函数
from src.tools import tiff_boundary_to_vector

# 在QApplication之前先实例化
uiLoader = QUiLoader()

class MainWindow(QMainWindow):
    def __init__(self):
        super(MainWindow, self).__init__()
        
        # 获取 UI 文件的绝对路径
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)  # 获取项目根目录
        ui_file_path = os.path.join(project_root, 'ui', 'main_window.ui')
        
        # 加载 UI 文件
        self.ui = uiLoader.load(ui_file_path, self)
        
        # 设置应用程序图标
        icon_path = os.path.join(project_root, 'resources', 'toolbox.ico')
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        # 重要：必须将 UI 显示出来
        self.setCentralWidget(self.ui)
        
        # 绑定信号与槽
        self.init_signals()

    def init_signals(self):
        """
        初始化信号与槽的绑定
        """
        # 像素时序查看器按钮
        self.ui.button_pixel_time_series_viewer.clicked.connect(self.on_button_pixel_time_series_viewer_click)
        
        # TIFF边界转矢量按钮
        self.ui.button_tiff_boundary_to_vector.clicked.connect(self.on_button_tiff_boundary_to_vector_click)
    
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
        按钮点击事件的处理逻辑，弹出参数设置对话框
        """
        try:
            # 创建并显示参数设置对话框
            dialog = TiffBoundarySettingsDialog(self)
            result = dialog.exec()
            
            # 如果用户点击了确定按钮
            if result == QDialog.Accepted:
                # 获取用户设置的参数
                settings = dialog.get_settings()
                
                # 检查必要的参数是否已设置
                if not settings["input_file"]:
                    QMessageBox.warning(self, "参数错误", "请选择输入TIFF文件!")
                    return
                    
                if not settings["output_file"]:
                    QMessageBox.warning(self, "参数错误", "请指定输出矢量文件!")
                    return
                
                # 调用工具函数进行转换
                success = tiff_boundary_to_vector(
                    settings["input_file"], 
                    settings["output_file"], 
                    to_wgs84=settings["to_wgs84"], 
                    output_format=settings["output_format"]
                )
                
                # 显示结果
                if success:
                    QMessageBox.information(self, "成功", "TIFF边界已成功转换为矢量文件!")
                else:
                    QMessageBox.critical(self, "错误", "转换过程中出现错误，请查看控制台输出。")
                    
        except Exception as e:
            QMessageBox.critical(self, "错误", f"发生异常: {str(e)}")
            traceback.print_exc()


if __name__ == "__main__":
    # 创建应用程序实例
    app = QApplication(sys.argv)

    # 创建主窗口实例
    window = MainWindow()
    window.show()

    # 运行应用程序
    sys.exit(app.exec())