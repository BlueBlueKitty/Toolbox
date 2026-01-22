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
                               QSplitter, QGroupBox, QGridLayout, QCheckBox, QFormLayout)
from PySide6.QtCore import Qt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from PIL import Image
from osgeo import gdal
import traceback

from src.widgets import ImageViewer, ColormapComboBox

# 设置matplotlib支持中文显示
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'SimSun', 'KaiTi', 'FangSong']
plt.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题


class PixelTimeSeriesViewerDialog(QDialog):
    """像素时序查看器对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.setWindowTitle("像素时序查看器")
        self.resize(1600, 900)
        
        # 存储时序图像数据
        self.image_files = []  # 文件路径列表
        self.image_data_list = []  # 图像数据列表
        self.current_image_index_1 = 0  # 窗口1当前显示的图像索引
        self.current_image_index_2 = 0  # 窗口2当前显示的图像索引
        self.selected_pixel = None  # 选中的像素坐标 (x, y)
        
        # 创建UI
        self._create_ui()
        
    def _create_ui(self):
        """创建用户界面"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)
        
        # 顶部控制区
        control_layout = QHBoxLayout()
        control_layout.setSpacing(10)
        
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
        
        # Colormap选择（两个窗口共用）
        control_layout.addWidget(QLabel("Colormap:"))
        self.colormap_combo = ColormapComboBox()
        self.colormap_combo.currentTextChanged.connect(self.on_colormap_changed)
        control_layout.addWidget(self.colormap_combo)
        
        main_layout.addLayout(control_layout)
        
        # 创建主分割器：上方图像查看区，下方时序曲线
        main_splitter = QSplitter(Qt.Vertical)
        
        # ============ 上方：双图像查看区（左右排列）============
        images_splitter = QSplitter(Qt.Horizontal)
        
        # 图像窗口1（左侧）
        viewer1_widget = self._create_image_viewer_panel("窗口1", 1)
        images_splitter.addWidget(viewer1_widget)
        
        # 图像窗口2（右侧）
        viewer2_widget = self._create_image_viewer_panel("窗口2", 2)
        images_splitter.addWidget(viewer2_widget)
        
        # 设置两个图像窗口等宽
        images_splitter.setStretchFactor(0, 1)
        images_splitter.setStretchFactor(1, 1)
        
        main_splitter.addWidget(images_splitter)
        
        # ============ 下方：时序曲线图 ============
        curve_widget = QGroupBox("时序曲线")
        curve_layout = QVBoxLayout(curve_widget)
        curve_layout.setContentsMargins(5, 5, 5, 5)
        
        # Matplotlib图表
        self.figure = Figure(figsize=(10, 3))
        self.canvas = FigureCanvas(self.figure)
        self.toolbar = NavigationToolbar(self.canvas, self)
        
        curve_layout.addWidget(self.toolbar)
        curve_layout.addWidget(self.canvas)
        
        # 像素信息
        self.pixel_info_label = QLabel("请点击图像选择像素")
        curve_layout.addWidget(self.pixel_info_label)
        
        main_splitter.addWidget(curve_widget)
        
        # 设置分割器比例：上方图像区域占70%，下方曲线区域占30%
        main_splitter.setStretchFactor(0, 7)
        main_splitter.setStretchFactor(1, 3)
        
        main_layout.addWidget(main_splitter)
    
    def _create_image_viewer_panel(self, title, viewer_id):
        """创建单个图像查看器面板
        
        Args:
            title: 窗口标题
            viewer_id: 查看器ID（1或2）
        """
        panel = QGroupBox(title)
        layout = QVBoxLayout(panel)
        
        # 图像查看器
        viewer = ImageViewer()
        setattr(self, f'image_viewer_{viewer_id}', viewer)
        
        # 连接像素点击信号
        viewer.pixel_clicked.connect(self.on_pixel_clicked)
        
        # 连接视图变换信号（用于同步缩放）
        viewer.view_transformed.connect(lambda t: self.sync_other_viewer(viewer_id, t))
        
        # 连接鼠标样式变化信号（用于同步鼠标样式）
        viewer.cursor_changed.connect(lambda c: self.sync_other_cursor(viewer_id, c))
        
        # 连接滚动条位置变化信号（用于同步拖动）
        viewer.scroll_changed.connect(lambda h, v: self.sync_other_scroll(viewer_id, h, v))
        
        layout.addWidget(viewer)
        
        # 控制区域
        control_layout = QVBoxLayout()
        
        # 图像切换控制
        switch_layout = QHBoxLayout()
        
        prev_btn = QPushButton("上一张")
        next_btn = QPushButton("下一张")
        prev_btn.clicked.connect(lambda: self.switch_image(viewer_id, -1))
        next_btn.clicked.connect(lambda: self.switch_image(viewer_id, 1))
        setattr(self, f'prev_btn_{viewer_id}', prev_btn)
        setattr(self, f'next_btn_{viewer_id}', next_btn)
        prev_btn.setEnabled(False)
        next_btn.setEnabled(False)
        
        image_slider = QSlider(Qt.Horizontal)
        image_slider.setMinimum(0)
        image_slider.setMaximum(0)
        image_slider.setEnabled(False)
        image_slider.valueChanged.connect(lambda v: self.slider_changed(viewer_id, v))
        setattr(self, f'image_slider_{viewer_id}', image_slider)
        
        image_index_label = QLabel("0/0")
        setattr(self, f'image_index_label_{viewer_id}', image_index_label)
        
        switch_layout.addWidget(prev_btn)
        switch_layout.addWidget(image_slider)
        switch_layout.addWidget(image_index_label)
        switch_layout.addWidget(next_btn)
        
        control_layout.addLayout(switch_layout)
        
        # 图像信息标签
        image_info_label = QLabel("图像信息: 未加载")
        setattr(self, f'image_info_label_{viewer_id}', image_info_label)
        control_layout.addWidget(image_info_label)
        
        layout.addLayout(control_layout)
        
        return panel
        
    def sync_other_viewer(self, source_viewer_id, transform):
        """同步其他查看器的视图变换（缩放）
        
        Args:
            source_viewer_id: 源查看器ID（1或2）
            transform: 变换对象
        """
        # 同步到另一个查看器
        target_viewer_id = 2 if source_viewer_id == 1 else 1
        target_viewer = getattr(self, f'image_viewer_{target_viewer_id}')
        target_viewer.sync_transform(transform)
    
    def sync_other_cursor(self, source_viewer_id, cursor):
        """同步其他查看器的鼠标样式
        
        Args:
            source_viewer_id: 源查看器ID（1或2）
            cursor: 鼠标样式
        """
        # 同步到另一个查看器
        target_viewer_id = 2 if source_viewer_id == 1 else 1
        target_viewer = getattr(self, f'image_viewer_{target_viewer_id}')
        target_viewer.sync_cursor(cursor)
    
    def sync_other_scroll(self, source_viewer_id, h_value, v_value):
        """同步其他查看器的滚动条位置（拖动）
        
        Args:
            source_viewer_id: 源查看器ID（1或2）
            h_value: 水平滚动条值
            v_value: 垂直滚动条值
        """
        # 同步到另一个查看器
        target_viewer_id = 2 if source_viewer_id == 1 else 1
        target_viewer = getattr(self, f'image_viewer_{target_viewer_id}')
        target_viewer.sync_scroll(h_value, v_value)
    
    def on_colormap_changed(self, colormap_name):
        """Colormap变化时同时更新两个窗口"""
        if hasattr(self, 'image_viewer_1'):
            self.image_viewer_1.set_colormap(colormap_name)
        if hasattr(self, 'image_viewer_2'):
            self.image_viewer_2.set_colormap(colormap_name)
    
    def switch_image(self, viewer_id, direction):
        """切换图像
        
        Args:
            viewer_id: 查看器ID（1或2）
            direction: 方向（-1表示上一张，1表示下一张）
        """
        current_index = getattr(self, f'current_image_index_{viewer_id}')
        new_index = current_index + direction
        
        if 0 <= new_index < len(self.image_data_list):
            setattr(self, f'current_image_index_{viewer_id}', new_index)
            self.show_image(viewer_id)
    
    def slider_changed(self, viewer_id, value):
        """滑块值改变
        
        Args:
            viewer_id: 查看器ID（1或2）
            value: 滑块值
        """
        current_index = getattr(self, f'current_image_index_{viewer_id}')
        if value != current_index:
            setattr(self, f'current_image_index_{viewer_id}', value)
            self.show_image(viewer_id)
    
    def show_image(self, viewer_id):
        """显示指定查看器的当前图像
        
        Args:
            viewer_id: 查看器ID（1或2）
        """
        if not self.image_data_list:
            return
        
        current_index = getattr(self, f'current_image_index_{viewer_id}')
        viewer = getattr(self, f'image_viewer_{viewer_id}')
        slider = getattr(self, f'image_slider_{viewer_id}')
        index_label = getattr(self, f'image_index_label_{viewer_id}')
        info_label = getattr(self, f'image_info_label_{viewer_id}')
        
        # 更新图像查看器
        current_data = self.image_data_list[current_index]
        viewer.set_image_from_array(current_data)
        
        # 更新滑块
        slider.blockSignals(True)
        slider.setValue(current_index)
        slider.blockSignals(False)
        
        # 更新索引标签
        index_label.setText(f"{current_index + 1}/{len(self.image_data_list)}")
        
        # 更新图像信息
        file_name = os.path.basename(self.image_files[current_index])
        shape = current_data.shape
        if current_data.ndim == 2:
            info = f"{file_name} | 尺寸: {shape[1]}x{shape[0]} | 单波段"
        elif current_data.ndim == 3:
            info = f"{file_name} | 尺寸: {shape[1]}x{shape[0]} | {shape[2]}波段"
        else:
            info = f"{file_name} | 尺寸: {shape}"
        
        info_label.setText(info)
        
        # 如果已选择像素，更新曲线高亮
        if self.selected_pixel:
            self.update_time_series_plot()
    
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
            
            # 更新两个窗口的控件
            for viewer_id in [1, 2]:
                slider = getattr(self, f'image_slider_{viewer_id}')
                prev_btn = getattr(self, f'prev_btn_{viewer_id}')
                next_btn = getattr(self, f'next_btn_{viewer_id}')
                
                slider.setMaximum(len(self.image_data_list) - 1)
                slider.setEnabled(True)
                prev_btn.setEnabled(True)
                next_btn.setEnabled(True)
            
            # 显示第一张和第二张图像
            self.current_image_index_1 = 0
            self.current_image_index_2 = min(1, len(self.image_data_list) - 1)  # 如果只有一张图像，两个窗口都显示第一张
            self.show_image(1)
            self.show_image(2)
            
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
        self.current_image_index_1 = 0
        self.current_image_index_2 = min(1, len(self.image_data_list) - 1)  # 如果只有一张图像，两个窗口都显示第一张
        self.show_image(1)
        self.show_image(2)
        
        # 如果已选择像素，更新曲线
        if self.selected_pixel:
            self.update_time_series_plot()
    
    def on_pixel_clicked(self, x, y):
        """像素点击事件处理"""
        self.selected_pixel = (x, y)
        self.pixel_info_label.setText(f"选中像素: ({x}, {y})")
        
        # 绘制时序曲线
        self.update_time_series_plot()
    
    def update_time_series_plot(self):
        """更新时序曲线图（支持双窗口不同颜色显示）"""
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
            ax.plot(time_indices, values, 'o-', label='像素值', linewidth=2, color='blue')
            
            # 高亮两个窗口当前图像的点，用不同颜色
            ax.plot(self.current_image_index_1, values[self.current_image_index_1], 
                   'ro', markersize=12, label='窗口1', zorder=10)
            ax.plot(self.current_image_index_2, values[self.current_image_index_2], 
                   'go', markersize=12, label='窗口2', zorder=10)
            
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
            band_colors = ['red', 'green', 'blue', 'cyan', 'magenta', 'yellow']
            for band_idx in range(num_bands):
                values = []
                for img_data in self.image_data_list:
                    values.append(img_data[y, x, band_idx])
                
                color = band_colors[band_idx % len(band_colors)]
                ax.plot(time_indices, values, 'o-', label=f'波段{band_idx+1}', 
                       linewidth=2, color=color, alpha=0.7)
            
            # 高亮两个窗口当前图像的位置（用竖线）
            ax.axvline(x=self.current_image_index_1, color='red', linestyle='--', 
                      linewidth=2, label='窗口1', alpha=0.8)
            ax.axvline(x=self.current_image_index_2, color='darkgreen', linestyle='--', 
                      linewidth=2, label='窗口2', alpha=0.8)
            
            # 如果是RGB图像（3波段），计算并绘制灰度值
            if num_bands == 3:
                gray_values = []
                for img_data in self.image_data_list:
                    # RGB转灰度: 0.299*R + 0.587*G + 0.114*B
                    r, g, b = img_data[y, x, 0], img_data[y, x, 1], img_data[y, x, 2]
                    gray = 0.299 * r + 0.587 * g + 0.114 * b
                    gray_values.append(gray)
                
                ax.plot(time_indices, gray_values, 's--', label='灰度值', 
                       linewidth=2, alpha=0.5, color='black')
            
            ax.set_xlabel('图像索引')
            ax.set_ylabel('像素值')
            ax.set_title(f'像素 ({x}, {y}) 的时序曲线')
            ax.legend()
            ax.grid(True, alpha=0.3)
        
        self.figure.tight_layout()
        self.canvas.draw()
