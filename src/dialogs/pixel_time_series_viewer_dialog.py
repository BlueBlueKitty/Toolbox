'''
Author: Yibo Yuan 2633669459@qq.com
Description: 像素时序查看器对话框

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
import re
import numpy as np
from pathlib import Path
from typing import Optional, Tuple
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QFileDialog, QLabel, QSlider, QComboBox, QMessageBox,
                               QSplitter, QGroupBox, QGridLayout, QCheckBox, QFormLayout,
                               QDialogButtonBox, QInputDialog, QFrame)
from PySide6.QtCore import Qt, QSettings

# 导入共享的GAMMA对话框
from src.dialogs.gamma_dialogs import GammaTimeSeriesDialog

# 配置文件路径
def get_settings():
    config_dir = Path.home() / ".toolbox"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "pixel_time_series_viewer.ini"
    return QSettings(str(config_file), QSettings.IniFormat)

def extract_dates_from_filenames(file_paths):
    """从文件名列表中提取日期
    
    尝试从文件名中提取日期，支持多种常见格式：
    - YYYYMMDD (如 20210315)
    - YYYY-MM-DD (如 2021-03-15)
    - YYYY_MM_DD (如 2021_03_15)
    - YYYYDDD (年+儒略日，如 2021074)
    
    Args:
        file_paths: 文件路径列表
        
    Returns:
        list: 提取的日期字符串列表，如果提取失败则返回None
    """
    date_patterns = [
        # YYYY-MM-DD 或 YYYY_MM_DD 或 YYYY.MM.DD
        r'(\d{4})[-_\.](\d{2})[-_\.](\d{2})',
        # YYYYMMDD
        r'(\d{8})',
        # YYYYDDD (年+儒略日)
        r'(\d{4})(\d{3})'
    ]
    
    dates = []
    for file_path in file_paths:
        filename = os.path.basename(file_path)
        found_date = None
        
        for pattern in date_patterns:
            match = re.search(pattern, filename)
            if match:
                if len(match.groups()) == 3:
                    # YYYY-MM-DD 格式
                    year, month, day = match.groups()
                    found_date = f"{year}-{month}-{day}"
                elif len(match.groups()) == 1:
                    date_str = match.group(1)
                    if len(date_str) == 8:
                        # YYYYMMDD 格式
                        found_date = f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"
                    elif len(date_str) == 7:
                        # YYYYDDD 格式（年+儒略日），保持原样
                        found_date = date_str
                elif len(match.groups()) == 2:
                    # YYYYDDD 格式
                    year, doy = match.groups()
                    found_date = f"{year}-{doy}"
                break
        
        if found_date:
            dates.append(found_date)
        else:
            # 如果任何一个文件没有找到日期，返回None
            return None
    
    return dates if dates else None
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
import traceback

from src.widgets import ImageViewer, ColormapComboBox, RenderSettingsWidget, ColorbarWidget
from src.utils.gamma_file_process import (
    GAMMA_FORMATS,
    read_gamma_downsampled,
    read_gamma_pixel,
    find_valid_par_for_binary,
    validate_dimensions,
    complex_to_phase,
    is_gamma_binary_file,
)
from src.utils.image_io import (
    read_tiff,
    read_tiff_downsampled,
    get_tiff_info,
    read_image,
    read_image_downsampled,
    read_any_image_downsampled,
    read_any_image_pixel,
    get_image_info,
    get_geotransform,
    pixel_to_lonlat,
    find_best_overview,
    read_h5_timeseries_metadata,
    read_h5_timeseries_frame,
    read_h5_timeseries_pixel,
    list_h5_datasets,
    read_h5_dataset,
    build_coordinate_transform,
    invert_geotransform,
    get_raster_bounds_wgs84,
    bounds_overlap,
    lonlat_to_pixel,
)


class PixelTimeSeriesViewerDialog(QDialog):
    """像素时序查看器对话框"""
    
    def __init__(self, parent=None, max_display_size=2048):
        super().__init__(parent)
        
        # 降采样配置
        self._configured_max_display_size = max_display_size
        self.max_display_size = max_display_size  # 显示时的最大尺寸
        
        self.setWindowTitle("像素时序查看器")
        self.resize(1600, 900)
        
        # 存储时序图像数据（按需加载模式）
        self.image_files = []  # 文件路径列表
        self.date_list = []  # 日期列表（用于h5时序数据）
        self.current_image_index_1 = 0  # 窗口1当前显示的图像索引
        self.current_image_index_2 = 0  # 窗口2当前显示的图像索引
        self.selected_pixel = None  # 选中的像素坐标 (x, y)
        self.selected_geo = None  # 选中的WGS84地理坐标 (lon, lat)
        self.selected_viewer_id = None  # 最近一次选点来自哪个窗口
        self.nodata_value = None  # Nodata值
        self._nodata_user_locked = False  # 是否使用用户手动指定的Nodata
        
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
        
        # GAMMA时序文件相关
        self.is_gamma_timeseries = False  # 是否为GAMMA时序数据
        self.gamma_format = "float32"  # GAMMA数据格式
        self.gamma_width = None  # GAMMA图像宽度
        self.gamma_height = None  # GAMMA图像高度
        
        # 时序曲线悬浮提示相关
        self._plot_data_points = []  # 存储绘图数据点 [(x, y, index), ...]
        self._plot_annotation = None  # matplotlib annotation对象
        self._plot_ax = None  # 当前axes对象
        
        # 地理信息
        self.geotransform = None  # GDAL地理变换参数
        self.projection = None  # 投影信息
        self.image_metadata = []  # 每张影像的元数据（文件夹模式）
        self.folder_sync_mode = 'rowcol'  # rowcol | geo | unavailable
        self.folder_sync_message = ""
        self.folder_scene_rect = None  # 统一场景范围（WGS84）
        
        # dB转换标志
        self._converted_to_db = False  # 是否已转换为dB
        
        # 创建UI
        self._create_ui()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            event.ignore()
            return
        super().keyPressEvent(event)
        
    def _create_ui(self):
        """创建用户界面"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)
        
        # 第一行：文件操作和基本控制
        control_layout1 = QHBoxLayout()
        control_layout1.setSpacing(10)
        
        self.open_folder_btn = QPushButton("打开图像文件夹")
        self.open_folder_btn.clicked.connect(self.open_folder)
        control_layout1.addWidget(self.open_folder_btn)
        
        self.open_gamma_folder_btn = QPushButton("打开GAMMA时序数据")
        self.open_gamma_folder_btn.clicked.connect(self.open_gamma_folder)
        control_layout1.addWidget(self.open_gamma_folder_btn)
        
        self.open_h5_btn = QPushButton("打开h5时序数据")
        self.open_h5_btn.clicked.connect(self.open_h5_timeseries)
        control_layout1.addWidget(self.open_h5_btn)
        
        control_layout1.addWidget(QLabel("排序:"))
        self.sort_order_combo = QComboBox()
        self.sort_order_combo.addItems(["正序", "倒序"])
        self.sort_order_combo.currentIndexChanged.connect(self.sort_images)
        control_layout1.addWidget(self.sort_order_combo)
        
        self.image_count_label = QLabel("未加载图像")
        control_layout1.addWidget(self.image_count_label)
        
        control_layout1.addStretch()
        
        # Nodata值设置
        self.set_nodata_btn = QPushButton("设置Nodata值")
        self.set_nodata_btn.clicked.connect(self.set_nodata_value)
        control_layout1.addWidget(self.set_nodata_btn)
        
        # 转为dB按钮
        self.to_db_btn = QPushButton("转为dB")
        self.to_db_btn.clicked.connect(self.convert_to_db)
        self.to_db_btn.setEnabled(False)
        control_layout1.addWidget(self.to_db_btn)

        self.toggle_display_limit_btn = QPushButton()
        self.toggle_display_limit_btn.clicked.connect(self.toggle_display_limit_override)
        control_layout1.addWidget(self.toggle_display_limit_btn)
        self._update_display_limit_button()
        
        main_layout.addLayout(control_layout1)
        
        # 第二行：渲染设置（波段选择、Colormap、拉伸、Gamma等）
        # 顺序：波段选择 | Colormap+反向 | 拉伸 | 最大最小值 | Gamma
        control_layout2 = QHBoxLayout()
        control_layout2.setSpacing(5)
        
        # 渲染设置组件（包含波段选择、反向、拉伸、最大最小值、Gamma）
        self.render_settings = RenderSettingsWidget(compact=True)
        self.render_settings.settings_changed.connect(self.on_render_settings_changed)
        self.render_settings.suggest_colormap.connect(self.on_suggest_colormap)
        
        # 从主窗口设置中读取平滑显示设置
        from PySide6.QtCore import QSettings
        settings = QSettings("Toolbox", "RemoteSensingToolbox")
        smooth_display = settings.value("display/smooth_display", False, type=bool)
        self.render_settings.set_smooth_display(smooth_display)
        
        # 把波段选择部分放最前面
        control_layout2.addWidget(self.render_settings.band_widget)
        
        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFrameShadow(QFrame.Sunken)
        control_layout2.addWidget(sep)
        
        # Colormap选择
        control_layout2.addWidget(QLabel("Colormap:"))
        self.colormap_combo = ColormapComboBox()
        self.colormap_combo.currentTextChanged.connect(self.on_colormap_changed)
        control_layout2.addWidget(self.colormap_combo)
        
        # "反向"选项（从render_settings中获取）
        control_layout2.addWidget(self.render_settings.reverse_check)
        
        # 分隔线
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.VLine)
        sep2.setFrameShadow(QFrame.Sunken)
        control_layout2.addWidget(sep2)
        
        # 拉伸控件（从render_settings获取）
        control_layout2.addWidget(QLabel("拉伸:"))
        control_layout2.addWidget(self.render_settings.stretch_combo)
        control_layout2.addWidget(self.render_settings.stretch_param_widget)
        
        # 分隔线
        sep3 = QFrame()
        sep3.setFrameShape(QFrame.VLine)
        sep3.setFrameShadow(QFrame.Sunken)
        control_layout2.addWidget(sep3)
        
        # 最大最小值控件（从render_settings获取）
        control_layout2.addWidget(self.render_settings.auto_range_check)
        control_layout2.addWidget(self.render_settings.min_spin)
        control_layout2.addWidget(self.render_settings.range_dash_label)
        control_layout2.addWidget(self.render_settings.max_spin)
        
        # 分隔线
        sep4 = QFrame()
        sep4.setFrameShape(QFrame.VLine)
        sep4.setFrameShadow(QFrame.Sunken)
        control_layout2.addWidget(sep4)
        
        # Gamma控件（从render_settings获取）
        control_layout2.addWidget(QLabel("γ:"))
        control_layout2.addWidget(self.render_settings.gamma_spin)
        
        control_layout2.addStretch()
        
        main_layout.addLayout(control_layout2)
        
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
        main_layout = QVBoxLayout(panel)
        
        # 创建水平布局：图像查看器 + Colorbar
        image_layout = QHBoxLayout()
        
        # 图像查看器
        viewer = ImageViewer()
        setattr(self, f'image_viewer_{viewer_id}', viewer)
        
        # 连接像素点击信号
        viewer.pixel_clicked.connect(lambda x, y: self.on_pixel_clicked(viewer_id, x, y))
        
        # 连接鼠标移动信号，用于更新colorbar
        viewer.mouse_moved.connect(lambda x, y, val: self.on_viewer_mouse_moved(viewer_id, x, y, val))
        
        # 连接视图变换信号（用于同步缩放）
        viewer.view_transformed.connect(lambda t: self.sync_other_viewer(viewer_id, t))
        
        # 连接鼠标样式变化信号（用于同步鼠标样式）
        viewer.cursor_changed.connect(lambda c: self.sync_other_cursor(viewer_id, c))
        
        # 连接滚动条位置变化信号（用于同步拖动）
        viewer.scroll_changed.connect(lambda h, v: self.sync_other_scroll(viewer_id, h, v))
        
        image_layout.addWidget(viewer)
        
        # Colorbar组件
        colorbar = ColorbarWidget()
        setattr(self, f'colorbar_{viewer_id}', colorbar)
        image_layout.addWidget(colorbar)
        
        main_layout.addLayout(image_layout)
        
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
        
        main_layout.addLayout(control_layout)
        
        # 连接鼠标移动事件，显示像素值
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
        # 跳过分隔符项（分隔符以"━"开头）
        if colormap_name.startswith('━'):
            return
        
        if hasattr(self, 'image_viewer_1'):
            self.image_viewer_1.set_colormap(colormap_name)
        if hasattr(self, 'image_viewer_2'):
            self.image_viewer_2.set_colormap(colormap_name)
        
        # 更新colorbar
        reversed = self.render_settings.reverse_check.isChecked() if hasattr(self, 'render_settings') else False
        if hasattr(self, 'colorbar_1'):
            self.colorbar_1.set_colormap(colormap_name, reversed)
        if hasattr(self, 'colorbar_2'):
            self.colorbar_2.set_colormap(colormap_name, reversed)
    
    def on_render_settings_changed(self):
        """渲染设置变化时更新两个窗口"""
        settings = self.render_settings.get_all_settings()
        if hasattr(self, 'image_viewer_1'):
            self.image_viewer_1.set_render_settings(settings)
        if hasattr(self, 'image_viewer_2'):
            self.image_viewer_2.set_render_settings(settings)
        
        # 更新colorbar的数值范围
        if hasattr(self, 'colorbar_1'):
            self._refresh_colorbar_range(1)
        if hasattr(self, 'colorbar_2'):
            self._refresh_colorbar_range(2)

        self._sync_selected_pixel_markers()
    
    def on_suggest_colormap(self, colormap_name):
        """接收建议的colormap并切换"""
        self.colormap_combo.setCurrentText(colormap_name)
    
    def _update_image_stats_to_render_settings(self):
        """从当前图像计算统计信息并更新到渲染设置"""
        # 使用第一个窗口的缓存图像
        if self._cached_image_1 is not None:
            arr = self._cached_image_1
            nodata_value = self._get_effective_nodata_for_index(self.current_image_index_1)
            # 创建有效掩码
            valid_mask = np.isfinite(arr)
            if nodata_value is not None:
                if np.isnan(nodata_value):
                    valid_mask = valid_mask & ~np.isnan(arr)
                else:
                    valid_mask = valid_mask & (arr != nodata_value)
            
            if np.any(valid_mask):
                valid_data = arr[valid_mask]
                min_val = float(np.min(valid_data))
                max_val = float(np.max(valid_data))
                self.render_settings.set_image_stats(min_val, max_val)

    def _clear_selected_pixel_state(self):
        """清除当前选点及其标记。"""
        self.selected_pixel = None
        self.selected_geo = None
        self.selected_viewer_id = None
        if hasattr(self, 'pixel_info_label'):
            self.pixel_info_label.setText("请点击图像选择像素")
        self._sync_selected_pixel_markers()

    def _sync_selected_pixel_markers(self):
        """在两个图像窗口同步选点标记。"""
        for viewer_id in (1, 2):
            viewer = getattr(self, f'image_viewer_{viewer_id}', None)
            if viewer is None:
                continue
            if self.selected_pixel is None:
                viewer.clear_selected_pixel()
            elif self.data_source_type == 'folder' and self.folder_sync_mode == 'unavailable':
                if viewer_id == self.selected_viewer_id:
                    viewer.set_selected_pixel(*self.selected_pixel)
                else:
                    viewer.clear_selected_pixel()
            elif self._uses_geo_sync() and self.selected_geo is not None:
                current_index = getattr(self, f'current_image_index_{viewer_id}', 0)
                pixel = self._map_lonlat_to_pixel(current_index, *self.selected_geo)
                if pixel[0] is not None and self._is_pixel_in_bounds(current_index, *pixel):
                    viewer.set_selected_pixel(*pixel)
                else:
                    viewer.clear_selected_pixel()
            else:
                viewer.set_selected_pixel(*self.selected_pixel)

    def _get_colorbar_data_range(self, viewer_id):
        """获取指定窗口当前图像的有效数据范围。"""
        data = getattr(self, f'_cached_image_{viewer_id}', None)
        if data is None:
            return None

        settings = self.render_settings.get_all_settings()
        display_mode = settings.get('display_mode', '灰度')

        if data.ndim == 3:
            if display_mode == 'RGB':
                return None
            band = min(settings.get('gray_band', 1), data.shape[2]) - 1
            data = data[:, :, band]

        nodata_value = self._get_effective_nodata_for_index(getattr(self, f'current_image_index_{viewer_id}'))
        valid_mask = np.isfinite(data)
        if nodata_value is not None:
            if np.isnan(nodata_value):
                valid_mask = valid_mask & ~np.isnan(data)
            else:
                valid_mask = valid_mask & (data != nodata_value)

        if not np.any(valid_mask):
            return None

        valid_data = data[valid_mask]
        return float(np.min(valid_data)), float(np.max(valid_data))

    def _refresh_colorbar_range(self, viewer_id):
        """刷新指定窗口 colorbar 的显示范围。"""
        colorbar = getattr(self, f'colorbar_{viewer_id}', None)
        if colorbar is None:
            return

        settings = self.render_settings.get_all_settings()
        data_range = self._get_colorbar_data_range(viewer_id)
        if settings.get('auto_range', True) and data_range is not None:
            vmin, vmax = data_range
        else:
            vmin = settings['value_min']
            vmax = settings['value_max']

        colorbar.set_range(vmin, vmax)
        colorbar.set_colormap(self.colormap_combo.currentText(), settings['colormap_reversed'])

    def _clear_cached_images(self):
        """清空两个窗口的图像缓存。"""
        self._cached_image_1 = None
        self._cached_index_1 = -1
        self._cached_original_size_1 = None
        self._cached_image_2 = None
        self._cached_index_2 = -1
        self._cached_original_size_2 = None

    def _is_unlimited_display_enabled(self) -> bool:
        return not self.max_display_size or self.max_display_size <= 0

    def _display_limit_status_text(self) -> str:
        if self._is_unlimited_display_enabled():
            return "显示全部像素"
        return f"上限: {self.max_display_size}px"

    def _update_display_limit_button(self):
        if self._is_unlimited_display_enabled():
            self.toggle_display_limit_btn.setText("恢复显示上限")
            base_limit = self._configured_max_display_size if self._configured_max_display_size > 0 else 2048
            self.toggle_display_limit_btn.setToolTip(f"恢复为当前设置的显示上限（{base_limit}px）")
        else:
            self.toggle_display_limit_btn.setText("取消显示上限")
            self.toggle_display_limit_btn.setToolTip("重新加载当前影像并显示全部像素")

    def toggle_display_limit_override(self):
        """切换是否取消显示上限。"""
        if self._is_unlimited_display_enabled():
            self.max_display_size = self._configured_max_display_size if self._configured_max_display_size > 0 else 2048
        else:
            self.max_display_size = 0
        self._update_display_limit_button()

        if self.image_count > 0:
            self._clear_cached_images()
            self.show_image(1, reset_view=True)
            self.show_image(2, reset_view=True)

    def _get_image_metadata(self, index) -> Optional[dict]:
        """获取指定索引影像的元数据。"""
        if 0 <= index < len(self.image_metadata):
            return self.image_metadata[index]
        return None

    def _uses_geo_sync(self) -> bool:
        """文件夹模式下是否启用地理坐标同步。"""
        return self.data_source_type == 'folder' and self.folder_sync_mode == 'geo'

    def _get_image_dimensions(self, index) -> Tuple[Optional[int], Optional[int]]:
        """获取指定影像的原始尺寸 (width, height)。"""
        if self.data_source_type == 'folder':
            metadata = self._get_image_metadata(index)
            if metadata is not None:
                return metadata.get('width'), metadata.get('height')
        elif self.data_source_type == 'gamma':
            return self.gamma_width, self.gamma_height
        elif self.image_shape is not None:
            if len(self.image_shape) >= 2:
                return self.image_shape[1], self.image_shape[0]
        return None, None

    def _build_folder_image_metadata(self, file_path) -> Optional[dict]:
        """构建单张文件夹影像的元数据，不读取整幅数据。"""
        ext = os.path.splitext(file_path)[1].lower()

        if ext in ['.tif', '.tiff', '.grd']:
            size_info, band_count, nodata_value = get_tiff_info(file_path)
            geotransform, projection = get_geotransform(file_path)
        else:
            size_info, band_count = get_image_info(file_path)
            nodata_value = None
            geotransform, projection = None, None

        if size_info is None:
            return None

        width, height = size_info
        inv_geotransform = invert_geotransform(geotransform) if geotransform is not None else None
        to_wgs84_transform = None
        from_wgs84_transform = None
        bounds_wgs84 = None

        if geotransform is not None and inv_geotransform is not None:
            to_wgs84_transform = build_coordinate_transform(
                source_projection=projection,
                target_epsg=4326,
            )
            from_wgs84_transform = build_coordinate_transform(
                source_epsg=4326,
                target_projection=projection,
            )
            bounds_wgs84 = get_raster_bounds_wgs84(
                width,
                height,
                geotransform,
                projection,
                to_wgs84_transform=to_wgs84_transform,
            )

        scene_rect = None
        if bounds_wgs84 is not None:
            min_lon, min_lat, max_lon, max_lat = bounds_wgs84
            scene_rect = (
                min_lon,
                -max_lat,
                max_lon - min_lon,
                max_lat - min_lat,
            )

        return {
            'path': file_path,
            'width': width,
            'height': height,
            'band_count': band_count,
            'nodata_value': nodata_value,
            'geotransform': geotransform,
            'projection': projection,
            'inv_geotransform': inv_geotransform,
            'to_wgs84_transform': to_wgs84_transform,
            'from_wgs84_transform': from_wgs84_transform,
            'bounds_wgs84': bounds_wgs84,
            'scene_rect': scene_rect,
            'has_geo': geotransform is not None and inv_geotransform is not None,
        }

    def _get_effective_nodata_for_index(self, index):
        """获取指定影像当前生效的Nodata值。"""
        if self._nodata_user_locked:
            return self.nodata_value

        if self.data_source_type == 'folder':
            metadata = self._get_image_metadata(index)
            if metadata is not None:
                return metadata.get('nodata_value')

        return self.nodata_value

    def _is_nodata_value(self, value, nodata_value) -> bool:
        """判断值是否为Nodata。"""
        if nodata_value is None or value is None:
            return False

        try:
            if isinstance(value, np.ndarray):
                if np.isnan(nodata_value):
                    return bool(np.all(np.isnan(value)))
                return bool(np.all(value == nodata_value))

            if np.isnan(nodata_value):
                return bool(np.isnan(value))
            return bool(value == nodata_value)
        except Exception:
            return False

    def _mask_nodata_value(self, value, nodata_value):
        """将Nodata值转为NaN，便于绘图。"""
        if value is None:
            return np.nan

        if isinstance(value, np.ndarray):
            masked = value.astype(np.float32, copy=True)
            if nodata_value is None:
                return masked

            if np.isnan(nodata_value):
                masked[np.isnan(masked)] = np.nan
            else:
                masked[masked == nodata_value] = np.nan
            return masked

        if self._is_nodata_value(value, nodata_value):
            return np.nan
        return value

    def _get_pixel_lonlat(self, index, x, y) -> Tuple[Optional[float], Optional[float]]:
        """将指定影像中的像素坐标转换为WGS84经纬度（按像素中心）。"""
        if self.data_source_type != 'folder':
            if self.geotransform is None:
                return None, None
            return pixel_to_lonlat(x, y, self.geotransform, self.projection, use_pixel_center=True)

        metadata = self._get_image_metadata(index)
        if metadata is None or not metadata.get('has_geo'):
            return None, None

        return pixel_to_lonlat(
            x,
            y,
            metadata['geotransform'],
            metadata['projection'],
            use_pixel_center=True,
            to_wgs84_transform=metadata.get('to_wgs84_transform'),
        )

    def _map_lonlat_to_pixel(self, index, lon, lat) -> Tuple[Optional[int], Optional[int]]:
        """将WGS84经纬度反算为指定影像的最近邻像素坐标。"""
        metadata = self._get_image_metadata(index)
        if metadata is None or not metadata.get('has_geo'):
            return None, None

        return lonlat_to_pixel(
            lon,
            lat,
            metadata['geotransform'],
            metadata['projection'],
            inv_geotransform=metadata.get('inv_geotransform'),
            from_wgs84_transform=metadata.get('from_wgs84_transform'),
            nearest=True,
        )

    def _is_pixel_in_bounds(self, index, x, y) -> bool:
        """判断像素坐标是否位于影像范围内。"""
        width, height = self._get_image_dimensions(index)
        if width is None or height is None or x is None or y is None:
            return False
        return 0 <= int(x) < int(width) and 0 <= int(y) < int(height)

    def _set_colorbar_current_value(self, colorbar, value):
        """更新colorbar当前值指示。"""
        if colorbar is None:
            return

        if isinstance(value, (int, float, np.integer, np.floating)):
            if not (np.isnan(value) if isinstance(value, float) else False):
                colorbar.set_current_value(float(value))
            else:
                colorbar.set_current_value(None)
        else:
            colorbar.set_current_value(None)

    def _format_pixel_label_text(self, x, y, value, lonlat=None, nodata_value=None) -> str:
        """格式化像素值标签文本。"""
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return f"像素值: ({x}, {y}) = NaN"

        if self._is_nodata_value(value, nodata_value):
            text = f"像素值: ({x}, {y}) = NoData"
        elif isinstance(value, (int, float, np.integer, np.floating)):
            text = f"像素值: ({x}, {y}) = {value:.6g}"
        elif isinstance(value, np.ndarray):
            if value.ndim == 0:
                text = f"像素值: ({x}, {y}) = {value:.6g}"
            else:
                value_str = ", ".join(
                    "NaN" if np.isnan(v) else f"{v:.6g}" for v in value
                )
                text = f"像素值: ({x}, {y}) = [{value_str}]"
        else:
            text = f"像素值: ({x}, {y}) = {value}"

        if lonlat is not None and lonlat[0] is not None and lonlat[1] is not None:
            text += f" | 经纬度: ({lonlat[0]:.6f}, {lonlat[1]:.6f})"
        return text

    def _update_folder_scene_rect(self):
        """根据所有文件夹影像更新统一场景范围。"""
        scene_rects = [
            metadata.get('scene_rect')
            for metadata in self.image_metadata
            if metadata.get('scene_rect') is not None
        ]
        if not scene_rects:
            self.folder_scene_rect = None
            return

        min_x = min(rect[0] for rect in scene_rects)
        min_y = min(rect[1] for rect in scene_rects)
        max_x = max(rect[0] + rect[2] for rect in scene_rects)
        max_y = max(rect[1] + rect[3] for rect in scene_rects)
        self.folder_scene_rect = (min_x, min_y, max_x - min_x, max_y - min_y)

    def _configure_viewer_scene_mapping(self, viewer, index):
        """配置查看器的统一场景范围和当前图像摆放范围。"""
        if self._uses_geo_sync():
            metadata = self._get_image_metadata(index)
            viewer.set_scene_mapping(
                scene_world_rect=self.folder_scene_rect,
                image_world_rect=metadata.get('scene_rect') if metadata is not None else None,
            )
        else:
            viewer.set_scene_mapping(None, None)
    
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
            self.show_image(viewer_id, reset_view=False)
    
    def slider_changed(self, viewer_id, value):
        """滑块值改变
        
        Args:
            viewer_id: 查看器ID（1或2）
            value: 滑块值
        """
        current_index = getattr(self, f'current_image_index_{viewer_id}')
        if value != current_index:
            setattr(self, f'current_image_index_{viewer_id}', value)
            self.show_image(viewer_id, reset_view=False)
    
    def _get_image_data(self, index):
        """按需获取指定索引的图像数据（支持降采样）
        
        Args:
            index: 图像索引
            
        Returns:
            tuple: (图像数据数组, 原始尺寸(width, height))，失败返回(None, None)
        """
        if index < 0 or index >= self.image_count:
            return None, None
        
        if self.data_source_type == 'h5':
            # 使用image_io模块从h5文件按需读取
            actual_index = index + self.h5_start_index
            image_data, original_size = read_h5_timeseries_frame(
                self.h5_file_path, 
                actual_index, 
                max_size=self.max_display_size
            )
        elif self.data_source_type == 'gamma':
            # 从GAMMA二进制文件读取
            if index < len(self.image_files):
                image_data, _, original_size = self._read_gamma_image_downsampled(
                    self.image_files[index],
                    max_size=self.max_display_size
                )
            else:
                return None, None
        else:
            # 从文件夹按需读取（使用降采样读取）
            if index < len(self.image_files):
                image_data, _, original_size = self._read_image_downsampled(
                    self.image_files[index], 
                    max_size=self.max_display_size
                )
            else:
                return None, None
        
        # 如果已标记转换为dB，则应用转换
        if self._converted_to_db and image_data is not None:
            # 转换为dB
            data_copy = image_data.copy()
            nodata_value = self._get_effective_nodata_for_index(index)
            
            # 处理nodata值：如果是GAMMA数据，保持nodata（0）不变
            if self.is_gamma_timeseries or nodata_value == 0:
                # 创建mask：标记nodata像素
                nodata_mask = (data_copy == 0)
                # 将<=0且不是nodata的值设为一个很小的正数
                min_positive = np.min(data_copy[data_copy > 0]) if np.any(data_copy > 0) else 1e-10
                data_copy[(data_copy <= 0) & ~nodata_mask] = min_positive
                # 转换为dB
                db_data = np.where(nodata_mask, 0, 10 * np.log10(data_copy))
                image_data = db_data.astype(np.float32)
            else:
                # 非GAMMA数据，正常转换
                min_positive = np.min(data_copy[data_copy > 0]) if np.any(data_copy > 0) else 1e-10
                data_copy[data_copy <= 0] = min_positive
                image_data = 10 * np.log10(data_copy).astype(np.float32)
        
        return image_data, original_size
    
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
    
    def show_image(self, viewer_id, reset_view=False):
        """显示指定查看器的当前图像
        
        Args:
            viewer_id: 查看器ID（1或2）
            reset_view: 是否重置为适配全图视角
        """
        if self.image_count == 0:
            return
        
        current_index = getattr(self, f'current_image_index_{viewer_id}')
        viewer = getattr(self, f'image_viewer_{viewer_id}')
        slider = getattr(self, f'image_slider_{viewer_id}')
        index_label = getattr(self, f'image_index_label_{viewer_id}')
        info_label = getattr(self, f'image_info_label_{viewer_id}')
        
        previous_view_state = None if reset_view else viewer.capture_view_state()

        # 按需获取图像数据（包含原始尺寸）
        current_data, original_size = self._get_cached_image(viewer_id, current_index)
        
        if current_data is None:
            info_label.setText("图像加载失败")
            return
        
        self._configure_viewer_scene_mapping(viewer, current_index)

        # 更新图像查看器（传递原始尺寸用于坐标映射）
        viewer.set_image_from_array(current_data, original_size=original_size)

        current_metadata = self._get_image_metadata(current_index) if self.data_source_type == 'folder' else None
        current_nodata = self._get_effective_nodata_for_index(current_index)
        viewer.set_nodata_value(current_nodata)

        # 设置当前影像自己的地理信息到图像查看器（用于hillshade计算）
        if current_metadata is not None:
            viewer.set_geotransform(current_metadata.get('geotransform'), current_metadata.get('projection'))
        else:
            viewer.set_geotransform(self.geotransform, self.projection)
        
        # 首次加载或明确要求时才适配全图；切图时保留当前视角
        if reset_view:
            viewer.fit_in_view(delayed=True)
        else:
            viewer.restore_view_state(previous_view_state)
        self._refresh_colorbar_range(viewer_id)
        self._sync_selected_pixel_markers()
        
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
        
        info += f" | {self._display_limit_status_text()}"
        if self._converted_to_db:
            info += " | dB"
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
            supported_extensions = ['.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.grd']
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
            self, "选择h5时序数据文件", last_folder, "HDF5 Files (*.h5 *.hdf5);;All Files (*)",
            options=QFileDialog.Option.DontUseNativeDialog)
        
        if not file_path:
            return
        
        # 保存当前路径
        settings.setValue("last_h5_path", os.path.dirname(file_path))
        
        try:
            # 使用image_io模块获取h5时序元信息
            date_list, timeseries_shape, start_index = read_h5_timeseries_metadata(file_path)
            
            if date_list is None or timeseries_shape is None:
                QMessageBox.critical(self, "错误", "h5文件格式不正确，需要包含'date'和'timeseries'数据集！")
                return
            
            # 检查数据维度
            if len(timeseries_shape) != 3:
                QMessageBox.critical(self, "错误", 
                                   f"时序数据维度错误！期望3维(时间, 高度, 宽度)，得到{len(timeseries_shape)}维")
                return
            
            num_dates = timeseries_shape[0]
            height = timeseries_shape[1]
            width = timeseries_shape[2]
            
            # 保存日期列表
            self.date_list = date_list[start_index:] if start_index > 0 else date_list
            
            if start_index > 0:
                QMessageBox.information(self, "提示", "检测到第一帧数据全为0，已自动跳过")
            
            # 设置按需加载相关属性
            self.data_source_type = 'h5'
            self.h5_file_path = file_path
            self.h5_start_index = start_index
            self.image_shape = (height, width)
            self.image_count = num_dates - start_index
            self.geotransform = None
            self.projection = None
            self.image_metadata = []
            self.folder_sync_mode = 'rowcol'
            self.folder_sync_message = ""
            self.folder_scene_rect = None
            self._nodata_user_locked = False
            
            # 重置转换标志
            self._converted_to_db = False
            
            # 清空缓存
            self._clear_cached_images()
            self._clear_selected_pixel_state()
            
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
            self.show_image(1, reset_view=True)
            self.show_image(2, reset_view=True)
            
            # 启用转dB按钮
            self.to_db_btn.setEnabled(True)
            
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
            self.image_metadata = []
            self.date_list = []
            self._clear_selected_pixel_state()
            self.selected_geo = None
            self.selected_viewer_id = None
            self.nodata_value = None
            self._nodata_user_locked = False
            self.folder_sync_mode = 'rowcol'
            self.folder_sync_message = ""
            self.folder_scene_rect = None
            
            # 重置转换标志
            self._converted_to_db = False
            
            # 清空缓存
            self._clear_cached_images()
            
            # 设置数据源类型
            self.data_source_type = 'folder'
            self.h5_file_path = None
            self.h5_start_index = 0
            
            # 逐张收集元数据，不再要求尺寸一致
            unreadable_files = []
            valid_files = []
            metadata_list = []

            for file_path in file_list:
                if not os.path.exists(file_path):
                    unreadable_files.append(f"{os.path.basename(file_path)}: 文件不存在")
                    continue

                try:
                    metadata = self._build_folder_image_metadata(file_path)
                    if metadata is None:
                        unreadable_files.append(f"{os.path.basename(file_path)}: 元数据读取失败")
                        continue

                    valid_files.append(file_path)
                    metadata_list.append(metadata)
                except Exception as e:
                    unreadable_files.append(f"{os.path.basename(file_path)}: {str(e)}")
                    continue

            if unreadable_files:
                message = "以下文件读取失败，已跳过：\n" + "\n".join(unreadable_files[:10])
                if len(unreadable_files) > 10:
                    message += f"\n... 还有 {len(unreadable_files) - 10} 个文件"
                QMessageBox.warning(self, "警告", message)

            if not valid_files:
                QMessageBox.critical(self, "错误", "没有成功加载任何图像！")
                return

            first_metadata = metadata_list[0]
            self.nodata_value = first_metadata.get('nodata_value')
            band_count = first_metadata.get('band_count') or 1
            if band_count > 1:
                self.image_shape = (first_metadata['height'], first_metadata['width'], band_count)
            else:
                self.image_shape = (first_metadata['height'], first_metadata['width'])

            # 保留第一张影像的地理信息以兼容旧逻辑，实际显示改为逐张设置
            self.geotransform = first_metadata.get('geotransform')
            self.projection = first_metadata.get('projection')

            self.image_files = valid_files
            self.image_metadata = metadata_list
            self.image_count = len(valid_files)
            self._update_folder_scene_rect()

            size_set = {(m['width'], m['height']) for m in metadata_list}
            has_size_mismatch = len(size_set) > 1
            geo_metadata = [m for m in metadata_list if m.get('has_geo') and m.get('bounds_wgs84') is not None]
            all_have_geo = len(geo_metadata) == len(metadata_list) and len(metadata_list) > 0

            has_overlap = False
            for i in range(len(geo_metadata)):
                for j in range(i + 1, len(geo_metadata)):
                    if bounds_overlap(geo_metadata[i]['bounds_wgs84'], geo_metadata[j]['bounds_wgs84']):
                        has_overlap = True
                        break
                if has_overlap:
                    break

            if all_have_geo:
                self.folder_sync_mode = 'geo'
                if has_size_mismatch:
                    if has_overlap:
                        self.folder_sync_message = (
                            "检测到时序影像行列数不一致，已改为“地理坐标同步”。\n"
                            "跨影像像素值会按屏幕坐标反算到各自影像的最近邻像素，结果可能略有偏差。"
                        )
                    else:
                        self.folder_sync_message = (
                            "检测到时序影像行列数不一致，已改为“地理坐标同步”。\n"
                            "但这些影像之间缺少明显重叠范围，边缘或大部分位置可能显示“越界”。"
                        )
            else:
                if has_size_mismatch:
                    self.folder_sync_mode = 'unavailable'
                    self.folder_sync_message = (
                        "检测到时序影像行列数不一致，但部分影像缺少可用地理范围，当前无法跨影像同步。"
                    )
                else:
                    self.folder_sync_mode = 'rowcol'
                    if len(geo_metadata) != len(metadata_list) and len(geo_metadata) > 0:
                        self.folder_sync_message = (
                            "部分影像缺少地理范围，当前文件夹仍按同行列号同步。"
                        )
            
            # 尝试从文件名中提取日期
            extracted_dates = extract_dates_from_filenames(valid_files)
            if extracted_dates:
                self.date_list = extracted_dates
            else:
                self.date_list = []  # 没有提取到日期，使用空列表
            
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

            # 更新渲染设置组件的波段数
            self.render_settings.set_num_bands(band_count)
            
            # 显示第一张和第二张图像
            self.current_image_index_1 = 0
            self.current_image_index_2 = min(1, self.image_count - 1)  # 如果只有一张图像，两个窗口都显示第一张
            self.show_image(1, reset_view=True)
            self.show_image(2, reset_view=True)
            
            # 设置默认colormap为gray（非h5文件）
            self.colormap_combo.setCurrentText('gray')
            
            # 更新渲染设置的最大最小值（从第一张图像计算）
            self._update_image_stats_to_render_settings()
            
            # 启用转dB按钮
            self.to_db_btn.setEnabled(True)

            if self.folder_sync_message:
                if self.folder_sync_mode == 'unavailable':
                    QMessageBox.warning(self, "提示", self.folder_sync_message)
                else:
                    QMessageBox.information(self, "提示", self.folder_sync_message)
            
            # 应用排序
            self.sort_images()
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载图像失败: {str(e)}")
            traceback.print_exc()
    
    def open_gamma_folder(self):
        """打开GAMMA二进制文件时序文件夹"""
        settings = get_settings()
        last_folder = settings.value("last_gamma_folder_path", "")
        last_format = settings.value("last_gamma_format", "float32")
        
        folder = QFileDialog.getExistingDirectory(self, "选择GAMMA时序文件夹", last_folder)
        if not folder:
            return
        
        settings.setValue("last_gamma_folder_path", folder)
        
        try:
            # 查找目录中的二进制文件（排除.par文件）
            # 使用文件大小>10M且大小一致来判断
            MIN_FILE_SIZE = 10 * 1024 * 1024  # 10MB
            
            all_files_with_size = []
            for filename in os.listdir(folder):
                file_path = os.path.join(folder, filename)
                if os.path.isfile(file_path) and not 'par' in filename.lower():
                    file_size = os.path.getsize(file_path)
                    if file_size >= MIN_FILE_SIZE:
                        all_files_with_size.append((file_path, file_size))
            
            if not all_files_with_size:
                QMessageBox.warning(self, "警告", "文件夹中没有找到大于10MB的二进制文件！")
                return
            
            # 按文件大小分组，允许小误差（1%）
            SIZE_TOLERANCE = 0.01  # 1%误差
            size_groups = {}
            
            for file_path, file_size in all_files_with_size:
                # 查找是否有相近大小的组
                found_group = False
                for group_size in size_groups:
                    if abs(file_size - group_size) / group_size < SIZE_TOLERANCE:
                        size_groups[group_size].append(file_path)
                        found_group = True
                        break
                
                if not found_group:
                    size_groups[file_size] = [file_path]
            
            # 找到文件数最多的组
            largest_group = max(size_groups.values(), key=len)
            
            if len(largest_group) < 2:
                QMessageBox.warning(self, "警告", 
                    f"文件夹中没有找到大小一致的GAMMA二进制文件！\n"
                    f"找到{len(all_files_with_size)}个大于10MB的文件，但它们的大小不一致。")
                return
            
            all_files = largest_group
            
            # 先尝试以float32格式自动检测第一个文件的PAR文件
            first_file = all_files[0]
            auto_par_file, auto_dims = find_valid_par_for_binary(first_file, "float32")
            auto_format = "float32"
            
            # 如果没找到，再尝试cpxfloat32
            if auto_par_file is None:
                auto_par_file, auto_dims = find_valid_par_for_binary(first_file, "cpxfloat32")
                auto_format = "cpxfloat32"
            
            # 如果找到了有效的PAR文件，直接使用；否则弹出对话框
            if auto_par_file and auto_dims:
                gamma_format = auto_format
                width, height = auto_dims
                
                # 验证所有文件
                valid_files = []
                for file_path in all_files:
                    if validate_dimensions(file_path, width, height, gamma_format):
                        valid_files.append(file_path)
                
                if not valid_files:
                    QMessageBox.warning(self, "警告", "没有找到有效的GAMMA二进制文件！")
                    return
                    
                # 显示信息，但不需要用户确认
                info_msg = (f"自动检测到PAR文件: {os.path.basename(auto_par_file)}\n"
                           f"尺寸: {width} x {height}\n"
                           f"格式: {gamma_format}\n"
                           f"有效文件数: {len(valid_files)}")
                QMessageBox.information(self, "自动检测成功", info_msg)
            else:
                # 未找到，弹出对话框让用户选择
                format_dialog = GammaTimeSeriesDialog(self, last_format, all_files)
                if format_dialog.exec() != QDialog.Accepted:
                    return
                
                gamma_format = format_dialog.get_selected_format()
                valid_files = format_dialog.get_valid_files()
                width = format_dialog.get_width()
                height = format_dialog.get_height()
                
                if not valid_files:
                    QMessageBox.warning(self, "警告", "没有找到有效的GAMMA二进制文件！")
                    return
            
            # 保存设置
            settings.setValue("last_gamma_format", gamma_format)
            
            # 设置GAMMA相关属性
            self.is_gamma_timeseries = True
            self.gamma_format = gamma_format
            self.gamma_width = width
            self.gamma_height = height
            
            # 设置数据源类型
            self.data_source_type = 'gamma'
            self.h5_file_path = None
            self.h5_start_index = 0
            self.image_shape = (height, width)
            self.geotransform = None
            self.projection = None
            self.image_metadata = []
            self.folder_sync_mode = 'rowcol'
            self.folder_sync_message = ""
            self.folder_scene_rect = None
            self._nodata_user_locked = False
            
            # 重置转换标志
            self._converted_to_db = False
            
            # 清空缓存
            self._clear_cached_images()
            self._clear_selected_pixel_state()
            self.date_list = []
            
            # 按文件名排序
            valid_files.sort()
            self.image_files = valid_files
            self.image_count = len(valid_files)
            
            # 尝试从文件名中提取日期
            extracted_dates = extract_dates_from_filenames(valid_files)
            if extracted_dates:
                self.date_list = extracted_dates
            else:
                self.date_list = []  # 没有提取到日期，使用空列表
            
            # 设置默认Nodata值
            self.nodata_value = 0
            self.image_viewer_1.set_nodata_value(0)
            self.image_viewer_2.set_nodata_value(0)
            
            # 更新UI
            self.image_count_label.setText(f"已加载 {self.image_count} 张GAMMA时序影像")
            
            # 更新两个窗口的控件
            for viewer_id in [1, 2]:
                slider = getattr(self, f'image_slider_{viewer_id}')
                prev_btn = getattr(self, f'prev_btn_{viewer_id}')
                next_btn = getattr(self, f'next_btn_{viewer_id}')
                
                slider.setMaximum(self.image_count - 1)
                slider.setEnabled(True)
                prev_btn.setEnabled(True)
                next_btn.setEnabled(True)
            
            # 设置默认colormap
            is_complex = gamma_format.startswith('cpx')
            if is_complex:
                self.colormap_combo.setCurrentText('hsv')
            else:
                self.colormap_combo.setCurrentText('gray')
            
            # 显示第一张和第二张图像
            self.current_image_index_1 = 0
            self.current_image_index_2 = min(1, self.image_count - 1)
            self.show_image(1, reset_view=True)
            self.show_image(2, reset_view=True)
            
            # 更新渲染设置的最大最小值
            self._update_image_stats_to_render_settings()
            
            # 启用转dB按钮
            self.to_db_btn.setEnabled(True)
            
            QMessageBox.information(self, "成功", 
                f"成功加载GAMMA时序数据！\n" +
                f"文件数量: {self.image_count}\n" +
                f"尺寸: {width} x {height}\n" +
                f"格式: {gamma_format}")
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开GAMMA文件夹失败: {str(e)}")
            traceback.print_exc()
    
    def _read_gamma_image_downsampled(self, file_path, max_size=2048):
        """
        读取GAMMA二进制文件并降采样
        
        Returns:
            tuple: (图像数据, nodata值, 原始尺寸) 或 (None, None, None)
        """
        if not self.is_gamma_timeseries:
            return None, None, None
        
        try:
            data, downsample_factor = read_gamma_downsampled(
                file_path, 
                self.gamma_width, 
                self.gamma_height, 
                self.gamma_format,
                max_size
            )
            
            # 处理复数数据
            if self.gamma_format.startswith('cpx'):
                data = complex_to_phase(data)
            
            original_size = (self.gamma_width, self.gamma_height) if downsample_factor > 1 else None
            
            return data.astype(np.float32), 0, original_size
            
        except Exception as e:
            traceback.print_exc()
            return None, None, None
    
    def _read_gamma_pixel_value(self, file_path, x, y):
        """
        从GAMMA二进制文件读取单个像素值
        """
        try:
            value = read_gamma_pixel(
                file_path, x, y,
                self.gamma_width, self.gamma_height,
                self.gamma_format
            )
            
            # 处理复数数据
            if self.gamma_format.startswith('cpx'):
                value = np.angle(value)
            
            return value
        except Exception as e:
            traceback.print_exc()
            return None

    def _read_image(self, file_path):
        """
        读取图像文件，支持普通图像和TIFF
        
        Returns:
            tuple: (图像数据, nodata值) 或 (None, None)
        """
        try:
            ext = os.path.splitext(file_path)[1].lower()
            
            if ext in ['.tif', '.tiff']:
                # 使用image_io读取TIFF
                data, nodata, _ = read_tiff(file_path)
                return data, nodata
            else:
                # 使用image_io读取普通图像
                data = read_image(file_path)
                if data is None:
                    return None, None
                
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
            
            if ext in ['.tif', '.tiff', '.grd']:
                # 使用image_io读取TIFF/GRD
                data, nodata, original_size, factor = read_tiff_downsampled(file_path, max_size)
                return data, nodata, original_size
            else:
                # 使用image_io读取普通图像
                data, original_size, factor = read_image_downsampled(file_path, max_size)
                if data is None:
                    return None, None, None
                
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

        if self.image_metadata and len(self.image_metadata) == len(indices):
            self.image_metadata = [self.image_metadata[i] for i in indices]
        
        # 如果有日期列表，也需要重新排列
        if self.date_list and len(self.date_list) == len(indices):
            self.date_list = [self.date_list[i] for i in indices]
        
        # 清空缓存（因为索引顺序改变了）
        self._clear_cached_images()
        
        # 重置当前索引
        self.current_image_index_1 = 0
        self.current_image_index_2 = min(1, self.image_count - 1)  # 如果只有一张图像，两个窗口都显示第一张
        self.show_image(1, reset_view=True)
        self.show_image(2, reset_view=True)
        
        # 如果已选择像素，更新曲线
        if self.selected_pixel:
            self.update_time_series_plot()
    
    def on_pixel_clicked(self, viewer_id, x, y):
        """像素点击事件处理"""
        self.selected_viewer_id = viewer_id
        self.selected_pixel = (x, y)

        current_index = getattr(self, f'current_image_index_{viewer_id}', 0)
        if self._uses_geo_sync():
            lon, lat = self._get_pixel_lonlat(current_index, x, y)
            if lon is not None and lat is not None:
                self.selected_geo = (lon, lat)
                self.pixel_info_label.setText(
                    f"选中像素: ({x}, {y}) | 地理坐标: ({lon:.6f}, {lat:.6f})"
                )
            else:
                self.selected_geo = None
                self.pixel_info_label.setText(f"选中像素: ({x}, {y})")
        elif self.data_source_type == 'folder' and self.folder_sync_mode == 'unavailable':
            self.selected_geo = None
            self.pixel_info_label.setText(
                f"选中像素: ({x}, {y}) | 当前文件夹缺少统一地理范围，无法跨影像同步"
            )
        else:
            self.selected_geo = None
            self.pixel_info_label.setText(f"选中像素: ({x}, {y})")

        self._sync_selected_pixel_markers()

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
        if not self._is_pixel_in_bounds(index, x, y):
            return np.nan

        if self.data_source_type == 'h5':
            # 使用image_io模块从h5文件按需读取单个像素（获取整个时序）
            try:
                actual_index = index + self.h5_start_index
                # 直接读取单个像素的所有时序值
                all_values = read_h5_timeseries_pixel(
                    self.h5_file_path, x, y, self.h5_start_index
                )
                if all_values is not None and index < len(all_values):
                    return all_values[index]
                return np.nan
            except Exception as e:
                print(f"读取h5像素值失败 (索引 {index}, 位置 ({x}, {y})): {e}")
                return np.nan
        elif self.data_source_type == 'gamma':
            # 从GAMMA文件读取
            if index < len(self.image_files):
                return self._read_gamma_pixel_value(self.image_files[index], x, y)
            return np.nan
        else:
            # 从文件按需读取像素值
            if index < len(self.image_files):
                try:
                    file_path = self.image_files[index]
                    value = read_any_image_pixel(file_path, x, y)
                    if value is not None:
                        return value
                    return np.nan
                except Exception as e:
                    print(f"读取像素值失败 (文件 {file_path}, 位置 ({x}, {y})): {e}")
                    return np.nan
            return np.nan
    
    def _get_all_pixel_values_at(self, x, y, lonlat=None):
        """批量获取所有时序图像在指定位置的像素值
        
        Args:
            x: X坐标
            y: Y坐标
            
        Returns:
            像素值列表
        """
        if self.data_source_type == 'folder':
            values = []
            for i in range(self.image_count):
                query_x, query_y = x, y
                if self._uses_geo_sync() and lonlat is not None:
                    query_x, query_y = self._map_lonlat_to_pixel(i, *lonlat)
                if query_x is None or query_y is None or not self._is_pixel_in_bounds(i, query_x, query_y):
                    values.append(np.nan)
                    continue

                val = self._get_pixel_value_at(i, query_x, query_y)
                nodata_value = self._get_effective_nodata_for_index(i)
                values.append(self._mask_nodata_value(val, nodata_value))
        elif self.data_source_type == 'h5':
            # 从h5文件批量读取整列像素（更高效）
            try:
                # 直接获取所有时序值（read_h5_timeseries_pixel返回完整时序）
                all_values = read_h5_timeseries_pixel(
                    self.h5_file_path,
                    x, y,
                    start_index=self.h5_start_index
                )
                if all_values is not None:
                    values = list(all_values[:self.image_count])
                else:
                    values = [np.nan] * self.image_count
            except Exception as e:
                print(f"批量读取h5像素值失败 (位置 ({x}, {y})): {e}")
                values = [np.nan] * self.image_count
        elif self.data_source_type == 'gamma':
            # 从GAMMA二进制文件逐个读取
            values = []
            for i in range(self.image_count):
                if i < len(self.image_files):
                    val = self._read_gamma_pixel_value(self.image_files[i], x, y)
                    values.append(val if val is not None else np.nan)
                else:
                    values.append(np.nan)
        else:
            values = [np.nan] * self.image_count

        # 如果已转换为dB，对像素值也应用dB转换
        if self._converted_to_db:
            converted_values = []
            for i, val in enumerate(values):
                nodata_value = self._get_effective_nodata_for_index(i)
                if isinstance(val, np.ndarray):
                    converted_val = val.copy()
                    for band_idx in range(len(converted_val)):
                        v = converted_val[band_idx]
                        if np.isnan(v):
                            continue
                        if self.is_gamma_timeseries or nodata_value == 0:
                            if v == 0:
                                converted_val[band_idx] = 0
                            elif v > 0:
                                converted_val[band_idx] = 10 * np.log10(v)
                            else:
                                converted_val[band_idx] = 10 * np.log10(1e-10)
                        else:
                            if v > 0:
                                converted_val[band_idx] = 10 * np.log10(v)
                            else:
                                converted_val[band_idx] = 10 * np.log10(1e-10)
                    converted_values.append(converted_val)
                else:
                    if np.isnan(val):
                        converted_values.append(val)
                    elif self.is_gamma_timeseries or nodata_value == 0:
                        if val == 0:
                            converted_values.append(0)
                        elif val > 0:
                            converted_values.append(10 * np.log10(val))
                        else:
                            converted_values.append(10 * np.log10(1e-10))
                    else:
                        if val > 0:
                            converted_values.append(10 * np.log10(val))
                        else:
                            converted_values.append(10 * np.log10(1e-10))
            return converted_values

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

        if self.data_source_type == 'folder' and self.folder_sync_mode == 'unavailable':
            ax = self.figure.add_subplot(111)
            ax.text(
                0.5, 0.5,
                "当前文件夹影像缺少统一地理范围\n不同行列数之间无法同步时序像素值",
                ha='center', va='center', fontsize=11
            )
            ax.set_axis_off()
            self.figure.tight_layout()
            self.canvas.draw()
            return
        
        # 提取时序数据
        time_indices = list(range(self.image_count))

        if self._uses_geo_sync() and self.selected_geo is not None:
            all_values = self._get_all_pixel_values_at(x, y, lonlat=self.selected_geo)
            title_text = (
                f'地理坐标 ({self.selected_geo[0]:.6f}, {self.selected_geo[1]:.6f})'
                f' 对应像素的时序曲线'
            )
        else:
            all_values = self._get_all_pixel_values_at(x, y)
            title_text = f'像素 ({x}, {y}) 的时序曲线'
        
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
                ax.set_xticklabels(tick_labels)
            else:
                ax.set_xlabel('图像索引')
            
            ax.set_ylabel('像素值')
            ax.set_title(title_text)
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
                ax.set_xticklabels(tick_labels)
            else:
                ax.set_xlabel('图像索引')
            
            ax.set_ylabel('像素值')
            ax.set_title(title_text)
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
                self.nodata_value = None
                self._nodata_user_locked = True
                self.image_viewer_1.set_nodata_value(None)
                self.image_viewer_2.set_nodata_value(None)
                self._refresh_colorbar_range(1)
                self._refresh_colorbar_range(2)
                QMessageBox.information(self, "成功", "已取消Nodata值设置")
            else:
                try:
                    # 支持nan值
                    if text.lower().strip() == "nan":
                        nodata_value = np.nan
                    else:
                        nodata_value = float(text)
                    
                    self.nodata_value = nodata_value
                    self._nodata_user_locked = True
                    self.image_viewer_1.set_nodata_value(nodata_value)
                    self.image_viewer_2.set_nodata_value(nodata_value)
                    self._refresh_colorbar_range(1)
                    self._refresh_colorbar_range(2)
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
        current_index = getattr(self, f'current_image_index_{viewer_id}', 0)
        current_nodata = self._get_effective_nodata_for_index(current_index)
        current_lonlat = self._get_pixel_lonlat(current_index, x, y)
        if self._uses_geo_sync():
            current_value = self._get_pixel_value_at(current_index, x, y)
        else:
            current_value = value if value is not None else self._get_pixel_value_at(current_index, x, y)

        # 更新当前窗口的像素值标签
        pixel_value_label = getattr(self, f'pixel_value_label_{viewer_id}')
        if current_value is not None:
            pixel_value_label.setText(
                self._format_pixel_label_text(
                    x, y, current_value, lonlat=current_lonlat, nodata_value=current_nodata
                )
            )
        else:
            pixel_value_label.setText("像素值: -")

        # 更新colorbar当前值指示
        colorbar = getattr(self, f'colorbar_{viewer_id}', None)
        self._set_colorbar_current_value(colorbar, current_value)

        # 同时更新另一个窗口的像素值
        other_viewer_id = 2 if viewer_id == 1 else 1
        other_pixel_label = getattr(self, f'pixel_value_label_{other_viewer_id}')
        other_colorbar = getattr(self, f'colorbar_{other_viewer_id}', None)

        other_index = getattr(self, f'current_image_index_{other_viewer_id}', 0)
        other_nodata = self._get_effective_nodata_for_index(other_index)

        if self.data_source_type == 'folder' and self.folder_sync_mode == 'unavailable':
            other_pixel_label.setText("像素值: 无法同步")
            self._set_colorbar_current_value(other_colorbar, None)
            return

        if self._uses_geo_sync():
            if current_lonlat[0] is None or current_lonlat[1] is None:
                other_pixel_label.setText("像素值: 无法同步")
                self._set_colorbar_current_value(other_colorbar, None)
                return

            other_x, other_y = self._map_lonlat_to_pixel(other_index, *current_lonlat)
            if other_x is None or other_y is None:
                other_pixel_label.setText("像素值: 无法同步")
                self._set_colorbar_current_value(other_colorbar, None)
            elif not self._is_pixel_in_bounds(other_index, other_x, other_y):
                other_pixel_label.setText("像素值: 越界")
                self._set_colorbar_current_value(other_colorbar, None)
            else:
                other_value = self._get_pixel_value_at(other_index, other_x, other_y)
                other_pixel_label.setText(
                    self._format_pixel_label_text(
                        other_x,
                        other_y,
                        other_value,
                        lonlat=current_lonlat,
                        nodata_value=other_nodata,
                    )
                )
                self._set_colorbar_current_value(other_colorbar, other_value)
        else:
            other_cached_image = getattr(self, f'_cached_image_{other_viewer_id}', None)
            other_original_size = getattr(self, f'_cached_original_size_{other_viewer_id}', None)

            if self._is_pixel_in_bounds(other_index, x, y):
                if other_cached_image is not None:
                    if other_original_size:
                        orig_w, _ = other_original_size
                        downsample_factor = orig_w / other_cached_image.shape[1]
                    else:
                        downsample_factor = 1.0
                    display_x = int(x / downsample_factor)
                    display_y = int(y / downsample_factor)
                    if 0 <= display_x < other_cached_image.shape[1] and 0 <= display_y < other_cached_image.shape[0]:
                        other_value = other_cached_image[display_y, display_x]
                    else:
                        other_value = self._get_pixel_value_at(other_index, x, y)
                else:
                    other_value = self._get_pixel_value_at(other_index, x, y)
                other_lonlat = self._get_pixel_lonlat(other_index, x, y)
                other_pixel_label.setText(
                    self._format_pixel_label_text(
                        x, y, other_value, lonlat=other_lonlat, nodata_value=other_nodata
                    )
                )
                self._set_colorbar_current_value(other_colorbar, other_value)
            else:
                other_pixel_label.setText("像素值: 越界")
                self._set_colorbar_current_value(other_colorbar, None)
    
    def convert_to_db(self):
        """将显示的图像转换为dB (10*log10)"""
        if self.image_count == 0:
            return
        
        try:
            # 确认操作
            reply = QMessageBox.question(
                self, "确认", 
                "将所有图像转换为dB (10*log10)？\n注意：此操作会修改缓存的图像数据。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply != QMessageBox.Yes:
                return
            
            # 清空缓存，强制重新加载
            self._clear_cached_images()
            
            # 标记为已转换为dB
            self._converted_to_db = True
            
            # 如果是GAMMA时序数据，保持nodata为0
            if self.is_gamma_timeseries:
                self.nodata_value = 0
                self.image_viewer_1.set_nodata_value(0)
                self.image_viewer_2.set_nodata_value(0)
            
            # 重新显示当前图像
            self.show_image(1)
            self.show_image(2)
            
            # 如果有选中的像素，更新曲线
            if self.selected_pixel:
                self.update_time_series_plot()
            
            QMessageBox.information(self, "成功", "已转换为dB (10*log10)")
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"转换为dB失败: {str(e)}")
            traceback.print_exc()

