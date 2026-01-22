'''
Author: Yibo Yuan 2633669459@qq.com
Date: 2026-01-22
Description: 像素时序查看器对话框

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
import numpy as np
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QFileDialog, QLabel, QSlider, QComboBox, QMessageBox,
                               QSplitter, QGroupBox, QGridLayout, QCheckBox)
from PySide6.QtCore import Qt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
from PIL import Image
from osgeo import gdal
import traceback

from src.widgets import ImageViewer


class PixelTimeSeriesViewerDialog(QDialog):
    """像素时序查看器对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.setWindowTitle("像素时序查看器")
        self.resize(1200, 700)
        
        # 存储时序图像数据
        self.image_files = []  # 文件路径列表
        self.image_data_list = []  # 图像数据列表
        self.current_image_index = 0  # 当前显示的图像索引
        self.selected_pixel = None  # 选中的像素坐标 (x, y)
        
        # 创建UI
        self._create_ui()
        
    def _create_ui(self):
        """创建用户界面"""
        main_layout = QVBoxLayout(self)
        
        # 顶部控制区
        control_layout = QHBoxLayout()
        
        self.open_folder_btn = QPushButton("打开图像文件夹")
        self.open_folder_btn.clicked.connect(self.open_folder)
        control_layout.addWidget(self.open_folder_btn)
        
        control_layout.addWidget(QLabel("排序方式:"))
        self.sort_order_combo = QComboBox()
        self.sort_order_combo.addItems(["正序", "倒序"])
        self.sort_order_combo.currentIndexChanged.connect(self.sort_images)
        control_layout.addWidget(self.sort_order_combo)
        
        self.image_count_label = QLabel("未加载图像")
        control_layout.addWidget(self.image_count_label)
        
        control_layout.addStretch()
        
        main_layout.addLayout(control_layout)
        
        # 创建分割器：左侧图像查看器，右侧时序曲线
        splitter = QSplitter(Qt.Horizontal)
        
        # 左侧：图像查看器和切换控制
        left_widget = QGroupBox("图像查看")
        left_layout = QVBoxLayout(left_widget)
        
        # 图像查看器
        self.image_viewer = ImageViewer()
        self.image_viewer.pixel_clicked.connect(self.on_pixel_clicked)
        left_layout.addWidget(self.image_viewer)
        
        # 图像切换控制
        switch_layout = QHBoxLayout()
        
        self.prev_btn = QPushButton("上一张")
        self.prev_btn.clicked.connect(self.previous_image)
        self.prev_btn.setEnabled(False)
        switch_layout.addWidget(self.prev_btn)
        
        self.image_slider = QSlider(Qt.Horizontal)
        self.image_slider.setMinimum(0)
        self.image_slider.setMaximum(0)
        self.image_slider.valueChanged.connect(self.slider_changed)
        self.image_slider.setEnabled(False)
        switch_layout.addWidget(self.image_slider)
        
        self.next_btn = QPushButton("下一张")
        self.next_btn.clicked.connect(self.next_image)
        self.next_btn.setEnabled(False)
        switch_layout.addWidget(self.next_btn)
        
        self.image_index_label = QLabel("0/0")
        switch_layout.addWidget(self.image_index_label)
        
        left_layout.addLayout(switch_layout)
        
        # 当前图像信息
        self.image_info_label = QLabel("图像信息: 未加载")
        left_layout.addWidget(self.image_info_label)
        
        splitter.addWidget(left_widget)
        
        # 右侧：时序曲线图
        right_widget = QGroupBox("时序曲线")
        right_layout = QVBoxLayout(right_widget)
        
        # Matplotlib图表
        self.figure = Figure(figsize=(6, 4))
        self.canvas = FigureCanvas(self.figure)
        self.toolbar = NavigationToolbar(self.canvas, self)
        
        right_layout.addWidget(self.toolbar)
        right_layout.addWidget(self.canvas)
        
        # 像素信息
        self.pixel_info_label = QLabel("请点击图像选择像素")
        right_layout.addWidget(self.pixel_info_label)
        
        splitter.addWidget(right_widget)
        
        # 设置分割器比例
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        
        main_layout.addWidget(splitter)
        
    def open_folder(self):
        """打开图像文件夹"""
        folder = QFileDialog.getExistingDirectory(self, "选择图像文件夹")
        if not folder:
            return
        
        try:
            # 查找支持的图像文件
            supported_extensions = ['.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff']
            files = []
            
            for filename in os.listdir(folder):
                ext = os.path.splitext(filename)[1].lower()
                if ext in supported_extensions:
                    files.append(os.path.join(folder, filename))
            
            if not files:
                QMessageBox.warning(self, "警告", "文件夹中没有找到支持的图像文件！")
                return
            
            # 按文件名排序
            files.sort()
            
            # 加载图像
            self.load_images(files)
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开文件夹失败: {str(e)}")
            traceback.print_exc()
    
    def load_images(self, file_list):
        """加载图像列表"""
        if not file_list:
            return
        
        try:
            # 清空之前的数据
            self.image_files = []
            self.image_data_list = []
            self.selected_pixel = None
            
            # 读取第一张图像以检查尺寸和波段数
            first_image_data = self._read_image(file_list[0])
            if first_image_data is None:
                QMessageBox.critical(self, "错误", f"无法读取第一张图像: {file_list[0]}")
                return
            
            reference_shape = first_image_data.shape
            
            # 加载所有图像并检查一致性
            inconsistent_files = []
            
            for file_path in file_list:
                image_data = self._read_image(file_path)
                
                if image_data is None:
                    inconsistent_files.append(f"{os.path.basename(file_path)}: 读取失败")
                    continue
                
                if image_data.shape != reference_shape:
                    inconsistent_files.append(
                        f"{os.path.basename(file_path)}: "
                        f"尺寸{image_data.shape} != 参考尺寸{reference_shape}"
                    )
                    continue
                
                self.image_files.append(file_path)
                self.image_data_list.append(image_data)
            
            # 如果有不一致的文件，提示用户
            if inconsistent_files:
                message = "以下文件与参考图像不一致，已跳过：\n" + "\n".join(inconsistent_files)
                QMessageBox.warning(self, "警告", message)
            
            if not self.image_data_list:
                QMessageBox.critical(self, "错误", "没有成功加载任何图像！")
                return
            
            # 更新UI
            self.image_count_label.setText(f"已加载 {len(self.image_data_list)} 张图像")
            self.image_slider.setMaximum(len(self.image_data_list) - 1)
            self.image_slider.setEnabled(True)
            self.prev_btn.setEnabled(True)
            self.next_btn.setEnabled(True)
            
            # 显示第一张图像
            self.current_image_index = 0
            self.show_current_image()
            
            # 应用排序
            self.sort_images()
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载图像失败: {str(e)}")
            traceback.print_exc()
    
    def _read_image(self, file_path):
        """
        读取图像文件，支持普通图像和TIFF
        
        Returns:
            numpy array: 图像数据，格式为(H, W)或(H, W, C)
        """
        try:
            ext = os.path.splitext(file_path)[1].lower()
            
            if ext in ['.tif', '.tiff']:
                # 使用GDAL读取TIFF
                ds = gdal.Open(file_path)
                if ds is None:
                    return None
                
                band_count = ds.RasterCount
                
                if band_count == 1:
                    # 单波段
                    band = ds.GetRasterBand(1)
                    data = band.ReadAsArray()
                else:
                    # 多波段
                    data = []
                    for i in range(1, band_count + 1):
                        band = ds.GetRasterBand(i)
                        data.append(band.ReadAsArray())
                    data = np.stack(data, axis=-1)
                
                ds = None
                return data
            else:
                # 使用PIL读取普通图像
                img = Image.open(file_path)
                data = np.array(img)
                
                # 如果是单通道灰度图，确保是2D
                if data.ndim == 2:
                    return data
                elif data.ndim == 3:
                    # 如果有alpha通道，去掉
                    if data.shape[2] == 4:
                        data = data[:, :, :3]
                    return data
                else:
                    return None
                
        except Exception as e:
            print(f"读取图像失败 {file_path}: {e}")
            return None
    
    def sort_images(self):
        """排序图像"""
        if not self.image_files:
            return
        
        # 获取排序方式
        reverse = (self.sort_order_combo.currentIndex() == 1)
        
        # 创建索引列表并排序
        indices = list(range(len(self.image_files)))
        indices.sort(key=lambda i: os.path.basename(self.image_files[i]), reverse=reverse)
        
        # 重新排列
        self.image_files = [self.image_files[i] for i in indices]
        self.image_data_list = [self.image_data_list[i] for i in indices]
        
        # 重置当前索引
        self.current_image_index = 0
        self.show_current_image()
        
        # 如果已选择像素，更新曲线
        if self.selected_pixel:
            self.update_time_series_plot()
    
    def show_current_image(self):
        """显示当前图像"""
        if not self.image_data_list:
            return
        
        # 更新图像查看器
        current_data = self.image_data_list[self.current_image_index]
        self.image_viewer.set_image_from_array(current_data)
        
        # 更新滑块
        self.image_slider.blockSignals(True)
        self.image_slider.setValue(self.current_image_index)
        self.image_slider.blockSignals(False)
        
        # 更新索引标签
        self.image_index_label.setText(
            f"{self.current_image_index + 1}/{len(self.image_data_list)}"
        )
        
        # 更新图像信息
        file_name = os.path.basename(self.image_files[self.current_image_index])
        shape = current_data.shape
        if current_data.ndim == 2:
            info = f"{file_name} | 尺寸: {shape[1]}x{shape[0]} | 单波段"
        elif current_data.ndim == 3:
            info = f"{file_name} | 尺寸: {shape[1]}x{shape[0]} | {shape[2]}波段"
        else:
            info = f"{file_name} | 尺寸: {shape}"
        
        self.image_info_label.setText(info)
        
        # 如果已选择像素，更新曲线高亮
        if self.selected_pixel:
            self.update_time_series_plot()
    
    def previous_image(self):
        """上一张图像"""
        if self.current_image_index > 0:
            self.current_image_index -= 1
            self.show_current_image()
    
    def next_image(self):
        """下一张图像"""
        if self.current_image_index < len(self.image_data_list) - 1:
            self.current_image_index += 1
            self.show_current_image()
    
    def slider_changed(self, value):
        """滑块值改变"""
        if value != self.current_image_index:
            self.current_image_index = value
            self.show_current_image()
    
    def on_pixel_clicked(self, x, y):
        """像素点击事件处理"""
        self.selected_pixel = (x, y)
        self.pixel_info_label.setText(f"选中像素: ({x}, {y})")
        
        # 绘制时序曲线
        self.update_time_series_plot()
    
    def update_time_series_plot(self):
        """更新时序曲线图"""
        if not self.selected_pixel or not self.image_data_list:
            return
        
        x, y = self.selected_pixel
        
        # 清空图表
        self.figure.clear()
        
        # 提取时序数据
        time_indices = list(range(len(self.image_data_list)))
        
        # 获取第一张图像以确定波段数
        first_image = self.image_data_list[0]
        
        if first_image.ndim == 2:
            # 单波段灰度图
            values = []
            for img_data in self.image_data_list:
                values.append(img_data[y, x])
            
            ax = self.figure.add_subplot(111)
            ax.plot(time_indices, values, 'o-', label='像素值', linewidth=2)
            
            # 高亮当前图像的点
            ax.plot(self.current_image_index, values[self.current_image_index], 
                   'ro', markersize=10, label='当前图像')
            
            ax.set_xlabel('图像索引')
            ax.set_ylabel('像素值')
            ax.set_title(f'像素 ({x}, {y}) 的时序曲线')
            ax.legend()
            ax.grid(True, alpha=0.3)
            
        elif first_image.ndim == 3:
            num_bands = first_image.shape[2]
            
            # 多波段图像
            ax = self.figure.add_subplot(111)
            
            # 为每个波段绘制曲线
            for band_idx in range(num_bands):
                values = []
                for img_data in self.image_data_list:
                    values.append(img_data[y, x, band_idx])
                
                ax.plot(time_indices, values, 'o-', label=f'波段{band_idx+1}', linewidth=2)
                
                # 高亮当前图像的点
                ax.plot(self.current_image_index, values[self.current_image_index], 
                       'o', markersize=10)
            
            # 如果是RGB图像（3波段），计算并绘制灰度值
            if num_bands == 3:
                gray_values = []
                for img_data in self.image_data_list:
                    # RGB转灰度: 0.299*R + 0.587*G + 0.114*B
                    r, g, b = img_data[y, x, 0], img_data[y, x, 1], img_data[y, x, 2]
                    gray = 0.299 * r + 0.587 * g + 0.114 * b
                    gray_values.append(gray)
                
                ax.plot(time_indices, gray_values, 's--', label='灰度值', 
                       linewidth=2, alpha=0.7)
                
                # 高亮当前图像的灰度值
                ax.plot(self.current_image_index, gray_values[self.current_image_index], 
                       's', markersize=10, color='red')
            
            ax.set_xlabel('图像索引')
            ax.set_ylabel('像素值')
            ax.set_title(f'像素 ({x}, {y}) 的时序曲线')
            ax.legend()
            ax.grid(True, alpha=0.3)
        
        self.figure.tight_layout()
        self.canvas.draw()
