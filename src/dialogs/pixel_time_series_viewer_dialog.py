'''
Author: Yibo Yuan 2633669459@qq.com
Date: 2026-01-22
Description: 像素时序查看器对话框

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
import numpy as np
from pathlib import Path
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QFileDialog, QLabel, QSlider, QComboBox, QMessageBox,
                               QSplitter, QGroupBox, QGridLayout, QCheckBox, QFormLayout)
from PySide6.QtCore import Qt, QSettings

# 配置文件路径
def get_settings():
    config_dir = Path.home() / ".toolbox"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "pixel_time_series_viewer.ini"
    return QSettings(str(config_file), QSettings.IniFormat)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from PIL import Image
from osgeo import gdal
import traceback
import h5py

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
        
        # 存储时序图像数据（按需加载模式）
        self.image_files = []  # 文件路径列表
        self.date_list = []  # 日期列表（用于h5时序数据）
        self.current_image_index_1 = 0  # 窗口1当前显示的图像索引
        self.current_image_index_2 = 0  # 窗口2当前显示的图像索引
        self.selected_pixel = None  # 选中的像素坐标 (x, y)
        self.nodata_value = None  # Nodata值
        
        # 按需加载相关
        self.data_source_type = None  # 数据源类型：'folder' 或 'h5'
        self.h5_file_path = None  # h5文件路径（仅当data_source_type为'h5'时使用）
        self.h5_start_index = 0  # h5数据起始索引（跳过全0帧）
        self.image_shape = None  # 图像形状 (height, width) 或 (height, width, bands)
        self.image_count = 0  # 图像总数
        
        # 缓存当前显示的两张图像（包含原始尺寸信息）
        self._cached_image_1 = None  # 窗口1缓存的图像数据
        self._cached_index_1 = -1  # 窗口1缓存的图像索引
        self._cached_original_size_1 = None  # 窗口1缓存的原始尺寸
        self._cached_image_2 = None  # 窗口2缓存的图像数据
        self._cached_index_2 = -1  # 窗口2缓存的图像索引
        self._cached_original_size_2 = None  # 窗口2缓存的原始尺寸
        
        # 时序曲线悬浮提示相关
        self._plot_data_points = []  # 存储绘图数据点 [(x, y, index), ...]
        self._plot_annotation = None  # matplotlib annotation对象
        self._plot_ax = None  # 当前axes对象
        
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
        
        self.open_h5_btn = QPushButton("打开h5时序数据")
        self.open_h5_btn.clicked.connect(self.open_h5_timeseries)
        control_layout.addWidget(self.open_h5_btn)
        
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
        
        # Nodata值设置
        self.set_nodata_btn = QPushButton("设置Nodata值")
        self.set_nodata_btn.clicked.connect(self.set_nodata_value)
        control_layout.addWidget(self.set_nodata_btn)
        
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
        
        # 连接鼠标事件用于悬浮提示
        self.canvas.mpl_connect('motion_notify_event', self._on_plot_mouse_move)
        self.canvas.mpl_connect('button_press_event', self._on_plot_click)
        
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
        
        # 像素信息标签（显示当前像素值）
        pixel_value_label = QLabel("像素值: -")
        setattr(self, f'pixel_value_label_{viewer_id}', pixel_value_label)
        control_layout.addWidget(pixel_value_label)
        
        layout.addLayout(control_layout)
        
        # 连接鼠标移动事件，指標显示像素值
        viewer.mouse_moved.connect(lambda x, y, val: self.on_viewer_mouse_moved(viewer_id, x, y, val))
        
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
        
        if 0 <= new_index < self.image_count:
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
    
    def _get_image_data(self, index, max_display_size=2048):
        """按需获取指定索引的图像数据（支持降采样）
        
        Args:
            index: 图像索引
            max_display_size: 显示用的最大边长，超过此尺寸会降采样
            
        Returns:
            tuple: (图像数据数组, 原始尺寸(width, height))，失败返回(None, None)
        """
        if index < 0 or index >= self.image_count:
            return None, None
        
        if self.data_source_type == 'h5':
            # 从h5文件按需读取
            try:
                with h5py.File(self.h5_file_path, 'r') as h5f:
                    actual_index = index + self.h5_start_index
                    # 获取原始尺寸
                    original_height, original_width = h5f['timeseries'].shape[1:3]
                    original_size = (original_width, original_height)
                    
                    # 计算是否需要降采样
                    max_dim = max(original_width, original_height)
                    if max_dim <= max_display_size:
                        # 不需要降采样
                        image_data = h5f['timeseries'][actual_index, :, :]
                    else:
                        # 需要降采样
                        scale = max_display_size / max_dim
                        target_height = int(original_height * scale)
                        target_width = int(original_width * scale)
                        
                        # 读取完整数据然后降采样（h5不支持直接降采样读取）
                        full_data = h5f['timeseries'][actual_index, :, :]
                        
                        # 使用简单的步进降采样
                        step_y = original_height // target_height
                        step_x = original_width // target_width
                        image_data = full_data[::step_y, ::step_x]
                        
                        # 确保尺寸正确
                        image_data = image_data[:target_height, :target_width]
                    
                    return image_data, original_size
            except Exception as e:
                print(f"读取h5数据失败 (索引 {index}): {e}")
                return None, None
        else:
            # 从文件夹按需读取（使用降采样读取）
            if index < len(self.image_files):
                image_data, _, original_size = self._read_image_downsampled(
                    self.image_files[index], 
                    max_size=max_display_size
                )
                return image_data, original_size
            return None, None
    
    def _get_cached_image(self, viewer_id, index):
        """获取缓存的图像数据，如果未缓存则加载
        
        Args:
            viewer_id: 查看器ID（1或2）
            index: 图像索引
            
        Returns:
            tuple: (图像数据数组, 原始尺寸(width, height))
        """
        cached_index = getattr(self, f'_cached_index_{viewer_id}')
        
        if cached_index == index:
            # 缓存命中
            cached_image = getattr(self, f'_cached_image_{viewer_id}')
            cached_size = getattr(self, f'_cached_original_size_{viewer_id}', None)
            return cached_image, cached_size
        
        # 加载新数据
        image_data, original_size = self._get_image_data(index)
        
        # 更新缓存
        setattr(self, f'_cached_image_{viewer_id}', image_data)
        setattr(self, f'_cached_index_{viewer_id}', index)
        setattr(self, f'_cached_original_size_{viewer_id}', original_size)
        
        return image_data, original_size
    
    def show_image(self, viewer_id):
        """显示指定查看器的当前图像
        
        Args:
            viewer_id: 查看器ID（1或2）
        """
        if self.image_count == 0:
            return
        
        current_index = getattr(self, f'current_image_index_{viewer_id}')
        viewer = getattr(self, f'image_viewer_{viewer_id}')
        slider = getattr(self, f'image_slider_{viewer_id}')
        index_label = getattr(self, f'image_index_label_{viewer_id}')
        info_label = getattr(self, f'image_info_label_{viewer_id}')
        
        # 按需获取图像数据（包含原始尺寸）
        current_data, original_size = self._get_cached_image(viewer_id, current_index)
        
        if current_data is None:
            info_label.setText("图像加载失败")
            return
        
        # 更新图像查看器（传递原始尺寸用于坐标映射）
        viewer.set_image_from_array(current_data, original_size=original_size)
        
        # 更新滑块
        slider.blockSignals(True)
        slider.setValue(current_index)
        slider.blockSignals(False)
        
        # 更新索引标签
        index_label.setText(f"{current_index + 1}/{self.image_count}")
        
        # 更新图像信息（显示原始尺寸）
        file_name = os.path.basename(self.image_files[current_index])
        display_shape = current_data.shape
        if original_size:
            orig_w, orig_h = original_size
            if display_shape[0] != orig_h or display_shape[1] != orig_w:
                # 显示降采样信息
                if current_data.ndim == 2:
                    info = f"{file_name} | 原始: {orig_w}x{orig_h} | 显示: {display_shape[1]}x{display_shape[0]} | 单波段"
                elif current_data.ndim == 3:
                    info = f"{file_name} | 原始: {orig_w}x{orig_h} | 显示: {display_shape[1]}x{display_shape[0]} | {display_shape[2]}波段"
                else:
                    info = f"{file_name} | 尺寸: {display_shape}"
            else:
                # 没有降采样
                if current_data.ndim == 2:
                    info = f"{file_name} | 尺寸: {orig_w}x{orig_h} | 单波段"
                elif current_data.ndim == 3:
                    info = f"{file_name} | 尺寸: {orig_w}x{orig_h} | {display_shape[2]}波段"
                else:
                    info = f"{file_name} | 尺寸: {display_shape}"
        else:
            if current_data.ndim == 2:
                info = f"{file_name} | 尺寸: {display_shape[1]}x{display_shape[0]} | 单波段"
            elif current_data.ndim == 3:
                info = f"{file_name} | 尺寸: {display_shape[1]}x{display_shape[0]} | {display_shape[2]}波段"
            else:
                info = f"{file_name} | 尺寸: {display_shape}"
        
        info_label.setText(info)
        
        # 如果已选择像素，更新曲线高亮
        if self.selected_pixel:
            self.update_time_series_plot()
    
    def open_folder(self):
        """打开图像文件夹"""
        # 读取上次打开的路径
        settings = get_settings()
        last_folder = settings.value("last_folder_path", "")
        
        folder = QFileDialog.getExistingDirectory(self, "选择图像文件夹", last_folder)
        if not folder:
            return
        
        # 保存当前路径
        settings.setValue("last_folder_path", folder)
        
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
    
    def open_h5_timeseries(self):
        """打开h5时序数据"""
        # 读取上次打开的路径
        settings = get_settings()
        last_folder = settings.value("last_h5_path", "")
        
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择h5时序数据文件", last_folder, "HDF5 Files (*.h5 *.hdf5);;All Files (*)")
        
        if not file_path:
            return
        
        # 保存当前路径
        settings.setValue("last_h5_path", os.path.dirname(file_path))
        
        try:
            # 打开h5文件获取元信息（不加载全部数据）
            with h5py.File(file_path, 'r') as h5f:
                # 读取日期列表
                if 'date' not in h5f:
                    QMessageBox.critical(self, "错误", "h5文件中未找到'date'数据集！")
                    return
                
                # 读取时序数据元信息
                if 'timeseries' not in h5f:
                    QMessageBox.critical(self, "错误", "h5文件中未找到'timeseries'数据集！")
                    return
                
                # 读取日期
                dates = h5f['date'][:]
                # 将字节串转换为字符串（如果需要）
                if dates.dtype.kind == 'S' or dates.dtype.kind == 'U':
                    self.date_list = [d.decode('utf-8') if isinstance(d, bytes) else str(d) for d in dates]
                else:
                    self.date_list = [str(d) for d in dates]
                
                # 获取时序数据的形状（不读取数据本身）
                timeseries_shape = h5f['timeseries'].shape
                
                # 检查数据维度
                if len(timeseries_shape) != 3:
                    QMessageBox.critical(self, "错误", 
                                       f"时序数据维度错误！期望3维(时间, 高度, 宽度)，得到{len(timeseries_shape)}维")
                    return
                
                num_dates = timeseries_shape[0]
                height = timeseries_shape[1]
                width = timeseries_shape[2]
                
                # 检查第一帧是否全为0，如果是则跳过
                start_index = 0
                first_frame = h5f['timeseries'][0, :, :]
                if num_dates > 0 and np.all(first_frame == 0):
                    start_index = 1
                    QMessageBox.information(self, "提示", "检测到第一帧数据全为0，已自动跳过")
                
                # 调整日期列表
                if start_index > 0 and len(self.date_list) > start_index:
                    self.date_list = self.date_list[start_index:]
                
                # 设置按需加载相关属性
                self.data_source_type = 'h5'
                self.h5_file_path = file_path
                self.h5_start_index = start_index
                self.image_shape = (height, width)
                self.image_count = num_dates - start_index
                
                # 清空缓存
                self._cached_image_1 = None
                self._cached_index_1 = -1
                self._cached_original_size_1 = None
                self._cached_image_2 = None
                self._cached_index_2 = -1
                self._cached_original_size_2 = None
                self.selected_pixel = None
                
                # 生成文件名列表（用于显示）
                self.image_files = []
                for i in range(self.image_count):
                    if i < len(self.date_list):
                        self.image_files.append(f"{self.date_list[i]}.h5")
                    else:
                        self.image_files.append(f"frame_{i + start_index:04d}.h5")
                
                # 更新UI
                self.image_count_label.setText(f"已加载 {self.image_count} 张时序影像")
                
                # 更新两个窗口的控件
                for viewer_id in [1, 2]:
                    slider = getattr(self, f'image_slider_{viewer_id}')
                    prev_btn = getattr(self, f'prev_btn_{viewer_id}')
                    next_btn = getattr(self, f'next_btn_{viewer_id}')
                    
                    slider.setMaximum(self.image_count - 1)
                    slider.setEnabled(True)
                    prev_btn.setEnabled(True)
                    next_btn.setEnabled(True)
                
                # 设置默认的彩色colormap
                self.colormap_combo.setCurrentText('jet')
                
                # 设置默认Nodata值为0（h5数据）
                self.nodata_value = 0
                self.image_viewer_1.set_nodata_value(0)
                self.image_viewer_2.set_nodata_value(0)
                
                # 显示第一张和第二张图像
                self.current_image_index_1 = 0
                self.current_image_index_2 = min(1, self.image_count - 1)
                self.show_image(1)
                self.show_image(2)
                
                QMessageBox.information(self, "成功", 
                                      f"成功加载h5时序数据！\n" +
                                      f"影像数量: {self.image_count}\n" +
                                      f"影像尺寸: {width} x {height}\n" +
                                      f"日期范围: {self.date_list[0]} 至 {self.date_list[-1]}")
                
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开h5文件失败: {str(e)}")
            traceback.print_exc()
    
    def load_images(self, file_list):
        """加载图像列表（按需加载模式，只读取第一张获取元信息）"""
        if not file_list:
            return
        
        try:
            # 清空之前的数据
            self.image_files = []
            self.date_list = []
            self.selected_pixel = None
            self.nodata_value = None
            
            # 清空缓存
            self._cached_image_1 = None
            self._cached_index_1 = -1
            self._cached_original_size_1 = None
            self._cached_image_2 = None
            self._cached_index_2 = -1
            self._cached_original_size_2 = None
            
            # 检查第一张图像是否是大型TIFF，提示创建金字塔
            first_file = file_list[0]
            ext = os.path.splitext(first_file)[1].lower()
            if ext in ['.tif', '.tiff']:
                self._check_and_build_overviews(first_file)
            
            # 使用降采样读取第一张图像以检查尺寸和波段数
            first_image_data, first_nodata, first_original_size = self._read_image_downsampled(file_list[0])
            if first_image_data is None:
                QMessageBox.critical(self, "错误", f"无法读取第一张图像: {file_list[0]}")
                return
            
            # 保存第一张图像的nodata值和形状（使用原始尺寸）
            self.nodata_value = first_nodata
            if first_original_size:
                orig_w, orig_h = first_original_size
                if first_image_data.ndim == 2:
                    reference_shape = (orig_h, orig_w)
                else:
                    reference_shape = (orig_h, orig_w, first_image_data.shape[2])
            else:
                reference_shape = first_image_data.shape
            self.image_shape = reference_shape
            
            # 设置数据源类型
            self.data_source_type = 'folder'
            self.h5_file_path = None
            self.h5_start_index = 0
            
            # 验证所有图像的一致性（只检查文件是否可读，不加载全部数据）
            inconsistent_files = []
            valid_files = []
            
            for file_path in file_list:
                # 只检查文件是否存在和可读
                if not os.path.exists(file_path):
                    inconsistent_files.append(f"{os.path.basename(file_path)}: 文件不存在")
                    continue
                
                # 对于第一个文件，我们已经验证过了
                if file_path == file_list[0]:
                    valid_files.append(file_path)
                    continue
                
                # 对于其他文件，快速检查尺寸（通过读取元数据而非全部数据）
                try:
                    ext = os.path.splitext(file_path)[1].lower()
                    if ext in ['.tif', '.tiff']:
                        ds = gdal.Open(file_path)
                        if ds is None:
                            inconsistent_files.append(f"{os.path.basename(file_path)}: 读取失败")
                            continue
                        width = ds.RasterXSize
                        height = ds.RasterYSize
                        band_count = ds.RasterCount
                        ds = None
                        
                        # 检查尺寸一致性
                        if reference_shape[0] != height or reference_shape[1] != width:
                            inconsistent_files.append(
                                f"{os.path.basename(file_path)}: 尺寸({width}x{height}) != 参考尺寸({reference_shape[1]}x{reference_shape[0]})"
                            )
                            continue
                    else:
                        # 对于非TIFF文件，使用PIL检查尺寸
                        img = Image.open(file_path)
                        img_width, img_height = img.size
                        if reference_shape[0] != img_height or reference_shape[1] != img_width:
                            inconsistent_files.append(
                                f"{os.path.basename(file_path)}: 尺寸({img_width}x{img_height}) != 参考尺寸({reference_shape[1]}x{reference_shape[0]})"
                            )
                            continue
                    
                    valid_files.append(file_path)
                except Exception as e:
                    inconsistent_files.append(f"{os.path.basename(file_path)}: {str(e)}")
                    continue
            
            # 如果有不一致的文件，提示用户
            if inconsistent_files:
                message = "以下文件与参考图像不一致，已跳过：\n" + "\n".join(inconsistent_files[:10])
                if len(inconsistent_files) > 10:
                    message += f"\n... 还有 {len(inconsistent_files) - 10} 个文件"
                QMessageBox.warning(self, "警告", message)
            
            if not valid_files:
                QMessageBox.critical(self, "错误", "没有成功加载任何图像！")
                return
            
            # 只保存文件路径，不加载数据
            self.image_files = valid_files
            self.image_count = len(valid_files)
            
            # 更新UI
            self.image_count_label.setText(f"已加载 {self.image_count} 张图像")
            
            # 更新两个窗口的控件
            for viewer_id in [1, 2]:
                slider = getattr(self, f'image_slider_{viewer_id}')
                prev_btn = getattr(self, f'prev_btn_{viewer_id}')
                next_btn = getattr(self, f'next_btn_{viewer_id}')
                
                slider.setMaximum(self.image_count - 1)
                slider.setEnabled(True)
                prev_btn.setEnabled(True)
                next_btn.setEnabled(True)
            
            # 设置Nodata值到两个查看器
            self.image_viewer_1.set_nodata_value(self.nodata_value)
            self.image_viewer_2.set_nodata_value(self.nodata_value)
            
            # 显示第一张和第二张图像
            self.current_image_index_1 = 0
            self.current_image_index_2 = min(1, self.image_count - 1)  # 如果只有一张图像，两个窗口都显示第一张
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
            tuple: (图像数据, nodata值) 或 (None, None)
        """
        try:
            ext = os.path.splitext(file_path)[1].lower()
            
            if ext in ['.tif', '.tiff']:
                # 使用GDAL读取TIFF
                ds = gdal.Open(file_path)
                if ds is None:
                    return None, None
                
                band_count = ds.RasterCount
                
                # 获取Nodata值（从第一个波段）
                band1 = ds.GetRasterBand(1)
                nodata_value = band1.GetNoDataValue()
                
                if band_count == 1:
                    # 单波段
                    data = band1.ReadAsArray()
                else:
                    # 多波段
                    data = []
                    for i in range(1, band_count + 1):
                        band = ds.GetRasterBand(i)
                        data.append(band.ReadAsArray())
                    data = np.stack(data, axis=-1)
                
                ds = None
                return data, nodata_value
            else:
                # 使用PIL读取普通图像
                img = Image.open(file_path)
                data = np.array(img)
                
                # 如果是单通道灰度图，确保是2D
                if data.ndim == 2:
                    return data, None
                elif data.ndim == 3:
                    # 如果有alpha通道，去掉
                    if data.shape[2] == 4:
                        data = data[:, :, :3]
                    return data, None
                else:
                    return None, None
                
        except Exception as e:
            print(f"读取图像失败 {file_path}: {e}")
            return None, None
    
    def _read_image_downsampled(self, file_path, max_size=2048):
        """
        降采样读取图像，用于大图像预览
        
        Args:
            file_path: 图像文件路径
            max_size: 最大边长（像素），默认2048
            
        Returns:
            tuple: (降采样后的图像数据, nodata值, 原始尺寸(width, height)) 或 (None, None, None)
        """
        try:
            ext = os.path.splitext(file_path)[1].lower()
            
            if ext in ['.tif', '.tiff']:
                return self._read_tiff_downsampled(file_path, max_size)
            else:
                # 使用PIL读取普通图像（PIL支持降采样）
                img = Image.open(file_path)
                original_size = img.size  # (width, height)
                
                # 计算降采样比例
                max_dim = max(original_size)
                if max_dim > max_size:
                    scale = max_size / max_dim
                    new_size = (int(original_size[0] * scale), int(original_size[1] * scale))
                    img = img.resize(new_size, Image.Resampling.LANCZOS)
                
                data = np.array(img)
                
                # 如果是单通道灰度图，确保是2D
                if data.ndim == 2:
                    return data, None, original_size
                elif data.ndim == 3:
                    # 如果有alpha通道，去掉
                    if data.shape[2] == 4:
                        data = data[:, :, :3]
                    return data, None, original_size
                else:
                    return None, None, None
                
        except Exception as e:
            print(f"降采样读取图像失败 {file_path}: {e}")
            return None, None, None
    
    def _read_tiff_downsampled(self, file_path, max_size=2048):
        """
        使用GDAL降采样读取TIFF，优先使用金字塔（Overview）
        
        Args:
            file_path: TIFF文件路径
            max_size: 最大边长（像素）
            
        Returns:
            tuple: (降采样后的图像数据, nodata值, 原始尺寸(width, height)) 或 (None, None, None)
        """
        try:
            ds = gdal.Open(file_path)
            if ds is None:
                return None, None, None
            
            original_width = ds.RasterXSize
            original_height = ds.RasterYSize
            original_size = (original_width, original_height)
            band_count = ds.RasterCount
            
            # 获取Nodata值
            band1 = ds.GetRasterBand(1)
            nodata_value = band1.GetNoDataValue()
            
            # 计算目标尺寸
            max_dim = max(original_width, original_height)
            if max_dim <= max_size:
                # 不需要降采样
                if band_count == 1:
                    data = band1.ReadAsArray()
                else:
                    data = []
                    for i in range(1, band_count + 1):
                        band = ds.GetRasterBand(i)
                        data.append(band.ReadAsArray())
                    data = np.stack(data, axis=-1)
                ds = None
                return data, nodata_value, original_size
            
            # 计算降采样后的尺寸
            scale = max_size / max_dim
            target_width = int(original_width * scale)
            target_height = int(original_height * scale)
            
            # 检查是否有金字塔（Overview）
            overview_count = band1.GetOverviewCount()
            
            if overview_count > 0:
                # 找到最适合的金字塔级别
                best_overview = self._find_best_overview(band1, target_width, target_height)
                if best_overview is not None:
                    # 从金字塔读取
                    if band_count == 1:
                        data = best_overview.ReadAsArray(
                            buf_xsize=target_width, 
                            buf_ysize=target_height
                        )
                    else:
                        data = []
                        for i in range(1, band_count + 1):
                            band = ds.GetRasterBand(i)
                            ov = self._find_best_overview(band, target_width, target_height)
                            if ov:
                                band_data = ov.ReadAsArray(
                                    buf_xsize=target_width, 
                                    buf_ysize=target_height
                                )
                            else:
                                band_data = band.ReadAsArray(
                                    buf_xsize=target_width, 
                                    buf_ysize=target_height
                                )
                            data.append(band_data)
                        data = np.stack(data, axis=-1)
                    ds = None
                    return data, nodata_value, original_size
            
            # 没有金字塔，直接降采样读取
            if band_count == 1:
                data = band1.ReadAsArray(
                    buf_xsize=target_width, 
                    buf_ysize=target_height
                )
            else:
                data = []
                for i in range(1, band_count + 1):
                    band = ds.GetRasterBand(i)
                    band_data = band.ReadAsArray(
                        buf_xsize=target_width, 
                        buf_ysize=target_height
                    )
                    data.append(band_data)
                data = np.stack(data, axis=-1)
            
            ds = None
            return data, nodata_value, original_size
            
        except Exception as e:
            print(f"降采样读取TIFF失败 {file_path}: {e}")
            traceback.print_exc()
            return None, None, None
    
    def _find_best_overview(self, band, target_width, target_height):
        """
        找到最适合目标尺寸的金字塔级别
        
        Args:
            band: GDAL Band对象
            target_width: 目标宽度
            target_height: 目标高度
            
        Returns:
            最佳的Overview对象，如果没有合适的返回None
        """
        overview_count = band.GetOverviewCount()
        if overview_count == 0:
            return None
        
        best_overview = None
        best_size_diff = float('inf')
        
        for i in range(overview_count):
            ov = band.GetOverview(i)
            ov_width = ov.XSize
            ov_height = ov.YSize
            
            # 选择尺寸大于等于目标尺寸且最接近的金字塔
            if ov_width >= target_width and ov_height >= target_height:
                size_diff = (ov_width - target_width) + (ov_height - target_height)
                if size_diff < best_size_diff:
                    best_size_diff = size_diff
                    best_overview = ov
        
        # 如果没有找到大于目标的，选择最大的那个
        if best_overview is None and overview_count > 0:
            best_overview = band.GetOverview(0)  # 第一个通常是最大的
        
        return best_overview
    
    def _check_and_build_overviews(self, file_path):
        """
        检查TIFF文件是否有金字塔，如果没有则询问用户是否创建
        
        Args:
            file_path: TIFF文件路径
            
        Returns:
            bool: 是否有金字塔可用
        """
        try:
            ext = os.path.splitext(file_path)[1].lower()
            if ext not in ['.tif', '.tiff']:
                return False
            
            ds = gdal.Open(file_path)
            if ds is None:
                return False
            
            band1 = ds.GetRasterBand(1)
            overview_count = band1.GetOverviewCount()
            width = ds.RasterXSize
            height = ds.RasterYSize
            ds = None
            
            # 检查图像是否足够大需要金字塔
            max_dim = max(width, height)
            if max_dim <= 4096:  # 小于4096像素不需要金字塔
                return True
            
            if overview_count > 0:
                return True
            
            # 询问用户是否创建金字塔
            reply = QMessageBox.question(
                self, 
                "创建金字塔",
                f"检测到大图像 ({width}x{height})，但没有金字塔（Overview）。\n"
                f"金字塔可以显著提升大图像的加载和显示速度。\n\n"
                f"是否现在创建金字塔？（这可能需要一些时间）",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            
            if reply == QMessageBox.Yes:
                return self._build_overviews(file_path)
            
            return False
            
        except Exception as e:
            print(f"检查金字塔失败 {file_path}: {e}")
            return False
    
    def _build_overviews(self, file_path):
        """
        为TIFF文件创建金字塔
        
        Args:
            file_path: TIFF文件路径
            
        Returns:
            bool: 是否成功创建
        """
        try:
            # 以更新模式打开
            ds = gdal.Open(file_path, gdal.GA_Update)
            if ds is None:
                QMessageBox.warning(self, "警告", "无法以写入模式打开文件，可能是只读文件。")
                return False
            
            # 获取图像尺寸
            width = ds.RasterXSize
            height = ds.RasterYSize
            
            # 计算金字塔级别（2, 4, 8, 16, ...直到最小边小于256）
            overview_levels = []
            level = 2
            min_dim = min(width, height)
            while min_dim / level > 256:
                overview_levels.append(level)
                level *= 2
            overview_levels.append(level)  # 添加最后一级
            
            # 显示进度提示
            QMessageBox.information(
                self, 
                "正在创建金字塔",
                f"正在为图像创建金字塔...\n级别: {overview_levels}\n\n请稍候，这可能需要几分钟。"
            )
            
            # 创建金字塔
            # 使用NEAREST重采样方法（速度快，适合分类数据）
            # 可以改为AVERAGE（适合连续数据）或CUBIC（更平滑）
            ds.BuildOverviews("NEAREST", overview_levels)
            
            ds = None
            
            QMessageBox.information(self, "完成", "金字塔创建成功！")
            return True
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"创建金字塔失败: {str(e)}")
            traceback.print_exc()
            return False
    
    def sort_images(self):
        """排序图像"""
        if not self.image_files:
            return
        
        # 获取排序方式
        reverse = (self.sort_order_combo.currentIndex() == 1)
        
        # 创建索引列表并排序
        indices = list(range(len(self.image_files)))
        indices.sort(key=lambda i: os.path.basename(self.image_files[i]), reverse=reverse)
        
        # 重新排列文件路径
        self.image_files = [self.image_files[i] for i in indices]
        
        # 如果有日期列表，也需要重新排列
        if self.date_list and len(self.date_list) == len(indices):
            self.date_list = [self.date_list[i] for i in indices]
        
        # 清空缓存（因为索引顺序改变了）
        self._cached_image_1 = None
        self._cached_index_1 = -1
        self._cached_original_size_1 = None
        self._cached_image_2 = None
        self._cached_index_2 = -1
        self._cached_original_size_2 = None
        
        # 重置当前索引
        self.current_image_index_1 = 0
        self.current_image_index_2 = min(1, self.image_count - 1)  # 如果只有一张图像，两个窗口都显示第一张
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
    
    def _get_pixel_value_at(self, index, x, y):
        """按需获取指定索引图像在指定位置的像素值
        
        Args:
            index: 图像索引
            x: X坐标
            y: Y坐标
            
        Returns:
            像素值（标量或数组）
        """
        if self.data_source_type == 'h5':
            # 从h5文件按需读取单个像素
            try:
                with h5py.File(self.h5_file_path, 'r') as h5f:
                    actual_index = index + self.h5_start_index
                    pixel_value = h5f['timeseries'][actual_index, y, x]
                    return pixel_value
            except Exception as e:
                print(f"读取h5像素值失败 (索引 {index}, 位置 ({x}, {y})): {e}")
                return np.nan
        else:
            # 从文件按需读取像素值
            if index < len(self.image_files):
                try:
                    file_path = self.image_files[index]
                    ext = os.path.splitext(file_path)[1].lower()
                    
                    if ext in ['.tif', '.tiff']:
                        # 使用GDAL读取单个像素
                        ds = gdal.Open(file_path)
                        if ds is None:
                            return np.nan
                        
                        band_count = ds.RasterCount
                        if band_count == 1:
                            band = ds.GetRasterBand(1)
                            # 读取单个像素 (x_offset, y_offset, x_size, y_size)
                            pixel_value = band.ReadAsArray(x, y, 1, 1)[0, 0]
                        else:
                            pixel_values = []
                            for i in range(1, band_count + 1):
                                band = ds.GetRasterBand(i)
                                val = band.ReadAsArray(x, y, 1, 1)[0, 0]
                                pixel_values.append(val)
                            pixel_value = np.array(pixel_values)
                        ds = None
                        return pixel_value
                    else:
                        # 使用PIL读取，需要加载整张图像
                        img = Image.open(file_path)
                        data = np.array(img)
                        if data.ndim == 2:
                            return data[y, x]
                        else:
                            return data[y, x]
                except Exception as e:
                    print(f"读取像素值失败 (文件 {file_path}, 位置 ({x}, {y})): {e}")
                    return np.nan
            return np.nan
    
    def _get_all_pixel_values_at(self, x, y):
        """批量获取所有时序图像在指定位置的像素值
        
        Args:
            x: X坐标
            y: Y坐标
            
        Returns:
            像素值列表
        """
        if self.data_source_type == 'h5':
            # 从h5文件批量读取整列像素（更高效）
            try:
                with h5py.File(self.h5_file_path, 'r') as h5f:
                    # 读取所有时间步在指定像素位置的值
                    start_idx = self.h5_start_index
                    end_idx = start_idx + self.image_count
                    pixel_values = h5f['timeseries'][start_idx:end_idx, y, x]
                    return list(pixel_values)
            except Exception as e:
                print(f"批量读取h5像素值失败 (位置 ({x}, {y})): {e}")
                return [np.nan] * self.image_count
        else:
            # 从文件夹逐个读取
            values = []
            for i in range(self.image_count):
                val = self._get_pixel_value_at(i, x, y)
                values.append(val)
            return values
    
    def update_time_series_plot(self):
        """更新时序曲线图（按需读取像素值）"""
        if not self.selected_pixel or self.image_count == 0:
            return
        
        x, y = self.selected_pixel
        
        # 清空图表和数据点
        self.figure.clear()
        self._plot_data_points = []
        self._plot_annotation = None
        
        # 提取时序数据
        time_indices = list(range(self.image_count))
        
        # 批量获取所有像素值
        all_values = self._get_all_pixel_values_at(x, y)
        
        # 判断是单波段还是多波段
        first_value = all_values[0] if all_values else None
        is_multiband = isinstance(first_value, np.ndarray) and first_value.ndim > 0
        
        ax = self.figure.add_subplot(111)
        self._plot_ax = ax
        
        if not is_multiband:
            # 单波段灰度图
            values = all_values
            
            ax.plot(time_indices, values, 'o-', label='像素值', linewidth=1, markersize=4, color='blue')
            
            # 保存数据点用于悬浮检测
            for i, v in enumerate(values):
                self._plot_data_points.append((i, v, i))  # (x坐标, y坐标, 图像索引)
            
            # 高亮两个窗口当前图像的点，用不同颜色
            ax.plot(self.current_image_index_1, values[self.current_image_index_1], 
                   'ro', markersize=6, label='窗口1', zorder=10)
            ax.plot(self.current_image_index_2, values[self.current_image_index_2], 
                   'go', markersize=6, label='窗口2', zorder=10)
            
            # 如果有日期列表，使用日期作为x轴标签
            if self.date_list:
                ax.set_xlabel('日期')
                # 设置x轴刻度标签（每隔一定间隔显示日期）
                step = max(1, len(self.date_list) // 10)  # 最多显示10个日期标签
                tick_positions = list(range(0, len(self.date_list), step))
                tick_labels = [self.date_list[i] for i in tick_positions]
                ax.set_xticks(tick_positions)
                ax.set_xticklabels(tick_labels, rotation=45, ha='right')
            else:
                ax.set_xlabel('图像索引')
            
            ax.set_ylabel('像素值')
            ax.set_title(f'像素 ({x}, {y}) 的时序曲线')
            ax.legend()
            ax.grid(True, alpha=0.3)
            
        else:
            # 多波段图像
            num_bands = len(first_value)
            
            # 为每个波段绘制曲线
            band_colors = ['red', 'green', 'blue', 'cyan', 'magenta', 'yellow']
            for band_idx in range(num_bands):
                band_values = [v[band_idx] if isinstance(v, np.ndarray) else v for v in all_values]
                
                color = band_colors[band_idx % len(band_colors)]
                ax.plot(time_indices, band_values, 'o-', label=f'波段{band_idx+1}', 
                       linewidth=1, markersize=3, color=color, alpha=0.7)
                
                # 保存第一个波段的数据点用于悬浮检测（多波段时使用第一波段）
                if band_idx == 0:
                    for i, v in enumerate(band_values):
                        self._plot_data_points.append((i, v, i))
            
            # 高亮两个窗口当前图像的位置（用竖线）
            ax.axvline(x=self.current_image_index_1, color='red', linestyle='--', 
                      linewidth=2, label='窗口1', alpha=0.8)
            ax.axvline(x=self.current_image_index_2, color='darkgreen', linestyle='--', 
                      linewidth=2, label='窗口2', alpha=0.8)
            
            # 如果是RGB图像（3波段），计算并绘制灰度值
            if num_bands == 3:
                gray_values = []
                for v in all_values:
                    if isinstance(v, np.ndarray) and len(v) >= 3:
                        r, g, b = v[0], v[1], v[2]
                        gray = 0.299 * r + 0.587 * g + 0.114 * b
                        gray_values.append(gray)
                    else:
                        gray_values.append(np.nan)
                
                ax.plot(time_indices, gray_values, 's--', label='灰度值', 
                       linewidth=1, markersize=3, alpha=0.5, color='black')
            
            # 如果有日期列表，使用日期作为x轴标签
            if self.date_list:
                ax.set_xlabel('日期')
                # 设置x轴刻度标签（每隔一定间隔显示日期）
                step = max(1, len(self.date_list) // 10)  # 最多显示10个日期标签
                tick_positions = list(range(0, len(self.date_list), step))
                tick_labels = [self.date_list[i] for i in tick_positions]
                ax.set_xticks(tick_positions)
                ax.set_xticklabels(tick_labels, rotation=45, ha='right')
            else:
                ax.set_xlabel('图像索引')
            
            ax.set_ylabel('像素值')
            ax.set_title(f'像素 ({x}, {y}) 的时序曲线')
            ax.legend()
            ax.grid(True, alpha=0.3)
        
        # 创建annotation对象（初始不可见）
        self._plot_annotation = ax.annotate(
            "", xy=(0, 0), xytext=(20, 20),
            textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.5", fc="yellow", alpha=0.9),
            arrowprops=dict(arrowstyle="->", connectionstyle="arc3,rad=0"),
            fontsize=9, zorder=100
        )
        self._plot_annotation.set_visible(False)
        
        self.figure.tight_layout()
        self.canvas.draw()
    
    def _on_plot_mouse_move(self, event):
        """处理时序曲线图上的鼠标移动事件，显示悬浮提示"""
        if self._plot_annotation is None or self._plot_ax is None:
            return
        
        if event.inaxes != self._plot_ax:
            # 鼠标不在axes内，隐藏annotation
            if self._plot_annotation.get_visible():
                self._plot_annotation.set_visible(False)
                self.canvas.draw_idle()
            return
        
        if not self._plot_data_points or not self.image_files:
            return
        
        # 查找最近的数据点
        mouse_x, mouse_y = event.xdata, event.ydata
        if mouse_x is None or mouse_y is None:
            return
        
        # 计算与所有数据点的距离（归一化坐标系）
        # 获取axes的数据范围用于归一化
        xlim = self._plot_ax.get_xlim()
        ylim = self._plot_ax.get_ylim()
        x_range = xlim[1] - xlim[0] if xlim[1] != xlim[0] else 1
        y_range = ylim[1] - ylim[0] if ylim[1] != ylim[0] else 1
        
        min_dist = float('inf')
        nearest_point = None
        nearest_index = -1
        
        for px, py, idx in self._plot_data_points:
            if py is None or (isinstance(py, float) and np.isnan(py)):
                continue
            # 归一化距离
            dx = (mouse_x - px) / x_range
            dy = (mouse_y - py) / y_range
            dist = dx * dx + dy * dy
            
            if dist < min_dist:
                min_dist = dist
                nearest_point = (px, py)
                nearest_index = idx
        
        # 设置阈值（归一化后的距离）
        threshold = 0.01  # 约 10% 的范围
        
        if nearest_point and min_dist < threshold and nearest_index >= 0:
            # 显示annotation
            px, py = nearest_point
            
            # 获取文件名
            if nearest_index < len(self.image_files):
                file_name = os.path.basename(self.image_files[nearest_index])
            else:
                file_name = f"索引 {nearest_index}"
            
            # 获取日期（如果有）
            if self.date_list and nearest_index < len(self.date_list):
                date_str = self.date_list[nearest_index]
                text = f"{file_name}\n日期: {date_str}\n值: {py:.4g}"
            else:
                text = f"{file_name}\n索引: {nearest_index}\n值: {py:.4g}"
            
            # 动态计算annotation偏移方向，避免超出边界
            # 计算数据点在axes中的相对位置 (0-1)
            xlim = self._plot_ax.get_xlim()
            ylim = self._plot_ax.get_ylim()
            x_rel = (px - xlim[0]) / (xlim[1] - xlim[0]) if xlim[1] != xlim[0] else 0.5
            y_rel = (py - ylim[0]) / (ylim[1] - ylim[0]) if ylim[1] != ylim[0] else 0.5
            
            # 根据位置决定偏移方向
            # 水平方向：如果点在右侧60%区域，向左偏移；否则向右
            offset_x = -80 if x_rel > 0.6 else 20
            # 垂直方向：如果点在上方70%区域，向下偏移；否则向上
            offset_y = -60 if y_rel > 0.7 else 20
            
            self._plot_annotation.xy = (px, py)
            self._plot_annotation.set_text(text)
            self._plot_annotation.xyann = (offset_x, offset_y)
            self._plot_annotation.set_visible(True)
            self.canvas.draw_idle()
        else:
            # 隐藏annotation
            if self._plot_annotation.get_visible():
                self._plot_annotation.set_visible(False)
                self.canvas.draw_idle()
    
    def _on_plot_click(self, event):
        """处理时序曲线图上的点击事件，跳转到对应的图像"""
        if self._plot_ax is None or event.inaxes != self._plot_ax:
            return
        
        if not self._plot_data_points or not self.image_files:
            return
        
        mouse_x, mouse_y = event.xdata, event.ydata
        if mouse_x is None or mouse_y is None:
            return
        
        # 计算与所有数据点的距离
        xlim = self._plot_ax.get_xlim()
        ylim = self._plot_ax.get_ylim()
        x_range = xlim[1] - xlim[0] if xlim[1] != xlim[0] else 1
        y_range = ylim[1] - ylim[0] if ylim[1] != ylim[0] else 1
        
        min_dist = float('inf')
        nearest_index = -1
        
        for px, py, idx in self._plot_data_points:
            if py is None or (isinstance(py, float) and np.isnan(py)):
                continue
            dx = (mouse_x - px) / x_range
            dy = (mouse_y - py) / y_range
            dist = dx * dx + dy * dy
            
            if dist < min_dist:
                min_dist = dist
                nearest_index = idx
        
        threshold = 0.01
        
        if min_dist < threshold and nearest_index >= 0:
            # 点击了某个数据点，将窗口1切换到该图像
            if nearest_index != self.current_image_index_1:
                self.current_image_index_1 = nearest_index
                self.show_image(1)
                # 更新曲线以更新高亮点
                self.update_time_series_plot()    
    def set_nodata_value(self):
        """设置Nodata值"""
        from PySide6.QtWidgets import QInputDialog
        
        # 获取当前Nodata值
        current_nodata = self.image_viewer_1.nodata_value if hasattr(self.image_viewer_1, 'nodata_value') else None
        if np.isnan(current_nodata) if isinstance(current_nodata, float) else False:
            current_text = "nan"
        else:
            current_text = str(current_nodata) if current_nodata is not None else ""
        
        # 弹出对话框让用户输入
        text, ok = QInputDialog.getText(self, "设置Nodata值", 
                                        "请输入Nodata值（nan表示NaN，留空表示取消设置）:",
                                        text=current_text)
        
        if ok:
            if text.strip() == "":
                # 取消Nodata设置
                self.image_viewer_1.set_nodata_value(None)
                self.image_viewer_2.set_nodata_value(None)
                QMessageBox.information(self, "成功", "已取消Nodata值设置")
            else:
                try:
                    # 支持nan值
                    if text.lower().strip() == "nan":
                        nodata_value = np.nan
                    else:
                        nodata_value = float(text)
                    
                    self.image_viewer_1.set_nodata_value(nodata_value)
                    self.image_viewer_2.set_nodata_value(nodata_value)
                    QMessageBox.information(self, "成功", f"已设置Nodata值为: {nodata_value}")
                except ValueError:
                    QMessageBox.warning(self, "错误", "请输入有效的数字或'nan'！")
    
    def on_viewer_mouse_moved(self, viewer_id, x, y, value):
        """鼠标位置移动事件，显示像素值
        
        Args:
            viewer_id: 查看器ID（1或2）
            x: X坐标（原始图像坐标）
            y: Y坐标（原始图像坐标）
            value: 像素值（已从显示数组中获取）
        """
        # 更新当前窗口的像素值标签
        pixel_value_label = getattr(self, f'pixel_value_label_{viewer_id}')
        if value is not None:
            if isinstance(value, (int, float, np.integer, np.floating)):
                if np.isnan(value) if isinstance(value, float) else False:
                    pixel_value_label.setText(f"像素值: ({x}, {y}) = NaN")
                else:
                    pixel_value_label.setText(f"像素值: ({x}, {y}) = {value:.6g}")
            elif isinstance(value, np.ndarray):
                if value.ndim == 0:
                    pixel_value_label.setText(f"像素值: ({x}, {y}) = {value:.6g}")
                else:
                    value_str = ", ".join([f"{v:.6g}" for v in value])
                    pixel_value_label.setText(f"像素值: ({x}, {y}) = [{value_str}]")
        else:
            pixel_value_label.setText("像素值: -")
        
        # 同时更新另一个窗口的像素值，基于它当前缓存的图像
        other_viewer_id = 2 if viewer_id == 1 else 1
        other_pixel_label = getattr(self, f'pixel_value_label_{other_viewer_id}')
        
        # 获取另一个窗口缓存的图像数据和原始尺寸
        other_cached_image = getattr(self, f'_cached_image_{other_viewer_id}')
        other_original_size = getattr(self, f'_cached_original_size_{other_viewer_id}', None)
        
        if other_cached_image is not None:
            # 计算降采样因子
            if other_original_size:
                orig_w, orig_h = other_original_size
                downsample_factor = orig_w / other_cached_image.shape[1]
            else:
                downsample_factor = 1.0
            
            # 将原始坐标转换为降采样后的显示坐标
            display_x = int(x / downsample_factor)
            display_y = int(y / downsample_factor)
            
            # 检查坐标是否有效
            if 0 <= display_x < other_cached_image.shape[1] and 0 <= display_y < other_cached_image.shape[0]:
                other_value = other_cached_image[display_y, display_x]
                
                # 显示另一个窗口的像素值（使用原始坐标）
                if isinstance(other_value, (int, float, np.integer, np.floating)):
                    if np.isnan(other_value) if isinstance(other_value, float) else False:
                        other_pixel_label.setText(f"像素值: ({x}, {y}) = NaN")
                    else:
                        other_pixel_label.setText(f"像素值: ({x}, {y}) = {other_value:.6g}")
                elif isinstance(other_value, np.ndarray):
                    if other_value.ndim == 0:
                        other_pixel_label.setText(f"像素值: ({x}, {y}) = {other_value:.6g}")
                    else:
                        value_str = ", ".join([f"{v:.6g}" for v in other_value])
                        other_pixel_label.setText(f"像素值: ({x}, {y}) = [{value_str}]")
            else:
                other_pixel_label.setText("像素值: -")  # 坐标超出范围
        else:
            other_pixel_label.setText("像素值: -")

