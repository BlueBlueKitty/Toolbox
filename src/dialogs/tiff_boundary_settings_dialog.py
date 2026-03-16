'''
Author: Yibo Yuan 2633669459@qq.com

Description: 

Copyright (c) 2025 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''
'''
Author: Yibo Yuan 2633669459@qq.com
Description: TIFF边界转矢量的参数设置对话框

Copyright (c) 2025 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
                               QPushButton, QFileDialog, QCheckBox, 
                               QFormLayout, QDialogButtonBox, QGroupBox)
from PySide6.QtCore import Qt


class TiffBoundarySettingsDialog(QDialog):
    def execute_conversion(self):
        """
        执行TIFF边界转矢量的主逻辑，包含参数校验、调用工具函数、弹窗反馈
        """
        from PySide6.QtWidgets import QMessageBox
        try:
            settings = self.get_settings()
            if not settings["input_file"]:
                QMessageBox.warning(self, "参数错误", "请选择输入TIFF文件!")
                return False
            if not settings["output_file"]:
                QMessageBox.warning(self, "参数错误", "请指定输出矢量文件!")
                return False
            
            # 保存输入文件所在目录到配置文件
            input_dir = os.path.dirname(settings["input_file"])
            self.save_config({"last_input_dir": input_dir})
            
            # 延迟导入，避免循环依赖
            from src.utils.tiff_boundary_to_vector import tiff_boundary_to_vector
            success = tiff_boundary_to_vector(
                settings["input_file"],
                settings["output_file"],
                to_wgs84=settings["to_wgs84"]
            )
            if success:
                QMessageBox.information(self, "成功", "TIFF边界已成功转换为矢量文件!")
            else:
                QMessageBox.critical(self, "错误", "转换过程中出现错误，请查看控制台输出。")
            return success
        except Exception as e:
            QMessageBox.critical(self, "错误", f"发生异常: {str(e)}")
            import traceback
            traceback.print_exc()
            return False
        
    def __init__(self, parent=None):
        super(TiffBoundarySettingsDialog, self).__init__(parent)
        
        self.setWindowTitle("TIFF边界转矢量 - 参数设置")
        self.resize(500, 250)
        # 配置文件保存到用户主目录下的.toolbox文件夹
        config_dir = os.path.join(os.path.expanduser("~"), ".toolbox")
        os.makedirs(config_dir, exist_ok=True)
        self.config_file = os.path.join(config_dir, "tiff_boundary_settings.ini")
        
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
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self._on_accept_clicked)
        self.button_box.rejected.connect(self.reject)
        main_layout.addWidget(self.button_box)
        
        self.setLayout(main_layout)
        
        # 加载配置
        self.load_config()

    
    def load_config(self):
        """从ini配置文件加载上次的输入目录"""
        try:
            import configparser
            config = configparser.ConfigParser()
            if os.path.exists(self.config_file):
                config.read(self.config_file, encoding='utf-8')
                last_dir = config.get('TIFF', 'last_input_dir', fallback=None)
                if last_dir and os.path.exists(last_dir):
                    self.last_input_dir = last_dir
                else:
                    self.last_input_dir = str(os.path.expanduser("~"))
            else:
                self.last_input_dir = str(os.path.expanduser("~"))
        except Exception as e:
            print(f"加载配置失败: {e}")
            self.last_input_dir = str(os.path.expanduser("~"))
    
    def save_config(self, config_dict):
        """保存配置到ini文件"""
        try:
            import configparser
            config = configparser.ConfigParser()
            
            # 如果文件存在，先读取
            if os.path.exists(self.config_file):
                config.read(self.config_file, encoding='utf-8')
            
            # 确保有TIFF section
            if 'TIFF' not in config:
                config['TIFF'] = {}
            
            # 更新配置
            config['TIFF'].update(config_dict)
            
            # 写入配置文件
            with open(self.config_file, 'w', encoding='utf-8') as f:
                config.write(f)
        except Exception as e:
            print(f"保存配置失败: {e}")
    
    def browse_input_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择TIFF文件", self.last_input_dir, "TIFF文件 (*.tif *.tiff);;所有文件 (*.*)",
            options=QFileDialog.Option.DontUseNativeDialog
        )
        if file_path:
            self.input_file.setText(file_path)
            self.last_input_dir = os.path.dirname(file_path)
            # 如果还没有设置输出路径，则自动设置一个同名的输出文件
            if not self.output_file.text():
                base_name = os.path.splitext(os.path.basename(file_path))[0]
                suggested_output = os.path.join(os.path.dirname(file_path), f"{base_name}_boundary.shp")
                self.output_file.setText(suggested_output)
    
    def browse_output_file(self):
        """打开保存文件对话框，集成输出格式选择"""
        filter_str = "Shapefile (*.shp);;KML (*.kml);;KMZ (*.kmz)"
        
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self, "保存矢量文件", self.last_input_dir, filter_str,
            options=QFileDialog.Option.DontUseNativeDialog
        )
        if file_path:
            # 根据选中的过滤器确定扩展名
            if "Shapefile" in selected_filter:
                ext = ".shp"
            elif "KML" in selected_filter and "KMZ" not in selected_filter:
                ext = ".kml"
            elif "KMZ" in selected_filter:
                ext = ".kmz"
            else:
                # 从文件路径推断
                current_ext = os.path.splitext(file_path)[1].lower()
                if current_ext in ['.shp', '.kml', '.kmz']:
                    ext = current_ext
                else:
                    ext = ".shp"
            
            # 确保文件扩展名正确
            if not file_path.lower().endswith(ext):
                file_path = os.path.splitext(file_path)[0] + ext
                
            self.output_file.setText(file_path)
    
    def update_file_extension(self):
        """已废弃，保留以兼容"""
        pass

    def _on_accept_clicked(self):
        """在窗口内部完成校验与转换，成功后再关闭窗口。"""
        if self.execute_conversion():
            self.accept()

    
    def get_settings(self):
        """返回用户设置的参数"""
        return {
            "input_file": self.input_file.text(),
            "output_file": self.output_file.text(),
            "to_wgs84": self.use_wgs84.isChecked()
        }
