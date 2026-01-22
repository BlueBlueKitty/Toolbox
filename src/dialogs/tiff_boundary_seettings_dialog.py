'''
Author: Yibo Yuan 2633669459@qq.com
Date: 2025-03-24 21:50:23
LastEditors: Yibo Yuan 2633669459@qq.com
LastEditTime: 2025-03-25 01:23:14
FilePath: \Toolbox\src\dialogs\tiff_boundary_seettings_dialog.py
Description: 

Copyright (c) 2025 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''
'''
Author: Yibo Yuan 2633669459@qq.com
Date: 2025-03-24
Description: TIFF边界转矢量的参数设置对话框

Copyright (c) 2025 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
                               QPushButton, QFileDialog, QComboBox, QCheckBox, 
                               QFormLayout, QDialogButtonBox, QGroupBox)
from PySide6.QtCore import Qt


class TiffBoundarySettingsDialog(QDialog):
    def __init__(self, parent=None):
        super(TiffBoundarySettingsDialog, self).__init__(parent)
        
        self.setWindowTitle("TIFF边界转矢量 - 参数设置")
        self.resize(500, 300)
        
        # 创建布局
        main_layout = QVBoxLayout(self)
        
        # 输入文件部分
        input_group = QGroupBox("输入设置")
        input_layout = QFormLayout()
        
        self.input_file = QLineEdit()
        self.input_file.setReadOnly(True)
        self.browse_input_btn = QPushButton("浏览...")
        self.browse_input_btn.clicked.connect(self.browse_input_file)
        
        input_file_layout = QHBoxLayout()
        input_file_layout.addWidget(self.input_file, 1)
        input_file_layout.addWidget(self.browse_input_btn)
        
        input_layout.addRow("输入TIFF文件:", input_file_layout)
        input_group.setLayout(input_layout)
        
        # 输出设置部分
        output_group = QGroupBox("输出设置")
        output_layout = QFormLayout()
        
        self.output_file = QLineEdit()
        self.output_file.setReadOnly(True)
        self.browse_output_btn = QPushButton("浏览...")
        self.browse_output_btn.clicked.connect(self.browse_output_file)
        
        output_file_layout = QHBoxLayout()
        output_file_layout.addWidget(self.output_file, 1)
        output_file_layout.addWidget(self.browse_output_btn)
        
        output_layout.addRow("输出矢量文件:", output_file_layout)
        
        self.output_format = QComboBox()
        self.output_format.addItems(["Shapefile (*.shp)", "KML (*.kml)", "KMZ (*.kmz)"])
        self.output_format.currentIndexChanged.connect(self.update_file_extension)
        output_layout.addRow("输出格式:", self.output_format)
        
        output_group.setLayout(output_layout)
        
        # 坐标系设置
        coord_group = QGroupBox("坐标系设置")
        coord_layout = QVBoxLayout()
        
        self.use_wgs84 = QCheckBox("转换为WGS84坐标系")
        self.use_wgs84.setChecked(True)
        self.use_original_crs = QCheckBox("使用原始TIFF坐标系")

        # 互斥性设置：选择一个时取消选择另一个
        self.use_original_crs.toggled.connect(lambda checked: self.use_wgs84.setChecked(not checked))
        self.use_wgs84.toggled.connect(lambda checked: self.use_original_crs.setChecked(not checked))
        
        coord_layout.addWidget(self.use_wgs84)
        coord_layout.addWidget(self.use_original_crs)
        coord_group.setLayout(coord_layout)
        
        # 添加所有分组到主布局
        main_layout.addWidget(input_group)
        main_layout.addWidget(output_group)
        main_layout.addWidget(coord_group)
        
        # 添加确定和取消按钮
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main_layout.addWidget(buttons)
        
        self.setLayout(main_layout)
    
    def browse_input_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择TIFF文件", "", "TIFF文件 (*.tif *.tiff);;所有文件 (*.*)"
        )
        if file_path:
            self.input_file.setText(file_path)
            # 如果还没有设置输出路径，则自动设置一个同名的输出文件
            if not self.output_file.text():
                base_name = os.path.splitext(os.path.basename(file_path))[0]
                output_ext = ".shp" if self.output_format.currentIndex() == 0 else \
                            ".kml" if self.output_format.currentIndex() == 1 else \
                            ".kmz"
                suggested_output = os.path.join(os.path.dirname(file_path), f"{base_name}_boundary{output_ext}")
                self.output_file.setText(suggested_output)
    
    def browse_output_file(self):
        current_format = self.output_format.currentIndex()
        filter_str = "Shapefile (*.shp)" if current_format == 0 else \
                     "KML (*.kml)" if current_format == 1 else \
                     "KMZ (*.kmz)"
                     
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存矢量文件", "", filter_str
        )
        if file_path:
            # 确保文件扩展名正确
            expected_ext = ".shp" if current_format == 0 else \
                          ".kml" if current_format == 1 else \
                          ".kmz"
            if not file_path.lower().endswith(expected_ext):
                file_path += expected_ext
                
            self.output_file.setText(file_path)
    
    def update_file_extension(self):
        """当用户更改输出格式时更新文件扩展名"""
        current_path = self.output_file.text()
        if not current_path:
            return
            
        # 获取当前路径的基本部分（不含扩展名）
        base_path = os.path.splitext(current_path)[0]
        
        # 根据当前选择添加正确的扩展名
        current_format = self.output_format.currentIndex()
        new_ext = ".shp" if current_format == 0 else \
                 ".kml" if current_format == 1 else \
                 ".kmz"
                 
        self.output_file.setText(base_path + new_ext)
    
    def get_settings(self):
        """返回用户设置的参数"""
        return {
            "input_file": self.input_file.text(),
            "output_file": self.output_file.text(),
            "to_wgs84": self.use_wgs84.isChecked(),
            "output_format": ["shp", "kml", "kmz"][self.output_format.currentIndex()]
        }