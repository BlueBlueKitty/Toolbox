'''
Author: Yibo Yuan 2633669459@qq.com
Description: GAMMA文件相关的共用对话框

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                               QLabel, QComboBox, QGroupBox, QDialogButtonBox,
                               QFileDialog, QInputDialog, QMessageBox)

from src.utils.gamma_file_process import (
    GAMMA_FORMATS,
    find_valid_par_for_binary,
    validate_dimensions,
)


class GammaFormatSelectorWidget(QGroupBox):
    """GAMMA格式选择组件（可复用）"""
    
    def __init__(self, parent=None, default_format="float32"):
        super().__init__("数据格式", parent)
        
        layout = QVBoxLayout(self)
        
        format_row = QHBoxLayout()
        format_row.addWidget(QLabel("数据类型:"))
        self.format_combo = QComboBox()
        for fmt, desc in GAMMA_FORMATS.items():
            self.format_combo.addItem(f"{fmt} - {desc}", fmt)
        # 设置默认值
        for i in range(self.format_combo.count()):
            if self.format_combo.itemData(i) == default_format:
                self.format_combo.setCurrentIndex(i)
                break
        format_row.addWidget(self.format_combo)
        layout.addLayout(format_row)
    
    def get_selected_format(self):
        """获取选中的格式"""
        return self.format_combo.currentData()
    
    def connect_format_changed(self, callback):
        """连接格式变化信号"""
        self.format_combo.currentIndexChanged.connect(callback)


class GammaSingleFileDialog(QDialog):
    """GAMMA单文件格式选择对话框"""
    
    def __init__(self, parent=None, default_format="float32", binary_file=None):
        super().__init__(parent)
        self.binary_file = binary_file
        self.setWindowTitle("GAMMA文件设置")
        self.resize(500, 350)
        
        layout = QVBoxLayout(self)
        
        # 格式选择
        self.format_selector = GammaFormatSelectorWidget(self, default_format)
        layout.addWidget(self.format_selector)
        
        # 尺寸设置
        size_group = QGroupBox("图像尺寸")
        size_layout = QVBoxLayout(size_group)
        
        # 自动查找状态
        self.auto_status_label = QLabel("正在检测...")
        size_layout.addWidget(self.auto_status_label)
        
        # PAR文件选择
        par_row = QHBoxLayout()
        par_row.addWidget(QLabel("PAR文件:"))
        self.par_combo = QComboBox()
        self.par_combo.addItem("（自动检测）", None)
        par_row.addWidget(self.par_combo)
        self.browse_par_btn = QPushButton("浏览...")
        self.browse_par_btn.clicked.connect(self._browse_par_file)
        par_row.addWidget(self.browse_par_btn)
        size_layout.addLayout(par_row)
        
        # 手动输入尺寸
        manual_row = QHBoxLayout()
        manual_row.addWidget(QLabel("或手动输入:"))
        manual_row.addWidget(QLabel("宽度:"))
        self.width_edit = QLabel("-")
        manual_row.addWidget(self.width_edit)
        manual_row.addWidget(QLabel("高度:"))
        self.height_edit = QLabel("-")
        manual_row.addWidget(self.height_edit)
        self.manual_input_btn = QPushButton("手动输入尺寸")
        self.manual_input_btn.clicked.connect(self._manual_input_size)
        manual_row.addWidget(self.manual_input_btn)
        size_layout.addLayout(manual_row)
        
        layout.addWidget(size_group)
        
        # 手动输入的值存储
        self.manual_width = None
        self.manual_height = None
        
        # 按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
        # 格式变化时重新检测
        self.format_selector.connect_format_changed(self._update_detection)
        
        # 初始检测
        self._update_detection()
    
    def _update_detection(self):
        """更新自动检测结果"""
        if not self.binary_file:
            self.auto_status_label.setText("未指定二进制文件")
            return
        
        fmt = self.format_selector.get_selected_format()
        par_file, dims = find_valid_par_for_binary(self.binary_file, fmt)
        
        # 更新PAR文件列表
        self.par_combo.clear()
        self.par_combo.addItem("（自动检测）", None)
        
        if par_file:
            self.par_combo.addItem(os.path.basename(par_file), par_file)
            self.par_combo.setCurrentIndex(1)
            width, height = dims
            self.auto_status_label.setText(
                f"✓ 找到匹配的PAR文件: {os.path.basename(par_file)} ({width}x{height})"
            )
            self.auto_status_label.setStyleSheet("color: green;")
        else:
            self.auto_status_label.setText(
                "✗ 未找到匹配的PAR文件，请手动指定尺寸或选择PAR文件"
            )
            self.auto_status_label.setStyleSheet("color: red;")
    
    def _browse_par_file(self):
        """浏览PAR文件"""
        start_dir = os.path.dirname(self.binary_file) if self.binary_file else ""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择PAR文件", start_dir, 
            "PAR文件 (*.par *par* *.PAR *PAR*);;所有文件 (*.*)",
            options=QFileDialog.Option.DontUseNativeDialog
        )
        if file_path:
            # 添加到下拉框
            self.par_combo.addItem(os.path.basename(file_path), file_path)
            self.par_combo.setCurrentIndex(self.par_combo.count() - 1)
    
    def _manual_input_size(self):
        """手动输入尺寸"""
        width, ok1 = QInputDialog.getInt(self, "输入宽度", "请输入图像宽度（列数）:", 0, 1, 100000)
        if not ok1:
            return
        
        height, ok2 = QInputDialog.getInt(self, "输入高度", "请输入图像高度（行数）:", 0, 1, 100000)
        if not ok2:
            return
        
        self.manual_width = width
        self.manual_height = height
        self.width_edit.setText(str(width))
        self.height_edit.setText(str(height))
        
        # 验证尺寸
        fmt = self.format_selector.get_selected_format()
        if validate_dimensions(self.binary_file, width, height, fmt):
            self.auto_status_label.setText(f"✓ 尺寸 {width}x{height} 验证通过")
            self.auto_status_label.setStyleSheet("color: green;")
        else:
            self.auto_status_label.setText(f"✗ 尺寸 {width}x{height} 与文件大小不匹配")
            self.auto_status_label.setStyleSheet("color: red;")
    
    def get_selected_format(self):
        return self.format_selector.get_selected_format()
    
    def get_manual_width(self):
        return self.manual_width
    
    def get_manual_height(self):
        return self.manual_height
    
    def get_selected_par(self):
        return self.par_combo.currentData()


class GammaTimeSeriesDialog(QDialog):
    """GAMMA时序文件格式选择对话框"""
    
    def __init__(self, parent=None, default_format="float32", file_list=None):
        super().__init__(parent)
        self.file_list = file_list or []
        self.valid_files = []
        self.width = None
        self.height = None
        
        self.setWindowTitle("GAMMA时序设置")
        self.resize(600, 400)
        
        layout = QVBoxLayout(self)
        
        # 格式选择
        self.format_selector = GammaFormatSelectorWidget(self, default_format)
        layout.addWidget(self.format_selector)
        
        # 尺寸检测
        size_group = QGroupBox("图像尺寸")
        size_layout = QVBoxLayout(size_group)
        
        self.status_label = QLabel("选择PAR文件或手动输入尺寸...")
        size_layout.addWidget(self.status_label)
        
        # PAR文件选择
        par_row = QHBoxLayout()
        par_row.addWidget(QLabel("PAR文件:"))
        self.par_file_label = QLabel("（未选择）")
        par_row.addWidget(self.par_file_label)
        self.browse_par_btn = QPushButton("浏览PAR文件...")
        self.browse_par_btn.clicked.connect(self._browse_par_file)
        par_row.addWidget(self.browse_par_btn)
        par_row.addStretch()
        size_layout.addLayout(par_row)
        
        # 手动输入和自动检测按钮
        size_row = QHBoxLayout()
        
        self.manual_btn = QPushButton("手动输入尺寸")
        self.manual_btn.clicked.connect(self._manual_input)
        size_row.addWidget(self.manual_btn)
        
        size_row.addStretch()
        size_layout.addLayout(size_row)
        
        # 显示检测到的尺寸
        dim_row = QHBoxLayout()
        dim_row.addWidget(QLabel("宽度:"))
        self.width_label = QLabel("-")
        dim_row.addWidget(self.width_label)
        dim_row.addWidget(QLabel("高度:"))
        self.height_label = QLabel("-")
        dim_row.addWidget(self.height_label)
        dim_row.addStretch()
        size_layout.addLayout(dim_row)
        
        layout.addWidget(size_group)
        
        # 文件列表
        files_group = QGroupBox(f"检测到的文件 ({len(self.file_list)} 个)")
        files_layout = QVBoxLayout(files_group)
        
        self.files_status_label = QLabel("尚未验证文件")
        files_layout.addWidget(self.files_status_label)
        
        layout.addWidget(files_group)
        
        # 按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self._validate_and_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
        # 格式变化时重置检测结果
        self.format_selector.connect_format_changed(self._on_format_changed)
        
        # 存储选择的PAR文件
        self.selected_par_file = None
        
        # 初始化时尝试自动检测PAR文件
        self._auto_detect_par()
    
    def _browse_par_file(self):
        """浏览并选择PAR文件"""
        # 从文件列表获取起始目录
        start_dir = os.path.dirname(self.file_list[0]) if self.file_list else ""
        
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择PAR文件", start_dir,
            "PAR文件 (*.par *par* *.PAR *PAR*);;所有文件 (*.*)",
            options=QFileDialog.Option.DontUseNativeDialog
        )
        
        if file_path:
            self.selected_par_file = file_path
            self.par_file_label.setText(os.path.basename(file_path))
            
            # 尝试从PAR文件读取尺寸
            try:
                from src.utils.gamma_file_process import get_dimensions_from_par
                width, height = get_dimensions_from_par(file_path)
                self.width = width
                self.height = height
                self.width_label.setText(str(width))
                self.height_label.setText(str(height))
                
                self.status_label.setText(
                    f"✓ 从PAR文件读取到尺寸: {width} x {height}"
                )
                self.status_label.setStyleSheet("color: green;")
                
                # 验证文件
                self._validate_files()
                
            except Exception as e:
                self.status_label.setText(f"✗ 读取PAR文件失败: {str(e)}")
                self.status_label.setStyleSheet("color: red;")
    
    def _on_format_changed(self):
        """格式变化时的处理：重置并自动检测PAR"""
        # 重置检测结果
        self.valid_files = []
        self.width = None
        self.height = None
        self.width_label.setText("-")
        self.height_label.setText("-")
        self.files_status_label.setText("尚未验证文件")
        self.selected_par_file = None
        self.par_file_label.setText("（未选择）")
        
        # 自动检测PAR文件
        self._auto_detect_par()
    
    def _auto_detect_par(self):
        """自动检测PAR文件"""
        if not self.file_list:
            self.status_label.setText("没有文件可检测！")
            return
        
        fmt = self.format_selector.get_selected_format()
        self.status_label.setText("正在检测...")
        
        # 尝试从第一个文件的par文件获取尺寸
        first_file = self.file_list[0]
        par_file, dims = find_valid_par_for_binary(first_file, fmt)
        
        if par_file and dims:
            self.selected_par_file = par_file
            self.par_file_label.setText(os.path.basename(par_file))
            self.width, self.height = dims
            self.width_label.setText(str(self.width))
            self.height_label.setText(str(self.height))
            self.status_label.setText(
                f"✓ 从PAR文件检测到尺寸: {self.width} x {self.height}"
            )
            self.status_label.setStyleSheet("color: green;")
            
            # 验证所有文件
            self._validate_files()
        else:
            self.status_label.setText(
                "✗ 未找到PAR文件，请手动选择PAR文件或输入尺寸"
            )
            self.status_label.setStyleSheet("color: orange;")
    
    def _manual_input(self):
        """手动输入尺寸"""
        width, ok1 = QInputDialog.getInt(self, "输入宽度", "请输入图像宽度（列数）:", 0, 1, 100000)
        if not ok1:
            return
        
        height, ok2 = QInputDialog.getInt(self, "输入高度", "请输入图像高度（行数）:", 0, 1, 100000)
        if not ok2:
            return
        
        self.width = width
        self.height = height
        self.width_label.setText(str(width))
        self.height_label.setText(str(height))
        
        # 验证文件
        self._validate_files()
    
    def _validate_files(self):
        """验证所有文件是否符合当前尺寸"""
        if self.width is None or self.height is None:
            return
        
        fmt = self.format_selector.get_selected_format()
        self.valid_files = []
        invalid_count = 0
        
        for file_path in self.file_list:
            if validate_dimensions(file_path, self.width, self.height, fmt):
                self.valid_files.append(file_path)
            else:
                invalid_count += 1
        
        if self.valid_files:
            self.files_status_label.setText(
                f"✓ 有效文件: {len(self.valid_files)} 个" + 
                (f"，无效: {invalid_count} 个" if invalid_count > 0 else "")
            )
            self.files_status_label.setStyleSheet("color: green;")
        else:
            self.files_status_label.setText("✗ 没有找到有效文件")
            self.files_status_label.setStyleSheet("color: red;")
    
    def _validate_and_accept(self):
        """验证并接受"""
        if not self.valid_files:
            QMessageBox.warning(self, "警告", "没有有效的文件可加载！请检测或输入正确的尺寸。")
            return
        
        self.accept()
    
    def get_selected_format(self):
        return self.format_selector.get_selected_format()
    
    def get_valid_files(self):
        return self.valid_files
    
    def get_width(self):
        return self.width
    
    def get_height(self):
        return self.height
