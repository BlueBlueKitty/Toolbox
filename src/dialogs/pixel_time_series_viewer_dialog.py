'''
Author: Yibo Yuan 2633669459@qq.com
Description: 像素时序查看器对话框

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
import re
import copy
import json
import numpy as np
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional, Tuple
from shiboken6 import isValid
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                                QFileDialog, QLabel, QSlider, QComboBox, QMessageBox,
                                QSplitter, QGroupBox, QGridLayout, QCheckBox, QFormLayout,
                                QDialogButtonBox, QInputDialog, QFrame, QWidget,
                                QApplication, QSizePolicy, QToolButton)
from PySide6.QtCore import Qt, QSettings, QTimer, QSize
from PySide6.QtGui import QFontDatabase, QFont, QPainter, QPixmap, QIcon, QColor, QTransform
from PySide6.QtCore import QRectF

# 导入共享的GAMMA对话框
from src.dialogs.gamma_dialogs import GammaTimeSeriesDialog
from src.utils.window_geometry import expand_window_width_safely, fit_window_to_screen

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

from src.rendering.canvas import LayeredRasterCanvas
from src.rendering.models import ImageSourceMetadata
from src.rendering.style_auto_selector import DefaultRenderStyleFactory
from src.rendering.raster_source_utils import open_raster_source
from src.rendering.styles import style_to_legacy_config
from src.rendering.sync import SyncOptions
from src.widgets import (
    ColorbarWidget,
    MultiCanvasWorkspace,
    OperationProgressWidget,
)
from src.utils.gamma_file_process import (
    GAMMA_FORMATS,
    read_gamma_pixel,
    find_valid_par_for_binary,
    validate_dimensions,
    is_gamma_binary_file,
)
from src.utils.image_io import (
    read_tiff,
    get_tiff_info,
    read_image,
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
from src.utils.display_pyramid import (
    DEFAULT_PYRAMID_THRESHOLD_MB,
    read_gamma_pyramid_display,
    read_gdal_pyramid_display,
    read_h5_timeseries_frame_pyramid_display,
    read_standard_pyramid_display,
    write_derived_raster_cache,
)
from src.rendering.sources import GdalRasterSource, GammaVrtRasterSource, H5TimeSeriesRasterSource
from src.rendering.config import default_raster_render_config


@dataclass
class TimeSeriesLayerEntry:
    source_key: str
    display_name: str
    date_label: str
    source_kind: str
    source_path: str
    metadata: dict[str, Any]
    render_config: Any = None
    series_group_id: str = "default"
    frame_index: Optional[int] = None
    source: Any = None


class PixelTimeSeriesViewerDialog(QDialog):
    """像素时序查看器对话框"""
    
    def __init__(self, parent=None, pyramid_threshold_mb=DEFAULT_PYRAMID_THRESHOLD_MB):
        super().__init__(parent)
        
        # 显示金字塔配置
        self.pyramid_threshold_mb = pyramid_threshold_mb
        
        self.setWindowTitle("像素时序查看器")
        self.resize(1600, 900)
        
        # 存储时序图像数据（按需加载模式）
        self.time_series_layers: list[TimeSeriesLayerEntry] = []
        self.image_files = []  # 文件路径列表
        self.date_list = []  # 日期列表（用于h5时序数据）
        self.current_image_index_1 = 0  # 窗口1当前显示的图像索引
        self.current_image_index_2 = 0  # 窗口2当前显示的图像索引
        self.selected_pixel = None  # 选中的像素坐标 (x, y)
        self.selected_geo = None  # 选中的WGS84地理坐标 (lon, lat)
        self.selected_viewer_id = None  # 最近一次选点来自哪个窗口
        self._active_render_viewer_id = 1
        self._viewer_render_settings = {1: None, 2: None}
        self._viewer_colormaps = {1: "gray", 2: "gray"}
        self._viewer_has_image = {1: False, 2: False}
        self._h5_pixel_series_cache: dict[tuple[int, int], np.ndarray] = {}
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
        self._cached_source_1 = None
        self._cached_image_2 = None  # 窗口2缓存的图像数据
        self._cached_index_2 = -1  # 窗口2缓存的图像索引
        self._cached_original_size_2 = None  # 窗口2缓存的原始尺寸
        self._cached_source_2 = None
        
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
        self.image_metadata = []  # 当前时序图层的元数据镜像
        self.shared_scene_rect = None  # 统一场景范围（WGS84场景坐标）
        
        # dB转换标志
        self._converted_to_db = False  # 是否已转换为dB
        self._loading_new_series = False
        self._material_icon_family = self._load_material_icon_font()
        self._theme_mode = "dark"
        self._last_jump_row_1b = 1
        self._last_jump_col_1b = 1
        
        # 创建UI
        self._create_ui()
        self._set_series_status_text("未加载图像")
        base_settings = self._default_render_settings_for_band_count(1)
        self._viewer_render_settings[1] = copy.deepcopy(base_settings)
        self._viewer_render_settings[2] = copy.deepcopy(base_settings)
        self._apply_render_state_to_controls(1)
        self._loading_title_text = self.windowTitle()
        self._render_update_timer = QTimer(self)
        self._render_update_timer.setSingleShot(True)
        self._render_update_timer.timeout.connect(self._apply_render_settings_update)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

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
        self.db_toggle_check = QCheckBox("转dB")
        self.db_toggle_check.toggled.connect(self._on_db_toggled)
        control_layout1.addWidget(self.db_toggle_check)
        self.jump_pixel_btn = QPushButton("跳转像素...")
        self.jump_pixel_btn.setEnabled(False)
        self.jump_pixel_btn.clicked.connect(self.jump_to_pixel)
        control_layout1.addWidget(self.jump_pixel_btn)
        self.toggle_window_layout_btn = QToolButton()
        self.toggle_window_layout_btn.setToolTip("单窗口/双窗口切换")
        self.toggle_window_layout_btn.setAutoRaise(True)
        self.toggle_window_layout_btn.setIcon(self._material_icon("splitscreen", rotation_angle=90))
        self.toggle_window_layout_btn.setIconSize(QSize(20, 20))
        self.toggle_window_layout_btn.clicked.connect(self._toggle_window_layout)
        control_layout1.addWidget(self.toggle_window_layout_btn)
        
        control_layout1.addStretch()
        
        self.toggle_sidebar_btn = QToolButton()
        self.toggle_sidebar_btn.setToolTip("渲染控制侧边栏")
        self.toggle_sidebar_btn.setAutoRaise(True)
        self._update_sidebar_toggle_icon()
        self.toggle_sidebar_btn.setIconSize(QSize(20, 20))
        self.toggle_sidebar_btn.clicked.connect(self._toggle_sidebar)
        control_layout1.addWidget(self.toggle_sidebar_btn)

        main_layout.addLayout(control_layout1)
        
        outer_splitter = QSplitter(Qt.Horizontal)
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)

        # 创建主分割器：上方图像查看区，下方时序曲线
        main_splitter = QSplitter(Qt.Vertical)
        
        # ============ 上方：多窗口画布工作区（单/双窗）============
        self.workspace = MultiCanvasWorkspace(
            canvas_factory=lambda _wid: LayeredRasterCanvas(),
            window_ids=["viewer_1", "viewer_2"],
            window_labels={"viewer_1": "窗口1", "viewer_2": "窗口2"},
            panel_factory=self._create_viewer_panel_for_workspace,
            sync_options=SyncOptions(sync_pan=True, sync_zoom=True, sync_geographic_extent=True, sync_cursor=True),
            pointer_sync=True,
        )
        self.workspace.active_window_changed.connect(self._on_workspace_active_window_changed)
        main_splitter.addWidget(self.workspace)
        
        # ============ 下方：时序曲线图 ============
        curve_widget = QGroupBox("时序曲线")
        curve_layout = QVBoxLayout(curve_widget)
        curve_layout.setContentsMargins(5, 5, 5, 8)
        curve_layout.setSpacing(2)
        
        # Matplotlib图表
        self.figure = Figure(figsize=(10, 3.0), constrained_layout=True)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setMinimumHeight(220)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.toolbar = NavigationToolbar(self.canvas, self)
        
        # 连接鼠标事件用于悬浮提示
        self.canvas.mpl_connect('motion_notify_event', self._on_plot_mouse_move)
        self.canvas.mpl_connect('button_press_event', self._on_plot_click)
        
        curve_layout.addWidget(self.toolbar, 0)
        curve_layout.addWidget(self.canvas, 1)
        
        # 像素信息
        self.pixel_info_label = QLabel("请点击图像选择像素")
        curve_layout.addWidget(self.pixel_info_label)
        
        main_splitter.addWidget(curve_widget)
        
        # 设置分割器比例：上方图像区域占70%，下方曲线区域占30%
        main_splitter.setStretchFactor(0, 2)
        main_splitter.setStretchFactor(1, 1)
        
        content_layout.addWidget(main_splitter)
        outer_splitter.addWidget(content_widget)

        self.render_sidebar = self.workspace.render_sidebar
        self.render_settings = self.render_sidebar.render_settings
        self.colormap_combo = self.render_sidebar.colormap_combo
        self.render_settings.settings_changed.connect(self.on_render_settings_changed)
        self.render_settings.suggest_colormap.connect(self.on_suggest_colormap)
        self.render_sidebar.db_toggled.connect(self._on_db_toggled)
        settings = QSettings("Toolbox", "RemoteSensingToolbox")
        smooth_display = settings.value("display/smooth_display", False, type=bool)
        self.render_settings.set_smooth_display(smooth_display)
        self.colormap_combo.currentTextChanged.connect(self.on_colormap_changed)
        self.render_sidebar_binding = self.workspace.render_sidebar_binding
        self.render_sidebar_controller = self.workspace.render_sidebar_controller
        self.render_sidebar.target_changed.connect(self._on_sidebar_target_changed)
        self.viewport_sync_controller = self.workspace.viewport_sync_controller
        outer_splitter.addWidget(self.render_sidebar)
        outer_splitter.setStretchFactor(0, 4)
        outer_splitter.setStretchFactor(1, 1)
        outer_splitter.setSizes([self.width(), 0])
        self.outer_splitter = outer_splitter
        self._sidebar_visible = False
        self._sidebar_base_width = self.width()
        self.render_sidebar.setVisible(False)
        main_layout.addWidget(outer_splitter)
        self.operation_progress = OperationProgressWidget()
        main_layout.addWidget(self.operation_progress)
        self._load_workspace_preferences()

    def _create_viewer_panel_for_workspace(self, window_id: str, viewer) -> QWidget:
        if window_id == "viewer_1":
            return self._create_image_viewer_panel("窗口1", 1, viewer=viewer)
        return self._create_image_viewer_panel("窗口2", 2, viewer=viewer)

    def _create_image_viewer_panel(self, title, viewer_id, viewer=None):
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
        viewer = viewer or LayeredRasterCanvas()
        setattr(self, f'image_viewer_{viewer_id}', viewer)
        viewer.files_dropped.connect(lambda paths, vid=viewer_id: self._on_viewer_files_dropped(vid, paths))
        
        # 连接像素点击信号
        viewer.pixel_clicked.connect(lambda x, y: self.on_pixel_clicked(viewer_id, x, y))
        viewer.canvas_left_clicked.connect(lambda: self._set_active_render_viewer(viewer_id))
        
        # 连接鼠标移动信号，用于更新colorbar
        viewer.mouse_moved.connect(lambda x, y, val: self.on_viewer_mouse_moved(viewer_id, x, y, val))
        
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
        image_slider.setTracking(False)
        image_slider.valueChanged.connect(lambda v: self.slider_changed(viewer_id, v))
        image_slider.sliderMoved.connect(lambda v: self.preview_slider_position(viewer_id, v))
        setattr(self, f'image_slider_{viewer_id}', image_slider)
        
        image_index_label = QLabel("0/0")
        setattr(self, f'image_index_label_{viewer_id}', image_index_label)
        
        switch_layout.addWidget(prev_btn)
        switch_layout.addWidget(image_slider)
        switch_layout.addWidget(image_index_label)
        switch_layout.addWidget(next_btn)
        
        control_layout.addLayout(switch_layout)

        jump_layout = QHBoxLayout()
        jump_btn = QPushButton("跳转到...")
        jump_btn.setEnabled(False)
        jump_btn.clicked.connect(lambda: self.jump_to_image(viewer_id))
        setattr(self, f'jump_btn_{viewer_id}', jump_btn)

        image_select_combo = QComboBox()
        image_select_combo.setEnabled(False)
        image_select_combo.setMinimumWidth(220)
        image_select_combo.currentIndexChanged.connect(lambda idx: self.on_image_selector_changed(viewer_id, idx))
        setattr(self, f'image_select_combo_{viewer_id}', image_select_combo)

        jump_layout.addWidget(jump_btn)
        jump_layout.addWidget(image_select_combo, 1)
        control_layout.addLayout(jump_layout)
        
        # 图像信息标签（迁移到右侧渲染侧边栏）
        image_info_label = QLabel("图像信息: 未加载")
        setattr(self, f'image_info_label_{viewer_id}', image_info_label)
        image_info_label.setVisible(False)
        
        # 像素信息标签（显示当前像素值）
        pixel_value_label = QLabel("像素值: -")
        setattr(self, f'pixel_value_label_{viewer_id}', pixel_value_label)
        control_layout.addWidget(pixel_value_label)
        
        main_layout.addLayout(control_layout)
        
        # 连接鼠标移动事件，显示像素值
        viewer.mouse_moved.connect(lambda x, y, val: self.on_viewer_mouse_moved(viewer_id, x, y, val))
        
        return panel

    def _load_workspace_preferences(self) -> None:
        settings = get_settings()
        active_window = settings.value("workspace/active_window", "viewer_1", type=str)
        sync_options_raw = settings.value("workspace/sync_options", "", type=str)
        sync_options = {}
        if isinstance(sync_options_raw, str) and sync_options_raw.strip():
            try:
                parsed = json.loads(sync_options_raw)
                if isinstance(parsed, dict):
                    sync_options = parsed
            except Exception:
                sync_options = {}
        self.workspace.set_window_count(2)
        self.workspace.apply_sync_options(sync_options)
        self.workspace.set_active_window(active_window)
        self._active_render_viewer_id = 1 if self.workspace.current_target_id() == "viewer_1" else 2

    def _save_workspace_preferences(self) -> None:
        settings = get_settings()
        settings.setValue("workspace/window_count", 2)
        settings.setValue("workspace/active_window", self.workspace.current_target_id())
        settings.setValue("workspace/sync_options", json.dumps(self.workspace.sync_options_dict(), ensure_ascii=False))

    def _toggle_window_layout(self) -> None:
        target = 1 if self.workspace.window_count() == 2 else 2
        self.workspace.set_window_count(target)
        if target == 1:
            self._set_active_render_viewer(1)
        self._save_workspace_preferences()

    def _on_workspace_active_window_changed(self, target_id: str) -> None:
        target_viewer_id = 1 if target_id == "viewer_1" else 2
        if target_viewer_id != self._active_render_viewer_id:
            self._set_active_render_viewer(target_viewer_id)

    def on_theme_mode_changed(self, _mode: str) -> None:
        self._theme_mode = _mode
        for viewer_id in (1, 2):
            viewer = getattr(self, f"image_viewer_{viewer_id}", None)
            if viewer is not None:
                viewer._apply_background_from_palette()
        self._update_sidebar_toggle_icon()

    def _on_viewer_files_dropped(self, viewer_id: int, paths: list[str]) -> None:
        mode, target = self._classify_drop_target(paths)
        if mode == "files":
            self.load_images(target, target_viewer_id=viewer_id, append=True)
            return
        if mode == "folder":
            self.open_folder(target)
            return
        if mode == "h5":
            self.open_h5_timeseries(target)
            return
        if mode == "gamma":
            self.open_gamma_folder(target)
            return
        QMessageBox.warning(self, "拖拽打开失败", "未识别拖入数据类型，无法打开。")

    def _classify_drop_target(self, paths: list[str]):
        if not paths:
            return None, None
        local_paths = [str(Path(item)) for item in paths if os.path.exists(item)]
        if not local_paths:
            return None, None
        raster_exts = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp", ".grd"}
        h5_exts = {".h5", ".hdf5", ".nc"}
        dirs = [item for item in local_paths if os.path.isdir(item)]
        files = [item for item in local_paths if os.path.isfile(item)]
        if dirs:
            folder = dirs[0]
            entries = [Path(folder) / name for name in os.listdir(folder)]
            file_entries = [entry for entry in entries if entry.is_file()]
            if any(entry.suffix.lower() in h5_exts for entry in file_entries):
                h5_file = next((entry for entry in file_entries if entry.suffix.lower() in h5_exts), None)
                return ("h5", str(h5_file)) if h5_file else (None, None)
            if any(entry.suffix.lower() in raster_exts for entry in file_entries):
                return "folder", folder
            return "gamma", folder
        if files:
            if len(files) == 1:
                first = files[0]
                ext = Path(first).suffix.lower()
                if ext in h5_exts:
                    return "h5", first
                if ext in raster_exts:
                    return "files", [first]
                return "gamma", str(Path(first).parent)
            raster_files = [item for item in files if Path(item).suffix.lower() in raster_exts]
            if raster_files:
                return "files", sorted(raster_files)
            h5_file = next((item for item in files if Path(item).suffix.lower() in h5_exts), None)
            if h5_file:
                return "h5", h5_file
            return "gamma", str(Path(files[0]).parent)
        return None, None
        
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

    def _default_render_settings_for_band_count(self, band_count: int) -> dict:
        cfg = default_raster_render_config(max(1, int(band_count or 1)))
        settings = cfg.to_settings()
        settings["display_mode"] = cfg.display_mode
        settings["gray_band"] = cfg.gray_band
        settings["rgb_bands"] = tuple(cfg.rgb_bands)
        settings["gamma"] = cfg.gamma
        settings["stretch_mode"] = cfg.stretch_mode
        settings["percent_clip"] = tuple(cfg.percent_clip)
        settings["std_dev_n"] = cfg.std_dev_n
        settings["auto_range"] = cfg.auto_range
        settings["value_range"] = tuple(cfg.value_range)
        settings["value_min"] = cfg.value_range[0]
        settings["value_max"] = cfg.value_range[1]
        settings["colormap_reversed"] = cfg.colormap_reversed
        settings["smooth_display"] = cfg.smooth_display
        return settings

    def _viewer_band_count(self, viewer_id: int) -> int:
        source = getattr(self, f'_cached_source_{viewer_id}', None)
        if source is not None:
            try:
                return max(1, int(source.metadata().band_count or 1))
            except Exception:
                pass
        data = getattr(self, f'_cached_image_{viewer_id}', None)
        if data is None:
            return 1
        return int(data.shape[2]) if data.ndim == 3 else 1

    def _normalized_settings_for_band_count(self, settings: dict | None, band_count: int) -> dict:
        base = self._default_render_settings_for_band_count(band_count)
        if isinstance(settings, dict):
            base.update(settings)
        bc = max(1, int(band_count or 1))
        if bc < 3 and base.get("display_mode") == "RGB":
            base["display_mode"] = "灰度"
        base["gray_band"] = max(1, min(int(base.get("gray_band", 1)), bc))
        rgb = tuple(base.get("rgb_bands", (1, 2, 3)))
        base["rgb_bands"] = tuple(max(1, min(int(v), bc)) for v in rgb[:3])
        base["percent_clip"] = tuple(base.get("percent_clip", (2.0, 98.0)))
        base["value_range"] = tuple(base.get("value_range", (0.0, 1.0)))
        base["value_min"] = base["value_range"][0]
        base["value_max"] = base["value_range"][1]
        return base

    def _store_active_render_state(self) -> None:
        viewer_id = self._active_render_viewer_id
        self._viewer_render_settings[viewer_id] = self._normalized_settings_for_band_count(
            self.render_settings.get_all_settings(),
            self._viewer_band_count(viewer_id),
        )
        self._viewer_colormaps[viewer_id] = self.colormap_combo.currentText()

    def _apply_render_state_to_controls(self, viewer_id: int) -> None:
        settings = self._normalized_settings_for_band_count(
            self._viewer_render_settings.get(viewer_id),
            self._viewer_band_count(viewer_id),
        )
        self._viewer_render_settings[viewer_id] = copy.deepcopy(settings)
        self.render_settings.blockSignals(True)
        self.render_settings.set_num_bands(max(1, self._viewer_band_count(viewer_id)))
        self.render_settings.display_mode_combo.setCurrentText(settings["display_mode"])
        self.render_settings.gray_band_spin.setValue(int(settings["gray_band"]))
        self.render_settings.band_r_spin.setValue(int(settings["rgb_bands"][0]))
        self.render_settings.band_g_spin.setValue(int(settings["rgb_bands"][1]))
        self.render_settings.band_b_spin.setValue(int(settings["rgb_bands"][2]))
        self.render_settings.stretch_combo.setCurrentText(settings["stretch_mode"])
        self.render_settings.percent_low_spin.setValue(float(settings["percent_clip"][0]))
        self.render_settings.percent_high_spin.setValue(float(settings["percent_clip"][1]))
        self.render_settings.std_dev_spin.setValue(float(settings["std_dev_n"]))
        self.render_settings.gamma_spin.setValue(float(settings["gamma"]))
        self.render_settings.auto_range_check.setChecked(not bool(settings["auto_range"]))
        self.render_settings.min_spin.setValue(float(settings["value_range"][0]))
        self.render_settings.max_spin.setValue(float(settings["value_range"][1]))
        self.render_settings.reverse_check.setChecked(bool(settings["colormap_reversed"]))
        self.render_settings.blockSignals(False)
        self.colormap_combo.blockSignals(True)
        self.colormap_combo.setCurrentText(self._viewer_colormaps.get(viewer_id, "gray"))
        self.colormap_combo.blockSignals(False)
        is_rgb = settings.get("display_mode") == "RGB"
        self.colormap_combo.setEnabled(not is_rgb)

    def _set_active_render_viewer(self, viewer_id: int) -> None:
        viewer_id = 1 if int(viewer_id) == 1 else 2
        if hasattr(self, "workspace") and self.workspace.window_count() == 1:
            viewer_id = 1
        if viewer_id == self._active_render_viewer_id:
            if hasattr(self, "render_sidebar"):
                self.render_sidebar.set_current_target(f"viewer_{viewer_id}")
            return
        self._store_active_render_state()
        self._active_render_viewer_id = viewer_id
        self._apply_render_state_to_controls(viewer_id)
        self._update_image_stats_to_render_settings(viewer_id=viewer_id)
        self._viewer_render_settings[viewer_id] = self._normalized_settings_for_band_count(
            self.render_settings.get_all_settings(),
            self._viewer_band_count(viewer_id),
        )
        self._refresh_colorbar_range(viewer_id)
        if hasattr(self, "workspace"):
            self.workspace.set_active_window(f"viewer_{viewer_id}")
        if hasattr(self, "render_sidebar"):
            self.render_sidebar.set_current_target(f"viewer_{viewer_id}")
        if hasattr(self, "render_sidebar_controller"):
            self.render_sidebar_controller.refresh()
        self._save_workspace_preferences()

    def _on_sidebar_target_changed(self, target_id: str) -> None:
        if target_id == "viewer_1":
            self._set_active_render_viewer(1)
        elif target_id == "viewer_2":
            self._set_active_render_viewer(2)
    
    def on_colormap_changed(self, colormap_name):
        """Colormap变化时更新当前选中窗口"""
        if hasattr(self, "render_sidebar_controller") and self.render_sidebar_controller is not None:
            viewer_id = self._active_render_viewer_id
            self._viewer_colormaps[viewer_id] = colormap_name
            self._refresh_colorbar_range(viewer_id)
            return
        # 跳过分隔符项（分隔符以"━"开头）
        if colormap_name.startswith('━'):
            return
        if self._loading_new_series:
            return
        if self.image_count > 0:
            self._show_loading_indicator("正在重新渲染图像...")
        viewer_id = self._active_render_viewer_id
        self._viewer_colormaps[viewer_id] = colormap_name
        viewer = getattr(self, f'image_viewer_{viewer_id}', None)
        if viewer is not None:
            viewer.set_colormap(colormap_name)
        reversed = self.render_settings.reverse_check.isChecked() if hasattr(self, 'render_settings') else False
        colorbar = getattr(self, f'colorbar_{viewer_id}', None)
        if colorbar is not None:
            colorbar.set_colormap(colormap_name, reversed)
        self._refresh_colorbar_range(viewer_id)
        if self.image_count > 0:
            self._hide_loading_indicator()
    
    def on_render_settings_changed(self):
        """渲染设置变化时延迟更新两个窗口，避免频繁重绘。"""
        if hasattr(self, "render_sidebar_controller") and self.render_sidebar_controller is not None:
            if hasattr(self, "_render_update_timer") and isValid(self._render_update_timer):
                self._render_update_timer.start(60)
            return
        if not isValid(self):
            return
        if self._loading_new_series:
            return
        if self.image_count > 0:
            self._show_loading_indicator("正在重新渲染图像...")
        if hasattr(self, "_render_update_timer") and isValid(self._render_update_timer):
            self._render_update_timer.start(150)

    def _apply_render_settings_update(self):
        """应用渲染设置更新。"""
        if hasattr(self, "render_sidebar_controller") and self.render_sidebar_controller is not None:
            self._refresh_colorbar_range(self._active_render_viewer_id)
            return
        if not isValid(self):
            return
        try:
            viewer_id = self._active_render_viewer_id
            if self.render_settings.is_auto_range():
                self._update_image_stats_to_render_settings(viewer_id=viewer_id)
            settings = self._normalized_settings_for_band_count(
                self.render_settings.get_all_settings(),
                self._viewer_band_count(viewer_id),
            )
            self._viewer_render_settings[viewer_id] = copy.deepcopy(settings)
            self.colormap_combo.setEnabled(settings.get("display_mode") != "RGB")
            viewer = getattr(self, f'image_viewer_{viewer_id}', None)
            if viewer is not None:
                viewer.set_render_settings(settings)
                viewer.set_colormap(self._viewer_colormaps.get(viewer_id, "gray"))
            self._refresh_colorbar_range(viewer_id)
            self._sync_selected_pixel_markers()
        finally:
            self._hide_loading_indicator()

    def closeEvent(self, event):
        try:
            if hasattr(self, "_render_update_timer") and isValid(self._render_update_timer):
                self._render_update_timer.stop()
            if hasattr(self, "render_sidebar_controller") and self.render_sidebar_controller is not None:
                self.render_sidebar_controller.close()
            self._save_workspace_preferences()
        except Exception:
            pass
        super().closeEvent(event)

    def _navigation_item_label(self, index):
        prefix = f"{index + 1:04d}"
        entry = self._get_layer_entry(index)
        if entry is not None:
            label = entry.date_label or entry.display_name
            return f"{prefix} | {label}"
        return prefix

    def _refresh_navigation_controls(self):
        has_images = self.image_count > 0
        if hasattr(self, "jump_pixel_btn") and self.jump_pixel_btn is not None:
            self.jump_pixel_btn.setEnabled(has_images)
        for viewer_id in [1, 2]:
            slider = getattr(self, f'image_slider_{viewer_id}', None)
            prev_btn = getattr(self, f'prev_btn_{viewer_id}', None)
            next_btn = getattr(self, f'next_btn_{viewer_id}', None)
            jump_btn = getattr(self, f'jump_btn_{viewer_id}', None)
            image_select_combo = getattr(self, f'image_select_combo_{viewer_id}', None)
            if slider is not None:
                slider.setMaximum(max(0, self.image_count - 1))
                slider.setEnabled(has_images)
            if prev_btn is not None:
                prev_btn.setEnabled(has_images)
            if next_btn is not None:
                next_btn.setEnabled(has_images)
            if jump_btn is not None:
                jump_btn.setEnabled(has_images)
            if image_select_combo is not None:
                image_select_combo.blockSignals(True)
                image_select_combo.clear()
                if has_images:
                    for index in range(self.image_count):
                        image_select_combo.addItem(self._navigation_item_label(index), index)
                    image_select_combo.setEnabled(True)
                    current_index = getattr(self, f'current_image_index_{viewer_id}', 0)
                    image_select_combo.setCurrentIndex(min(current_index, self.image_count - 1))
                else:
                    image_select_combo.setEnabled(False)
                image_select_combo.blockSignals(False)

    def _reset_render_controls_for_new_series(self, band_count=1, colormap='gray'):
        """新时序数据使用默认渲染参数，避免继承上一批影像的显示状态。"""
        first_entry = self._get_layer_entry(0)
        source_kind = "gdal"
        has_color_table = False
        nodata = self.nodata_value
        custom_properties = {}
        metadata_path = ""
        width = int(self.image_shape[1] if self.image_shape is not None and len(self.image_shape) >= 2 else 1)
        height = int(self.image_shape[0] if self.image_shape is not None and len(self.image_shape) >= 2 else 1)
        if first_entry is not None:
            source_kind = first_entry.source_kind
            metadata_path = first_entry.source_path
            has_color_table = bool(first_entry.metadata.get("has_color_table", False))
            nodata = first_entry.metadata.get("nodata_value", nodata)
            width = int(first_entry.metadata.get("width", width))
            height = int(first_entry.metadata.get("height", height))
            custom_properties["categorical"] = has_color_table
        metadata = ImageSourceMetadata(
            id="timeseries_preview",
            path=metadata_path,
            path_mode="absolute",
            width=width,
            height=height,
            band_count=max(1, int(band_count or 1)),
            dtype="float32",
            nodata=nodata,
            crs_wkt=None,
            geotransform=None,
            resolution=None,
            has_georef=False,
            has_color_table=has_color_table,
            color_table=None,
            custom_properties={"source_kind": source_kind, **custom_properties},
        )
        style = DefaultRenderStyleFactory.create(metadata)
        if source_kind == 'gamma' and self.gamma_format.startswith('cpx') and hasattr(style, "color_ramp"):
            style = replace(style, color_ramp=replace(style.color_ramp, name="hsv"))
        config = style_to_legacy_config(style, DefaultRenderStyleFactory.create_display_settings(metadata))
        self.render_settings.blockSignals(True)
        self.render_settings.reset_to_defaults(max(1, int(band_count or 1)))
        self.render_settings.display_mode_combo.setCurrentText(config.display_mode)
        self.render_settings.gray_band_spin.setValue(int(config.gray_band))
        self.render_settings.band_r_spin.setValue(int(config.rgb_bands[0]))
        self.render_settings.band_g_spin.setValue(int(config.rgb_bands[1]))
        self.render_settings.band_b_spin.setValue(int(config.rgb_bands[2]))
        self.render_settings.stretch_combo.setCurrentText(config.stretch_mode)
        self.render_settings.auto_range_check.setChecked(not bool(config.auto_range))
        self.render_settings.min_spin.setValue(float(config.value_range[0]))
        self.render_settings.max_spin.setValue(float(config.value_range[1]))
        self.render_settings.gamma_spin.setValue(float(config.gamma))
        self.render_settings.blockSignals(False)
        target_colormap = config.colormap_name or colormap
        if source_kind == "h5":
            target_colormap = colormap or "jet"
        self.colormap_combo.blockSignals(True)
        self.colormap_combo.setCurrentText(target_colormap)
        self.colormap_combo.blockSignals(False)
        settings = self._normalized_settings_for_band_count(
            self.render_settings.get_all_settings(),
            max(1, int(band_count or 1)),
        )
        self._viewer_render_settings[1] = copy.deepcopy(settings)
        self._viewer_render_settings[2] = copy.deepcopy(settings)
        self._viewer_colormaps[1] = self.colormap_combo.currentText()
        self._viewer_colormaps[2] = self.colormap_combo.currentText()
        self._active_render_viewer_id = 1
        self.colormap_combo.setEnabled(settings.get("display_mode") != "RGB")
    
    def on_suggest_colormap(self, colormap_name):
        """接收建议的colormap并切换"""
        self.colormap_combo.setCurrentText(colormap_name)
    
    def _update_image_stats_to_render_settings(self, viewer_id=1):
        """从当前图像计算统计信息并更新到渲染设置"""
        source = getattr(self, f'_cached_source_{viewer_id}', None)
        if source is not None:
            settings = self._viewer_render_settings.get(viewer_id) or self.render_settings.get_all_settings()
            value_range = source.value_range_for_settings(settings)
            if value_range is not None:
                self.render_settings.set_image_stats(*value_range)
                return

        data = getattr(self, f'_cached_image_{viewer_id}', None)
        if data is not None:
            arr = data
            nodata_value = self._get_effective_nodata_for_index(getattr(self, f'current_image_index_{viewer_id}', 0))
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
            if not self._viewer_has_image.get(viewer_id, False):
                viewer.clear_selected_pixel()
            elif self.selected_pixel is None:
                viewer.clear_selected_pixel()
            else:
                current_index = getattr(self, f'current_image_index_{viewer_id}', 0)
                pixel = self._resolve_pixel_for_layer(current_index)
                if pixel is None:
                    viewer.clear_selected_pixel()
                else:
                    viewer.set_selected_pixel(*pixel)

    def _get_colorbar_data_range(self, viewer_id):
        """获取指定窗口当前图像的有效数据范围。"""
        settings = self._normalized_settings_for_band_count(
            self._viewer_render_settings.get(viewer_id) or self.render_settings.get_all_settings(),
            self._viewer_band_count(viewer_id),
        )
        source = getattr(self, f'_cached_source_{viewer_id}', None)
        if source is not None:
            try:
                value_range = source.value_range_for_settings(settings)
                if value_range is not None:
                    return float(value_range[0]), float(value_range[1])
            except Exception:
                pass

        data = getattr(self, f'_cached_image_{viewer_id}', None)
        if data is None:
            return None

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

        if int(viewer_id) == int(self._active_render_viewer_id):
            base_settings = self.render_settings.get_all_settings()
        else:
            base_settings = self._viewer_render_settings.get(viewer_id) or self.render_settings.get_all_settings()
        settings = self._normalized_settings_for_band_count(base_settings, self._viewer_band_count(viewer_id))
        data_range = self._get_colorbar_data_range(viewer_id)
        if settings.get('auto_range', True) and data_range is not None:
            vmin, vmax = data_range
        else:
            value_range = settings.get('value_range') or (settings.get('value_min', 0.0), settings.get('value_max', 1.0))
            vmin = float(value_range[0])
            vmax = float(value_range[1])

        colorbar.set_range(vmin, vmax)
        cmap_name = self._viewer_colormaps.get(viewer_id, self.colormap_combo.currentText())
        colorbar.set_colormap(cmap_name, bool(settings.get('colormap_reversed', False)))

    def _clear_cached_images(self):
        """清空两个窗口的图像缓存。"""
        self._cached_image_1 = None
        self._cached_index_1 = -1
        self._cached_original_size_1 = None
        self._cached_source_1 = None
        self._cached_image_2 = None
        self._cached_index_2 = -1
        self._cached_original_size_2 = None
        self._cached_source_2 = None
        self._h5_pixel_series_cache.clear()

    def _set_viewer_has_image(self, viewer_id: int, has_image: bool, *, index: Optional[int] = None) -> None:
        viewer_id = 1 if int(viewer_id) == 1 else 2
        self._viewer_has_image[viewer_id] = bool(has_image)
        if index is not None:
            setattr(self, f'current_image_index_{viewer_id}', int(index))

    def _clear_viewer_display(self, viewer_id: int) -> None:
        viewer = getattr(self, f'image_viewer_{viewer_id}', None)
        if viewer is not None:
            viewer.clear_raster()
            viewer.set_scene_mapping(None, None)
            viewer.set_geotransform(None, None)
        setattr(self, f'_cached_image_{viewer_id}', None)
        setattr(self, f'_cached_index_{viewer_id}', -1)
        setattr(self, f'_cached_original_size_{viewer_id}', None)
        setattr(self, f'_cached_source_{viewer_id}', None)
        image_index_label = getattr(self, f'image_index_label_{viewer_id}', None)
        if image_index_label is not None:
            image_index_label.setText("0/0")
        image_info_label = getattr(self, f'image_info_label_{viewer_id}', None)
        if image_info_label is not None:
            image_info_label.setText("图像信息: 未加载")
        pixel_value_label = getattr(self, f'pixel_value_label_{viewer_id}', None)
        if pixel_value_label is not None:
            pixel_value_label.setText("像素值: -")
        colorbar = getattr(self, f'colorbar_{viewer_id}', None)
        if colorbar is not None:
            colorbar.set_current_value(None)

    @staticmethod
    def _normalized_full_path(file_path: str) -> str:
        return os.path.normcase(os.path.abspath(file_path))

    def _dedupe_file_paths(self, file_paths: list[str], *, existing_paths: list[str] | None = None) -> tuple[list[str], int]:
        """按完整路径去重，保留首次出现的路径顺序。"""
        seen = {
            self._normalized_full_path(item)
            for item in (existing_paths or [])
            if item
        }
        deduped: list[str] = []
        duplicate_count = 0
        for item in file_paths or []:
            if not item:
                continue
            normalized = self._normalized_full_path(item)
            if normalized in seen:
                duplicate_count += 1
                continue
            seen.add(normalized)
            deduped.append(os.path.abspath(item))
        return deduped, duplicate_count

    def _show_loading_indicator(self, message: str):
        self.setWindowTitle(f"{self._loading_title_text} - 加载中")
        if hasattr(self, "operation_progress") and self.operation_progress is not None:
            self.operation_progress.start_task(message.replace("\n", " | "), 0)
        QApplication.processEvents()

    def _set_series_status_text(self, text: str) -> None:
        """将时序加载状态显示到底部进度日志区域。"""
        if hasattr(self, "operation_progress") and self.operation_progress is not None:
            self.operation_progress.progress_bar.setVisible(True)
            self.operation_progress.progress_bar.setRange(0, 100)
            self.operation_progress.progress_bar.setValue(0)
            self.operation_progress.message_label.setText(text)

    def _sync_db_toggle_widgets(self, checked: Optional[bool] = None) -> None:
        checked = bool(self._converted_to_db) if checked is None else bool(checked)
        if hasattr(self, "db_toggle_check") and self.db_toggle_check is not None:
            self.db_toggle_check.blockSignals(True)
            self.db_toggle_check.setChecked(checked)
            self.db_toggle_check.blockSignals(False)
        if hasattr(self, "render_sidebar"):
            self.render_sidebar.set_db_checked(checked)

    def _hide_loading_indicator(self):
        if not isValid(self):
            return
        self.setWindowTitle(self._loading_title_text)
        if hasattr(self, "operation_progress") and self.operation_progress is not None:
            self.operation_progress.finish_task("完成")
        if self.image_count <= 0:
            self._set_series_status_text("未加载图像")
        elif self.data_source_type == 'gamma':
            self._set_series_status_text(f"已加载 {self.image_count} 张GAMMA时序影像")
        elif self.data_source_type == 'h5':
            self._set_series_status_text(f"已加载 {self.image_count} 张时序影像")
        elif self.data_source_type == 'mixed':
            self._set_series_status_text(f"已加载 {self.image_count} 张混合时序影像")
        else:
            self._set_series_status_text(f"已加载 {self.image_count} 张图像")

    def _toggle_sidebar(self):
        if not hasattr(self, "render_sidebar") or not hasattr(self, "outer_splitter"):
            return
        if self._sidebar_visible:
            self.render_sidebar.setVisible(False)
            self.outer_splitter.setSizes([self.outer_splitter.width(), 0])
            base_width = getattr(self, "_sidebar_base_width", 0)
            if base_width > 0:
                self.resize(base_width, self.height())
            fit_window_to_screen(self, margin=24, center=False)
            self._sidebar_visible = False
        else:
            sidebar_width = max(180, min(240, int(self.render_sidebar.sizeHint().width())))
            self._sidebar_base_width = self.width()
            applied_sidebar_width = expand_window_width_safely(
                self,
                sidebar_width,
                min_main_width=520,
                margin=24,
            )
            self.render_sidebar.setVisible(True)
            self.outer_splitter.setSizes([max(1, self.width() - applied_sidebar_width), max(1, applied_sidebar_width)])
            self._sidebar_visible = True

    def _load_material_icon_font(self) -> str | None:
        font_path = Path(__file__).resolve().parents[2] / "resources" / "fonts" / "MaterialIcons-Regular.ttf"
        if not font_path.exists():
            return None
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id < 0:
            return None
        families = QFontDatabase.applicationFontFamilies(font_id)
        return families[0] if families else None

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, lambda: fit_window_to_screen(self, margin=24, center=True))

    def _material_icon(self, icon_name: str, *, size: int = 20, rotation_angle: float = 0.0) -> QIcon:
        if not self._material_icon_family:
            return QIcon()
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        font = QFont(self._material_icon_family)
        font.setPixelSize(size - 2)
        painter.setFont(font)
        icon_color = QColor("#e6e6e6") if self._theme_mode == "dark" else QColor("#334155")
        painter.setPen(icon_color)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, icon_name)
        painter.end()
        if rotation_angle:
            pixmap = pixmap.transformed(QTransform().rotate(float(rotation_angle)), Qt.SmoothTransformation)
        return QIcon(pixmap)

    def _update_sidebar_toggle_icon(self) -> None:
        if hasattr(self, "toggle_sidebar_btn") and self.toggle_sidebar_btn is not None:
            self.toggle_sidebar_btn.setIcon(self._material_icon("tune"))

    def _on_db_toggled(self, enabled: bool) -> None:
        if enabled == bool(self._converted_to_db):
            if hasattr(self, "db_toggle_check") and self.db_toggle_check.isChecked() != bool(enabled):
                self.db_toggle_check.blockSignals(True)
                self.db_toggle_check.setChecked(bool(enabled))
                self.db_toggle_check.blockSignals(False)
            if hasattr(self, "render_sidebar"):
                self.render_sidebar.set_db_checked(bool(enabled))
            return
        self._sync_db_toggle_widgets(enabled)
        if hasattr(self, "db_toggle_check") and self.db_toggle_check is not None:
            self.db_toggle_check.repaint()
        QApplication.processEvents()
        if enabled:
            QTimer.singleShot(0, lambda: self.convert_to_db(show_message=False, confirm=False))
            return
        self._converted_to_db = False
        self._clear_cached_images()
        if self.image_count > 0:
            self.show_image(1, reset_view=True)
            self.show_image(2, reset_view=True)
            self._update_image_stats_to_render_settings()
            self._apply_render_settings_update()

    def _get_image_metadata(self, index) -> Optional[dict]:
        """获取指定索引影像的元数据。"""
        if 0 <= index < len(self.image_metadata):
            return self.image_metadata[index]
        return None

    def _get_layer_entry(self, index: int) -> Optional[TimeSeriesLayerEntry]:
        if 0 <= int(index) < len(self.time_series_layers):
            return self.time_series_layers[int(index)]
        return None

    def _layer_has_geo(self, index: int) -> bool:
        entry = self._get_layer_entry(index)
        return bool(entry and entry.metadata.get("has_geo"))

    def _build_layer_metadata_from_source(self, source, source_path: str) -> dict[str, Any]:
        metadata = source.metadata()
        geotransform = metadata.geotransform
        projection = metadata.crs_wkt
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
                metadata.width,
                metadata.height,
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
            "path": source_path,
            "width": int(metadata.width),
            "height": int(metadata.height),
            "band_count": int(metadata.band_count or 1),
            "nodata_value": metadata.nodata,
            "geotransform": geotransform,
            "projection": projection,
            "inv_geotransform": inv_geotransform,
            "to_wgs84_transform": to_wgs84_transform,
            "from_wgs84_transform": from_wgs84_transform,
            "bounds_wgs84": bounds_wgs84,
            "scene_rect": scene_rect,
            "has_geo": geotransform is not None and inv_geotransform is not None,
            "has_color_table": bool(getattr(metadata, "has_color_table", False)),
            "color_table": getattr(metadata, "color_table", None),
        }

    def _build_file_layer_entry(self, file_path: str) -> Optional[TimeSeriesLayerEntry]:
        normalized_path = os.path.abspath(file_path)
        source = open_raster_source(normalized_path, pyramid_threshold_mb=self.pyramid_threshold_mb)
        metadata = self._build_layer_metadata_from_source(source, normalized_path)
        date_labels = extract_dates_from_filenames([normalized_path]) or []
        date_label = date_labels[0] if date_labels else os.path.basename(normalized_path)
        return TimeSeriesLayerEntry(
            source_key=self._normalized_full_path(normalized_path),
            display_name=os.path.basename(normalized_path),
            date_label=date_label,
            source_kind="folder",
            source_path=normalized_path,
            metadata=metadata,
            source=source,
        )

    def _build_h5_layer_entries(
        self,
        file_path: str,
        date_list: list[str],
        start_index: int,
        num_dates: int,
        width: int,
        height: int,
    ) -> list[TimeSeriesLayerEntry]:
        entries: list[TimeSeriesLayerEntry] = []
        metadata = {
            "path": os.path.abspath(file_path),
            "width": int(width),
            "height": int(height),
            "band_count": 1,
            "nodata_value": 0,
            "geotransform": None,
            "projection": None,
            "inv_geotransform": None,
            "to_wgs84_transform": None,
            "from_wgs84_transform": None,
            "bounds_wgs84": None,
            "scene_rect": None,
            "has_geo": False,
            "has_color_table": False,
            "color_table": None,
        }
        for index in range(start_index, num_dates):
            layer_pos = index - start_index
            label = date_list[layer_pos] if layer_pos < len(date_list) else f"frame_{index:04d}"
            entries.append(
                TimeSeriesLayerEntry(
                    source_key=f"{self._normalized_full_path(file_path)}#timeseries:{index}",
                    display_name=label,
                    date_label=label,
                    source_kind="h5",
                    source_path=os.path.abspath(file_path),
                    metadata=dict(metadata),
                    frame_index=index,
                )
            )
        return entries

    def _build_gamma_layer_entries(self, file_paths: list[str], gamma_format: str, width: int, height: int) -> list[TimeSeriesLayerEntry]:
        entries: list[TimeSeriesLayerEntry] = []
        extracted_dates = extract_dates_from_filenames(file_paths) or []
        for index, file_path in enumerate(file_paths):
            normalized_path = os.path.abspath(file_path)
            source = GammaVrtRasterSource(
                normalized_path,
                width,
                height,
                gamma_format,
                self.pyramid_threshold_mb,
            )
            metadata = self._build_layer_metadata_from_source(source, normalized_path)
            metadata["nodata_value"] = 0
            date_label = extracted_dates[index] if index < len(extracted_dates) else os.path.basename(normalized_path)
            entries.append(
                TimeSeriesLayerEntry(
                    source_key=self._normalized_full_path(normalized_path),
                    display_name=os.path.basename(normalized_path),
                    date_label=date_label,
                    source_kind="gamma",
                    source_path=normalized_path,
                    metadata=metadata,
                    frame_index=index,
                    source=source,
                )
            )
        return entries

    def _validate_time_series_layers(self, entries: list[TimeSeriesLayerEntry]) -> tuple[bool, str]:
        if not entries:
            return False, "没有可用图层。"

        metadata_list = [entry.metadata for entry in entries]
        for i in range(len(metadata_list)):
            for j in range(i + 1, len(metadata_list)):
                left = metadata_list[i]
                right = metadata_list[j]
                same_size = (left["width"], left["height"]) == (right["width"], right["height"])
                left_has_geo = bool(left.get("has_geo") and left.get("bounds_wgs84") is not None)
                right_has_geo = bool(right.get("has_geo") and right.get("bounds_wgs84") is not None)
                if left_has_geo and right_has_geo and not bounds_overlap(left["bounds_wgs84"], right["bounds_wgs84"]):
                    return False, "存在带地理信息但无重叠区域的图像，无法组成时序数据。"
                if not left_has_geo and not right_has_geo and not same_size:
                    return False, "存在不带地理信息且尺寸不一致的图像，无法组成时序数据。"
        return True, ""

    def _update_shared_scene_rect(self) -> None:
        scene_rects = [
            entry.metadata.get("scene_rect")
            for entry in self.time_series_layers
            if entry.metadata.get("scene_rect") is not None
        ]
        if not scene_rects:
            self.shared_scene_rect = None
            return
        min_x = min(rect[0] for rect in scene_rects)
        min_y = min(rect[1] for rect in scene_rects)
        max_x = max(rect[0] + rect[2] for rect in scene_rects)
        max_y = max(rect[1] + rect[3] for rect in scene_rects)
        self.shared_scene_rect = (min_x, min_y, max_x - min_x, max_y - min_y)

    def _sync_derived_series_state(self) -> None:
        self.image_count = len(self.time_series_layers)
        self.image_metadata = [entry.metadata for entry in self.time_series_layers]
        self.image_files = [entry.source_path for entry in self.time_series_layers]
        self.date_list = [entry.date_label for entry in self.time_series_layers]
        first_entry = self._get_layer_entry(0)
        if first_entry is None:
            self.image_shape = None
            self.nodata_value = None
            self.geotransform = None
            self.projection = None
            self._update_shared_scene_rect()
            return
        band_count = first_entry.metadata.get("band_count") or 1
        if band_count > 1:
            self.image_shape = (first_entry.metadata["height"], first_entry.metadata["width"], band_count)
        else:
            self.image_shape = (first_entry.metadata["height"], first_entry.metadata["width"])
        if not self._nodata_user_locked:
            self.nodata_value = first_entry.metadata.get("nodata_value")
        self.geotransform = first_entry.metadata.get("geotransform")
        self.projection = first_entry.metadata.get("projection")
        self._update_shared_scene_rect()

    def _resolve_pixel_for_layer(self, index: int, pixel: Optional[tuple[int, int]] = None, lonlat: Optional[tuple[float, float]] = None) -> Optional[tuple[int, int]]:
        query_pixel = pixel if pixel is not None else self.selected_pixel
        query_lonlat = lonlat if lonlat is not None else self.selected_geo
        if query_lonlat is not None and self._layer_has_geo(index):
            mapped_x, mapped_y = self._map_lonlat_to_pixel(index, *query_lonlat)
            if self._is_pixel_in_bounds(index, mapped_x, mapped_y):
                return int(mapped_x), int(mapped_y)
            return None
        if query_pixel is None:
            return None
        x, y = int(query_pixel[0]), int(query_pixel[1])
        if self._is_pixel_in_bounds(index, x, y):
            return x, y
        return None

    def _get_image_dimensions(self, index) -> Tuple[Optional[int], Optional[int]]:
        """获取指定影像的原始尺寸 (width, height)。"""
        metadata = self._get_image_metadata(index)
        if metadata is not None:
            return metadata.get('width'), metadata.get('height')
        return None, None

    def _build_folder_image_metadata(self, file_path) -> Optional[dict]:
        """构建单张文件夹影像的元数据，不读取整幅数据。"""
        entry = self._build_file_layer_entry(file_path)
        return None if entry is None else entry.metadata

    def _get_effective_nodata_for_index(self, index):
        """获取指定影像当前生效的Nodata值。"""
        if self._nodata_user_locked:
            return self.nodata_value
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
        """兼容旧调用入口，内部统一更新共享场景范围。"""
        self._update_shared_scene_rect()

    def _validate_folder_series_metadata(self, metadata_list: list[dict]) -> tuple[bool, str, str, str]:
        """兼容旧调用入口。"""
        entries = [
            TimeSeriesLayerEntry(
                source_key=f"legacy:{index}",
                display_name=str(index),
                date_label=str(index),
                source_kind="folder",
                source_path=metadata.get("path", str(index)),
                metadata=metadata,
            )
            for index, metadata in enumerate(metadata_list)
        ]
        is_valid, error_message = self._validate_time_series_layers(entries)
        return is_valid, error_message, "", ""

    def _apply_folder_series_state(
        self,
        layer_entries: list[TimeSeriesLayerEntry],
        *,
        clear_selection: bool,
        reset_render: bool,
    ) -> bool:
        is_valid, error_message = self._validate_time_series_layers(layer_entries)
        if not is_valid:
            QMessageBox.critical(self, "错误", error_message)
            return False

        self.time_series_layers = list(layer_entries)
        source_kinds = {entry.source_kind for entry in layer_entries}
        self.data_source_type = next(iter(source_kinds)) if len(source_kinds) == 1 else "mixed"
        self._sync_derived_series_state()
        first_entry = self._get_layer_entry(0)
        band_count = first_entry.metadata.get('band_count') if first_entry else 1

        if clear_selection:
            self._clear_selected_pixel_state()
            self.selected_geo = None
            self.selected_viewer_id = None

        self._clear_cached_images()
        self._refresh_navigation_controls()
        self._set_series_status_text(f"已加载 {self.image_count} 张图像")

        if reset_render:
            self._reset_render_controls_for_new_series(band_count, 'gray')

        return True

    def _refresh_viewers_after_series_change(self, *, reset_view: bool) -> None:
        shown_any = False
        for viewer_id in (1, 2):
            if self._viewer_has_image.get(viewer_id, False) and self.image_count > 0:
                current_index = getattr(self, f'current_image_index_{viewer_id}', 0)
                current_index = max(0, min(current_index, self.image_count - 1))
                setattr(self, f'current_image_index_{viewer_id}', current_index)
                self.show_image(viewer_id, reset_view=reset_view)
                shown_any = True
            else:
                self._clear_viewer_display(viewer_id)

        if shown_any:
            active_id = self._active_render_viewer_id
            if not self._viewer_has_image.get(active_id, False):
                active_id = 1 if self._viewer_has_image.get(1, False) else 2
                if self._viewer_has_image.get(active_id, False):
                    self._set_active_render_viewer(active_id)
            self._update_image_stats_to_render_settings(viewer_id=self._active_render_viewer_id)
            self._apply_render_settings_update()
        else:
            self._sync_selected_pixel_markers()

        if self.selected_pixel:
            self.update_time_series_plot()

    def _append_dropped_images(self, file_list: list[str], target_viewer_id: int) -> None:
        """将拖入的普通图像追加到当前时序。"""
        target_previously_had_image = self._viewer_has_image.get(target_viewer_id, False)
        current_keys = {}
        for viewer_id in (1, 2):
            if not self._viewer_has_image.get(viewer_id, False):
                continue
            current_index = getattr(self, f'current_image_index_{viewer_id}', 0)
            entry = self._get_layer_entry(current_index)
            if entry is not None:
                current_keys[viewer_id] = entry.source_key
        file_list, duplicate_count = self._dedupe_file_paths(
            file_list,
            existing_paths=[
                entry.source_path
                for entry in self.time_series_layers
                if entry.source_kind == 'folder'
            ],
        )
        if duplicate_count > 0 and not file_list:
            QMessageBox.information(self, "提示", "拖入的数据都已存在，未重复导入。")
            return
        unreadable_files = []
        new_entries: list[TimeSeriesLayerEntry] = []

        for file_path in file_list:
            if not os.path.exists(file_path):
                unreadable_files.append(f"{os.path.basename(file_path)}: 文件不存在")
                continue
            try:
                entry = self._build_file_layer_entry(file_path)
                if entry is None:
                    unreadable_files.append(f"{os.path.basename(file_path)}: 元数据读取失败")
                    continue
                new_entries.append(entry)
            except Exception as e:
                unreadable_files.append(f"{os.path.basename(file_path)}: {str(e)}")

        if unreadable_files:
            message = "以下文件读取失败，已跳过：\n" + "\n".join(unreadable_files[:10])
            if len(unreadable_files) > 10:
                message += f"\n... 还有 {len(unreadable_files) - 10} 个文件"
            QMessageBox.warning(self, "警告", message)

        if duplicate_count > 0:
            QMessageBox.information(self, "提示", f"已按完整路径去重，跳过 {duplicate_count} 个重复文件。")

        if not new_entries:
            return

        if self.time_series_layers:
            layer_entries = list(self.time_series_layers) + new_entries
            clear_selection = False
            reset_render = False
        else:
            layer_entries = list(new_entries)
            clear_selection = True
            reset_render = True

        reverse = self.sort_order_combo.currentIndex() == 1
        layer_entries.sort(key=lambda item: item.display_name.lower(), reverse=reverse)
        target_entry = new_entries[-1]
        target_index = next(
            (idx for idx in range(len(layer_entries) - 1, -1, -1) if layer_entries[idx].source_key == target_entry.source_key),
            len(layer_entries) - 1,
        )

        if not self._apply_folder_series_state(
            layer_entries,
            clear_selection=clear_selection,
            reset_render=reset_render,
        ):
            return

        if reset_render:
            self._converted_to_db = False
            self._nodata_user_locked = False
            self.is_gamma_timeseries = False

        self._sync_db_toggle_widgets()

        if len(layer_entries) == len(new_entries) and len(new_entries) == 1:
            other_viewer_id = 2 if int(target_viewer_id) == 1 else 1
            self._set_viewer_has_image(target_viewer_id, True, index=target_index)
            self._set_viewer_has_image(other_viewer_id, False)
        else:
            for viewer_id in (1, 2):
                if self._viewer_has_image.get(viewer_id, False):
                    current_key = current_keys.get(viewer_id)
                    restored_index = next(
                        (i for i, entry in enumerate(self.time_series_layers) if entry.source_key == current_key),
                        None,
                    )
                    if restored_index is None:
                        restored_index = max(0, min(getattr(self, f'current_image_index_{viewer_id}', 0), self.image_count - 1))
                    setattr(self, f'current_image_index_{viewer_id}', restored_index)
            self._set_viewer_has_image(target_viewer_id, True, index=target_index)

        self._refresh_viewers_after_series_change(reset_view=clear_selection or not target_previously_had_image)
    def _configure_viewer_scene_mapping(self, viewer, index):
        """配置查看器的统一场景范围和当前图像摆放范围。"""
        metadata = self._get_image_metadata(index)
        if self.shared_scene_rect is not None and metadata is not None and metadata.get("scene_rect") is not None:
            viewer.set_scene_mapping(
                scene_world_rect=self.shared_scene_rect,
                image_world_rect=metadata.get('scene_rect'),
            )
        else:
            viewer.set_scene_mapping(None, None)
    
    def switch_image(self, viewer_id, direction):
        """切换图像
        
        Args:
            viewer_id: 查看器ID（1或2）
            direction: 方向（-1表示上一张，1表示下一张）
        """
        if not self._viewer_has_image.get(viewer_id, False):
            if self.image_count <= 0:
                return
            self._set_viewer_has_image(viewer_id, True, index=0)
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
        if not self._viewer_has_image.get(viewer_id, False):
            self._set_viewer_has_image(viewer_id, True, index=value)
        current_index = getattr(self, f'current_image_index_{viewer_id}')
        if value != current_index:
            setattr(self, f'current_image_index_{viewer_id}', value)
            self.show_image(viewer_id, reset_view=False)

    def preview_slider_position(self, viewer_id, value):
        """拖动滑块时仅预览目标索引，不立即加载图像。"""
        if self.image_count == 0:
            return
        index_label = getattr(self, f'image_index_label_{viewer_id}')
        info_label = getattr(self, f'image_info_label_{viewer_id}')
        index_label.setText(f"{value + 1}/{self.image_count}")
        info_label.setText(f"准备切换到: {self._navigation_item_label(value)}")

    def jump_to_image(self, viewer_id):
        """弹出输入框跳转到指定影像。"""
        if self.image_count == 0:
            return
        current_index = getattr(self, f'current_image_index_{viewer_id}')
        value, ok = QInputDialog.getInt(
            self,
            "跳转到影像",
            f"请输入影像序号（1-{self.image_count}）:",
            current_index + 1,
            1,
            self.image_count,
            1,
        )
        if ok:
            target_index = value - 1
            self._set_viewer_has_image(viewer_id, True, index=target_index)
            setattr(self, f'current_image_index_{viewer_id}', target_index)
            slider = getattr(self, f'image_slider_{viewer_id}')
            slider.blockSignals(True)
            slider.setValue(target_index)
            slider.blockSignals(False)
            self.show_image(viewer_id, reset_view=False)

    def _get_current_image_shape_for_viewer(self, viewer_id: int) -> Optional[tuple[int, int]]:
        """获取当前窗口图像尺寸，返回(height, width)。"""
        if not self._viewer_has_image.get(viewer_id, False):
            return None
        current_index = getattr(self, f'current_image_index_{viewer_id}', -1)
        if current_index < 0:
            return None
        metadata = self._get_image_metadata(current_index) or {}
        width = int(metadata.get("width", 0) or 0)
        height = int(metadata.get("height", 0) or 0)
        if width > 0 and height > 0:
            return (height, width)
        size = self.image_shape
        if size is not None and len(size) >= 2:
            return (int(size[0]), int(size[1]))
        viewer = getattr(self, f'image_viewer_{viewer_id}', None)
        if viewer is None:
            return None
        image_size = viewer.get_image_size()
        if image_size is None:
            return None
        return (int(image_size[0]), int(image_size[1]))

    def jump_to_pixel(self):
        """输入行列号后定位到指定像素，展示标记并刷新时序曲线。"""
        if self.image_count <= 0:
            QMessageBox.information(self, "提示", "请先加载时序影像。")
            return
        viewer_id = int(self._active_render_viewer_id)
        if not self._viewer_has_image.get(viewer_id, False):
            self._set_viewer_has_image(viewer_id, True, index=0)
            self.show_image(viewer_id, reset_view=False)
        shape = self._get_current_image_shape_for_viewer(viewer_id)
        if shape is None:
            QMessageBox.warning(self, "提示", "当前无法获取图像尺寸，请先显示影像。")
            return
        height, width = shape
        row_1b, ok_row = QInputDialog.getInt(
            self,
            "跳转像素",
            f"请输入行号（1-{height}）:",
            max(1, min(int(self._last_jump_row_1b), int(height))),
            1,
            height,
            1,
        )
        if not ok_row:
            return
        col_1b, ok_col = QInputDialog.getInt(
            self,
            "跳转像素",
            f"请输入列号（1-{width}）:",
            max(1, min(int(self._last_jump_col_1b), int(width))),
            1,
            width,
            1,
        )
        if not ok_col:
            return
        self._last_jump_row_1b = int(row_1b)
        self._last_jump_col_1b = int(col_1b)
        y = int(row_1b - 1)
        x = int(col_1b - 1)
        self._focus_pixel(viewer_id, x, y)

    def _focus_pixel(self, viewer_id: int, x: int, y: int) -> None:
        """聚焦像素并同步更新标记与时序曲线。"""
        shape = self._get_current_image_shape_for_viewer(viewer_id)
        if shape is None:
            QMessageBox.warning(self, "提示", "当前无法定位像素。")
            return
        height, width = shape
        if not (0 <= x < width and 0 <= y < height):
            QMessageBox.warning(self, "提示", f"像素越界：行范围 1-{height}，列范围 1-{width}。")
            return

        viewer = getattr(self, f'image_viewer_{viewer_id}', None)
        if viewer is None:
            return

        self._set_active_render_viewer(viewer_id)
        self.selected_viewer_id = viewer_id
        self.selected_pixel = (int(x), int(y))

        current_index = getattr(self, f'current_image_index_{viewer_id}', 0)
        lon, lat = self._get_pixel_lonlat(current_index, int(x), int(y))
        if lon is not None and lat is not None:
            self.selected_geo = (lon, lat)
            self.pixel_info_label.setText(
                f"选中像素: ({x + 1}, {y + 1}) | 地理坐标: ({lon:.6f}, {lat:.6f})"
            )
        else:
            self.selected_geo = None
            self.pixel_info_label.setText(f"选中像素: ({x + 1}, {y + 1})")

        center = viewer.image_to_view_point(float(x) + 0.5, float(y) + 0.5)
        scene_rect = QRectF(viewer._current_scene_rect())
        if not scene_rect.isNull():
            pixel_size_x = abs(scene_rect.width()) / max(float(width), 1.0)
            pixel_size_y = abs(scene_rect.height()) / max(float(height), 1.0)
            span_px_w = max(32.0, min(float(width), float(width) * 0.1))
            span_px_h = max(32.0, min(float(height), float(height) * 0.1))
            half_w = max(pixel_size_x * span_px_w / 2.0, pixel_size_x * 4.0)
            half_h = max(pixel_size_y * span_px_h / 2.0, pixel_size_y * 4.0)
            x0 = max(scene_rect.left(), float(center.x()) - half_w)
            x1 = min(scene_rect.right(), float(center.x()) + half_w)
            y0 = max(scene_rect.top(), float(center.y()) - half_h)
            y1 = min(scene_rect.bottom(), float(center.y()) + half_h)
            if x1 <= x0 or y1 <= y0:
                x0 = float(center.x()) - half_w
                x1 = float(center.x()) + half_w
                y0 = float(center.y()) - half_h
                y1 = float(center.y()) + half_h
            viewer.view_box.setRange(xRange=(x0, x1), yRange=(y0, y1), padding=0.02)

        self._sync_selected_pixel_markers()
        self.update_time_series_plot()

    def on_image_selector_changed(self, viewer_id, combo_index):
        """通过日期/文件名下拉框切换影像。"""
        if combo_index < 0 or self.image_count == 0:
            return
        image_select_combo = getattr(self, f'image_select_combo_{viewer_id}')
        target_index = image_select_combo.currentData()
        if target_index is None:
            target_index = combo_index
        current_index = getattr(self, f'current_image_index_{viewer_id}')
        if target_index == current_index:
            return
        self._set_viewer_has_image(viewer_id, True, index=target_index)
        setattr(self, f'current_image_index_{viewer_id}', target_index)
        slider = getattr(self, f'image_slider_{viewer_id}')
        slider.blockSignals(True)
        slider.setValue(target_index)
        slider.blockSignals(False)
        self.show_image(viewer_id, reset_view=False)
    
    def _get_image_data(self, index):
        """按需获取指定索引的显示图像数据（可能来自金字塔）
        
        Args:
            index: 图像索引
            
        Returns:
            tuple: (图像数据数组, 原始尺寸(width, height))，失败返回(None, None)
        """
        if index < 0 or index >= self.image_count:
            return None, None
        entry = self._get_layer_entry(index)
        if entry is None:
            return None, None

        if entry.source_kind == 'h5' and entry.frame_index is not None:
            image_data, original_size, _factor, _mode = read_h5_timeseries_frame_pyramid_display(
                entry.source_path,
                int(entry.frame_index),
                self.pyramid_threshold_mb,
            )
        elif entry.source_kind == 'gamma':
            image_data, _, original_size = self._read_gamma_pyramid_display(entry.source_path)
        else:
            image_data, _, original_size = self._read_image_pyramid_display(entry.source_path)
        
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
                db_data = np.zeros_like(data_copy, dtype=np.float32)
                valid_mask = ~nodata_mask
                db_data[valid_mask] = 10 * np.log10(data_copy[valid_mask])
                image_data = db_data.astype(np.float32)
            else:
                # 非GAMMA数据，正常转换
                min_positive = np.min(data_copy[data_copy > 0]) if np.any(data_copy > 0) else 1e-10
                data_copy[data_copy <= 0] = min_positive
                image_data = (10 * np.log10(data_copy)).astype(np.float32)
        
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
        
        image_source = self._get_image_source(index)
        if image_source is not None:
            image_data = image_source.read_window_native(0, 0, 1, 1)
            original_size = None
        else:
            image_data, original_size = self._get_image_data(index)
        
        # 更新缓存
        setattr(self, f'_cached_image_{viewer_id}', image_data)
        setattr(self, f'_cached_index_{viewer_id}', index)
        setattr(self, f'_cached_original_size_{viewer_id}', original_size)
        setattr(self, f'_cached_source_{viewer_id}', image_source)
        
        return image_data, original_size

    def _convert_block_to_db(self, block):
        data_copy = np.asarray(block).copy()
        nodata_value = self.nodata_value
        if self.is_gamma_timeseries or nodata_value == 0:
            nodata_mask = (data_copy == 0)
            min_positive = np.min(data_copy[data_copy > 0]) if np.any(data_copy > 0) else 1e-10
            data_copy[(data_copy <= 0) & ~nodata_mask] = min_positive
            db_data = np.zeros_like(data_copy, dtype=np.float32)
            valid_mask = ~nodata_mask
            db_data[valid_mask] = 10 * np.log10(data_copy[valid_mask])
            return db_data
        min_positive = np.min(data_copy[data_copy > 0]) if np.any(data_copy > 0) else 1e-10
        data_copy[data_copy <= 0] = min_positive
        return (10 * np.log10(data_copy)).astype(np.float32)

    def _get_image_source(self, index):
        try:
            entry = self._get_layer_entry(index)
            if entry is None:
                return None
            if entry.source_kind == 'h5':
                return None
            source = entry.source
            if source is None:
                if entry.source_kind == 'gamma':
                    source = GammaVrtRasterSource(
                        entry.source_path,
                        self.gamma_width,
                        self.gamma_height,
                        self.gamma_format,
                        self.pyramid_threshold_mb,
                    )
                else:
                    source = open_raster_source(entry.source_path, pyramid_threshold_mb=self.pyramid_threshold_mb)
                entry.source = source
            if self._converted_to_db:
                cache_path = write_derived_raster_cache(
                    source,
                    f"db10_{entry.source_key}",
                    self._convert_block_to_db,
                    pyramid_threshold_mb=self.pyramid_threshold_mb,
                )
                return GdalRasterSource(str(cache_path), source_path=source.metadata().path, pyramid_threshold_mb=self.pyramid_threshold_mb)
            return source
        except Exception:
            return None
        return None
    
    def show_image(self, viewer_id, reset_view=False):
        """显示指定查看器的当前图像
        
        Args:
            viewer_id: 查看器ID（1或2）
            reset_view: 是否重置为适配全图视角
        """
        if self.image_count == 0 or not self._viewer_has_image.get(viewer_id, False):
            return
        
        current_index = getattr(self, f'current_image_index_{viewer_id}')
        entry = self._get_layer_entry(current_index)
        if entry is None:
            return
        self._show_loading_indicator(
            f"正在加载图像...\n窗口{viewer_id} 第 {current_index + 1}/{self.image_count} 张"
        )
        viewer = getattr(self, f'image_viewer_{viewer_id}')
        slider = getattr(self, f'image_slider_{viewer_id}')
        index_label = getattr(self, f'image_index_label_{viewer_id}')
        info_label = getattr(self, f'image_info_label_{viewer_id}')
        
        previous_view_state = None if reset_view else viewer.capture_view_state()
        previous_sync_state = bool(getattr(viewer, "is_syncing", False))
        viewer.is_syncing = True

        try:
            # 按需获取图像数据（包含原始尺寸）
            current_data, original_size = self._get_cached_image(viewer_id, current_index)
            
            if current_data is None:
                info_label.setText("图像加载失败")
                return
            
            self._configure_viewer_scene_mapping(viewer, current_index)

            image_source = getattr(self, f'_cached_source_{viewer_id}', None)
            current_metadata = self._get_image_metadata(current_index)
            current_nodata = self._get_effective_nodata_for_index(current_index)
            viewer.nodata_value = current_nodata
            if current_metadata is not None:
                viewer.set_geotransform(current_metadata.get('geotransform'), current_metadata.get('projection'))
            else:
                viewer.set_geotransform(self.geotransform, self.projection)
            if image_source is not None:
                band_count = int(image_source.metadata().band_count or 1)
            else:
                band_count = int(current_data.shape[2]) if current_data.ndim == 3 else 1
            settings_for_viewer = self._normalized_settings_for_band_count(
                self._viewer_render_settings.get(viewer_id),
                band_count,
            )
            self._viewer_render_settings[viewer_id] = copy.deepcopy(settings_for_viewer)
            viewer.prime_render_settings(settings_for_viewer)
            viewer.set_colormap(self._viewer_colormaps.get(viewer_id, "gray"))

            if image_source is not None:
                viewer.set_raster_source(image_source, reset_view=reset_view, nodata_value=current_nodata)
            else:
                # 更新图像查看器（传递原始尺寸用于坐标映射）
                viewer.set_raster_array(current_data, original_size=original_size)
            
            # 首次加载或明确要求时才适配全图；切图时保留当前视角
            if reset_view:
                viewer.fit_in_view(delayed=True)
            else:
                viewer.restore_view_state(previous_view_state)
            if hasattr(self, "render_sidebar_controller"):
                self.render_sidebar_controller.refresh()
            self._refresh_colorbar_range(viewer_id)
            self._sync_selected_pixel_markers()
            
            # 更新滑块
            slider.blockSignals(True)
            slider.setValue(current_index)
            slider.blockSignals(False)

            image_select_combo = getattr(self, f'image_select_combo_{viewer_id}', None)
            if image_select_combo is not None:
                image_select_combo.blockSignals(True)
                image_select_combo.setCurrentIndex(current_index)
                image_select_combo.blockSignals(False)
            
            # 更新索引标签
            index_label.setText(f"{current_index + 1}/{self.image_count}")
            
            # 更新图像信息（显示原始尺寸）
            file_name = entry.display_name
            display_shape = current_data.shape
            if image_source is not None:
                meta = image_source.metadata()
                if meta.band_count == 1:
                    info = f"{file_name} | 尺寸: {meta.width}x{meta.height} | 单波段"
                else:
                    info = f"{file_name} | 尺寸: {meta.width}x{meta.height} | {meta.band_count}波段"
            elif original_size:
                orig_w, orig_h = original_size
                if display_shape[0] != orig_h or display_shape[1] != orig_w:
                    # 显示来自金字塔/overview的预览尺寸
                    if current_data.ndim == 2:
                        info = f"{file_name} | 原始: {orig_w}x{orig_h} | 显示: {display_shape[1]}x{display_shape[0]} | 单波段"
                    elif current_data.ndim == 3:
                        info = f"{file_name} | 原始: {orig_w}x{orig_h} | 显示: {display_shape[1]}x{display_shape[0]} | {display_shape[2]}波段"
                    else:
                        info = f"{file_name} | 尺寸: {display_shape}"
                else:
                    # 显示尺寸与原始尺寸一致
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
            
            info += f" | 金字塔阈值: {self.pyramid_threshold_mb} MB"
            if self._converted_to_db:
                info += " | dB"
            info_label.setText(info)
        finally:
            viewer.is_syncing = previous_sync_state
            # 如果已选择像素，更新曲线高亮
            if self.selected_pixel:
                self.update_time_series_plot()
            self._hide_loading_indicator()
    
    def open_folder(self, folder: str | None = None):
        """打开图像文件夹"""
        # 读取上次打开的路径
        settings = get_settings()
        last_folder = settings.value("last_folder_path", "")

        if not folder:
            folder = QFileDialog.getExistingDirectory(self, "选择图像文件夹", last_folder)
        if not folder:
            return
        
        # 保存当前路径
        settings.setValue("last_folder_path", folder)
        
        try:
            self._show_loading_indicator(f"正在扫描图像文件夹...\n{os.path.basename(folder)}")
            # 查找支持的图像文件
            supported_extensions = ['.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.grd', '.nc']
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
            self._show_loading_indicator(f"正在加载图像文件夹...\n{os.path.basename(folder)}")
            self.load_images(files)
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开文件夹失败: {str(e)}")
            traceback.print_exc()
        finally:
            self._hide_loading_indicator()
    
    def open_h5_timeseries(self, file_path: str | None = None):
        """打开h5时序数据"""
        # 读取上次打开的路径
        settings = get_settings()
        last_folder = settings.value("last_h5_path", "")
        
        if not file_path:
            file_path, _ = QFileDialog.getOpenFileName(
                self, "选择h5时序数据文件", last_folder, "HDF5/NetCDF Files (*.h5 *.hdf5 *.nc);;All Files (*)")
        
        if not file_path:
            return
        
        # 保存当前路径
        settings.setValue("last_h5_path", os.path.dirname(file_path))
        
        try:
            self._loading_new_series = True
            self._show_loading_indicator(f"正在分析 h5 时序数据...\n{os.path.basename(file_path)}")
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
            trimmed_dates = date_list[start_index:] if start_index > 0 else date_list
            
            if start_index > 0:
                QMessageBox.information(self, "提示", "检测到第一帧数据全为0，已自动跳过")
            
            # 设置按需加载相关属性
            self.data_source_type = 'h5'
            self.h5_file_path = file_path
            self.h5_start_index = start_index
            self._nodata_user_locked = False
            self.is_gamma_timeseries = False
            
            # 重置转换标志
            self._converted_to_db = False
            
            # 清空缓存
            self._clear_cached_images()
            self._clear_selected_pixel_state()
            self.time_series_layers = self._build_h5_layer_entries(
                file_path,
                trimmed_dates,
                start_index,
                num_dates,
                width,
                height,
            )
            self._sync_derived_series_state()
            
            # 更新UI
            self._set_series_status_text(f"已加载 {self.image_count} 张时序影像")
            
            # 更新两个窗口的控件
            self._refresh_navigation_controls()
            
            # 设置默认的彩色colormap
            self._reset_render_controls_for_new_series(1, 'jet')
            
            # 设置默认Nodata值为0（h5数据）。这里不立即触发 viewer 重绘，
            # 避免旧的 TIFF 源被新的 H5 colormap/nodata 短暂重渲染。
            self.nodata_value = 0
            self.image_viewer_1.nodata_value = 0
            self.image_viewer_2.nodata_value = 0
            
            # 显示第一张和第二张图像
            self._show_loading_indicator(f"正在加载 h5 时序图像...\n{os.path.basename(file_path)}")
            self.current_image_index_1 = 0
            self.current_image_index_2 = min(1, self.image_count - 1)
            self._set_viewer_has_image(1, True, index=self.current_image_index_1)
            self._set_viewer_has_image(2, True, index=self.current_image_index_2)
            self._get_cached_image(1, self.current_image_index_1)
            self._update_image_stats_to_render_settings()
            self.show_image(1, reset_view=True)
            self.show_image(2, reset_view=True)
            self._apply_render_settings_update()
            
            # 启用转dB按钮
            self._sync_db_toggle_widgets()
            
            QMessageBox.information(self, "成功", 
                                  f"成功加载h5时序数据！\n" +
                                  f"影像数量: {self.image_count}\n" +
                                  f"影像尺寸: {width} x {height}\n" +
                                  f"日期范围: {self.date_list[0]} 至 {self.date_list[-1]}")
                
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开h5文件失败: {str(e)}")
            traceback.print_exc()
        finally:
            self._loading_new_series = False
            self._hide_loading_indicator()
    
    def load_images(self, file_list, *, target_viewer_id: int | None = None, append: bool = False):
        """加载图像列表（按需加载模式，只读取第一张获取元信息）"""
        if not file_list:
            return

        if append:
            self._append_dropped_images(file_list, target_viewer_id or 1)
            return

        file_list, duplicate_count = self._dedupe_file_paths(file_list)
        if not file_list:
            if duplicate_count > 0:
                QMessageBox.information(self, "提示", "导入的数据都已存在，未重复导入。")
            return
        
        try:
            self._loading_new_series = True
            # 清空之前的数据
            self.time_series_layers = []
            self.image_files = []
            self.image_metadata = []
            self.date_list = []
            self._clear_selected_pixel_state()
            self.selected_geo = None
            self.selected_viewer_id = None
            self.nodata_value = None
            self._nodata_user_locked = False
            self.shared_scene_rect = None
            self.is_gamma_timeseries = False
            
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
            layer_entries: list[TimeSeriesLayerEntry] = []

            for file_path in file_list:
                if not os.path.exists(file_path):
                    unreadable_files.append(f"{os.path.basename(file_path)}: 文件不存在")
                    continue

                try:
                    entry = self._build_file_layer_entry(file_path)
                    if entry is None:
                        unreadable_files.append(f"{os.path.basename(file_path)}: 元数据读取失败")
                        continue
                    layer_entries.append(entry)
                except Exception as e:
                    unreadable_files.append(f"{os.path.basename(file_path)}: {str(e)}")
                    continue

            if unreadable_files:
                message = "以下文件读取失败，已跳过：\n" + "\n".join(unreadable_files[:10])
                if len(unreadable_files) > 10:
                    message += f"\n... 还有 {len(unreadable_files) - 10} 个文件"
                QMessageBox.warning(self, "警告", message)

            if duplicate_count > 0:
                QMessageBox.information(self, "提示", f"已按完整路径去重，跳过 {duplicate_count} 个重复文件。")

            if not layer_entries:
                QMessageBox.critical(self, "错误", "没有成功加载任何图像！")
                return

            reverse = self.sort_order_combo.currentIndex() == 1
            layer_entries.sort(key=lambda item: item.display_name.lower(), reverse=reverse)
            if not self._apply_folder_series_state(
                layer_entries,
                clear_selection=False,
                reset_render=False,
            ):
                return
            band_count = layer_entries[0].metadata.get('band_count') or 1
            
            # 更新UI
            self._set_series_status_text(f"已加载 {self.image_count} 张图像")
            
            # 更新两个窗口的控件
            self._refresh_navigation_controls()

            self._reset_render_controls_for_new_series(band_count, 'gray')
            
            # 显示第一张和第二张图像
            self.current_image_index_1 = 0
            self.current_image_index_2 = min(1, self.image_count - 1)
            self._set_viewer_has_image(1, True, index=self.current_image_index_1)
            self._set_viewer_has_image(2, True, index=self.current_image_index_2)
            self._get_cached_image(1, self.current_image_index_1)
            self._update_image_stats_to_render_settings()
            self.show_image(1, reset_view=True)
            self.show_image(2, reset_view=True)
            self._apply_render_settings_update()
            
            # 启用转dB按钮
            self._sync_db_toggle_widgets()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载图像失败: {str(e)}")
            traceback.print_exc()
        finally:
            self._loading_new_series = False
    
    def open_gamma_folder(self, folder: str | None = None):
        """打开GAMMA二进制文件时序文件夹"""
        settings = get_settings()
        last_folder = settings.value("last_gamma_folder_path", "")
        last_format = settings.value("last_gamma_format", "float32")
        
        if not folder:
            folder = QFileDialog.getExistingDirectory(self, "选择GAMMA时序文件夹", last_folder)
        if not folder:
            return
        
        settings.setValue("last_gamma_folder_path", folder)
        
        try:
            self._loading_new_series = True
            self._show_loading_indicator(f"正在分析 GAMMA 时序数据...\n{os.path.basename(folder)}")
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
            self._nodata_user_locked = False
            
            # 重置转换标志
            self._converted_to_db = False
            
            # 清空缓存
            self._clear_cached_images()
            self._clear_selected_pixel_state()
            valid_files.sort()
            self.time_series_layers = self._build_gamma_layer_entries(valid_files, gamma_format, width, height)
            self._sync_derived_series_state()
            
            # 设置默认Nodata值
            self.nodata_value = 0
            self.image_viewer_1.set_nodata_value(0)
            self.image_viewer_2.set_nodata_value(0)
            
            # 更新UI
            self._set_series_status_text(f"已加载 {self.image_count} 张GAMMA时序影像")
            
            # 更新两个窗口的控件
            self._refresh_navigation_controls()
            
            # 设置默认colormap
            is_complex = gamma_format.startswith('cpx')
            if is_complex:
                self._reset_render_controls_for_new_series(1, 'hsv')
            else:
                self._reset_render_controls_for_new_series(1, 'gray')
            
            # 显示第一张和第二张图像
            self._show_loading_indicator(f"正在加载 GAMMA 时序图像...\n{os.path.basename(folder)}")
            self.current_image_index_1 = 0
            self.current_image_index_2 = min(1, self.image_count - 1)
            self._set_viewer_has_image(1, True, index=self.current_image_index_1)
            self._set_viewer_has_image(2, True, index=self.current_image_index_2)
            self._get_cached_image(1, self.current_image_index_1)
            self._update_image_stats_to_render_settings()
            self.show_image(1, reset_view=True)
            self.show_image(2, reset_view=True)
            self._apply_render_settings_update()
            
            # 启用转dB按钮
            self._sync_db_toggle_widgets()
            
            QMessageBox.information(self, "成功", 
                f"成功加载GAMMA时序数据！\n" +
                f"文件数量: {self.image_count}\n" +
                f"尺寸: {width} x {height}\n" +
                f"格式: {gamma_format}")
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开GAMMA文件夹失败: {str(e)}")
            traceback.print_exc()
        finally:
            self._loading_new_series = False
            self._hide_loading_indicator()
    
    def _read_gamma_pyramid_display(self, file_path):
        """
        读取GAMMA二进制文件的显示预览。
        
        Returns:
            tuple: (图像数据, nodata值, 原始尺寸) 或 (None, None, None)
        """
        if not self.is_gamma_timeseries:
            return None, None, None
        
        try:
            data, _nodata, original_size, _factor, _mode = read_gamma_pyramid_display(
                file_path, 
                self.gamma_width, 
                self.gamma_height, 
                self.gamma_format,
                self.pyramid_threshold_mb,
            )
            
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
            
            if ext in ['.tif', '.tiff', '.grd', '.nc']:
                # 使用image_io读取GDAL栅格
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
    
    def _read_image_pyramid_display(self, file_path):
        """
        使用金字塔读取图像显示预览。
        """
        try:
            ext = os.path.splitext(file_path)[1].lower()
            
            if ext in ['.tif', '.tiff', '.grd', '.nc']:
                data, nodata, original_size, _factor, _mode = read_gdal_pyramid_display(
                    file_path, self.pyramid_threshold_mb
                )
                return data, nodata, original_size
            else:
                data, nodata, original_size, _factor, _mode = read_standard_pyramid_display(
                    file_path, self.pyramid_threshold_mb
                )
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
            print(f"金字塔读取图像失败 {file_path}: {e}")
            return None, None, None
    
    def sort_images(self):
        """排序图像"""
        if not self.time_series_layers:
            return

        current_keys = {}
        for viewer_id in (1, 2):
            if self._viewer_has_image.get(viewer_id, False):
                current_index = getattr(self, f'current_image_index_{viewer_id}', 0)
                entry = self._get_layer_entry(current_index)
                if entry is not None:
                    current_keys[viewer_id] = entry.source_key
        
        # 获取排序方式
        reverse = (self.sort_order_combo.currentIndex() == 1)
        
        # 创建索引列表并排序
        self.time_series_layers.sort(key=lambda entry: entry.display_name.lower(), reverse=reverse)
        self._sync_derived_series_state()
        
        # 清空缓存（因为索引顺序改变了）
        self._clear_cached_images()
        
        for viewer_id in (1, 2):
            target_key = current_keys.get(viewer_id)
            if target_key is not None:
                target_index = next((i for i, entry in enumerate(self.time_series_layers) if entry.source_key == target_key), None)
                if target_index is not None:
                    setattr(self, f'current_image_index_{viewer_id}', target_index)
                    continue
            elif self._viewer_has_image.get(viewer_id, False):
                fallback_index = 0 if viewer_id == 1 else min(1, self.image_count - 1)
                setattr(self, f'current_image_index_{viewer_id}', fallback_index)
        self._refresh_navigation_controls()
        if self._viewer_has_image.get(1, False):
            self._get_cached_image(1, self.current_image_index_1)
        self._refresh_viewers_after_series_change(reset_view=True)
    
    def on_pixel_clicked(self, viewer_id, x, y):
        """像素点击事件处理"""
        self._set_active_render_viewer(viewer_id)
        self.selected_viewer_id = viewer_id
        self.selected_pixel = (x, y)

        current_index = getattr(self, f'current_image_index_{viewer_id}', 0)
        lon, lat = self._get_pixel_lonlat(current_index, x, y)
        if lon is not None and lat is not None:
            self.selected_geo = (lon, lat)
            self.pixel_info_label.setText(
                f"选中像素: ({x + 1}, {y + 1}) | 地理坐标: ({lon:.6f}, {lat:.6f})"
            )
        else:
            self.selected_geo = None
            self.pixel_info_label.setText(f"选中像素: ({x + 1}, {y + 1})")

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
        entry = self._get_layer_entry(index)
        if entry is not None and entry.source_kind == 'h5':
            cache_key = (int(x), int(y))
            values = self._h5_pixel_series_cache.get(cache_key)
            if values is None:
                try:
                    values = read_h5_timeseries_pixel(
                        entry.source_path,
                        int(x),
                        int(y),
                        self.h5_start_index,
                    )
                except Exception as e:
                    print(f"读取h5像素值失败 (索引 {index}, 位置 ({x}, {y})): {e}")
                    values = None
                if values is not None:
                    self._h5_pixel_series_cache[cache_key] = values
            if values is None:
                return np.nan
            local_index = int(entry.frame_index or 0) - int(self.h5_start_index)
            if 0 <= local_index < len(values):
                return values[local_index]
            return np.nan

        try:
            source = self._get_image_source(index)
            if source is None:
                return np.nan
            value = source.read_pixel(int(x), int(y))
            return value if value is not None else np.nan
        except Exception as e:
            print(f"读取像素值失败 (索引 {index}, 位置 ({x}, {y})): {e}")
            return np.nan
    
    def _get_all_pixel_values_at(self, x, y, lonlat=None):
        """批量获取所有时序图像在指定位置的像素值
        
        Args:
            x: X坐标
            y: Y坐标
            
        Returns:
            像素值列表
        """
        if self.data_source_type == 'h5':
            cache_key = (int(x), int(y))
            values = self._h5_pixel_series_cache.get(cache_key)
            if values is None:
                try:
                    values = read_h5_timeseries_pixel(
                        self.h5_file_path,
                        int(x),
                        int(y),
                        self.h5_start_index,
                    )
                except Exception as e:
                    print(f"批量读取h5像素值失败 (位置 ({x}, {y})): {e}")
                    values = None
                if values is not None:
                    self._h5_pixel_series_cache[cache_key] = values
            values = [np.nan] * self.image_count if values is None else list(values[:self.image_count])
        else:
            values = []
            for i in range(self.image_count):
                resolved = self._resolve_pixel_for_layer(i, pixel=(x, y), lonlat=lonlat)
                if resolved is None:
                    values.append(np.nan)
                    continue
                query_x, query_y = resolved
                val = self._get_pixel_value_at(i, query_x, query_y)
                nodata_value = self._get_effective_nodata_for_index(i)
                values.append(self._mask_nodata_value(val, nodata_value))

        # 如果已转换为dB，h5分支仍是原始值，需要在这里转换；
        # 普通/GAMMA分支已经从派生dB source取值，不能重复转换。
        if self._converted_to_db and self.data_source_type == 'h5':
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

        # 提取时序数据
        time_indices = list(range(self.image_count))

        if self.selected_geo is not None:
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
            if self._viewer_has_image.get(1, False):
                ax.plot(self.current_image_index_1, values[self.current_image_index_1],
                       'ro', markersize=6, label='窗口1', zorder=10)
            if self._viewer_has_image.get(2, False):
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
            if self._viewer_has_image.get(1, False):
                ax.axvline(x=self.current_image_index_1, color='red', linestyle='--',
                          linewidth=2, label='窗口1', alpha=0.8)
            if self._viewer_has_image.get(2, False):
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
        
        if not self._plot_data_points or not self.time_series_layers:
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
            entry = self._get_layer_entry(nearest_index)
            file_name = entry.display_name if entry is not None else f"索引 {nearest_index}"
            
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
        
        if not self._plot_data_points or not self.time_series_layers:
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
        if self.data_source_type == 'h5':
            current_value = value if value is not None else np.nan
        else:
            current_value = self._get_pixel_value_at(current_index, x, y)

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

        if not self._viewer_has_image.get(other_viewer_id, False):
            other_pixel_label.setText("像素值: -")
            self._set_colorbar_current_value(other_colorbar, None)
            return

        other_index = getattr(self, f'current_image_index_{other_viewer_id}', 0)
        other_nodata = self._get_effective_nodata_for_index(other_index)

        resolved = self._resolve_pixel_for_layer(
            other_index,
            pixel=(x, y),
            lonlat=(current_lonlat[0], current_lonlat[1]) if current_lonlat[0] is not None and current_lonlat[1] is not None else None,
        )
        if resolved is None:
            other_pixel_label.setText("像素值: 越界")
            self._set_colorbar_current_value(other_colorbar, None)
            return
        other_x, other_y = resolved
        if self.data_source_type == 'h5':
            other_cached_image = getattr(self, f'_cached_image_{other_viewer_id}', None)
            if other_cached_image is not None and 0 <= other_y < other_cached_image.shape[0] and 0 <= other_x < other_cached_image.shape[1]:
                other_value = other_cached_image[other_y, other_x]
            else:
                other_value = np.nan
        else:
            other_value = self._get_pixel_value_at(other_index, other_x, other_y)
        other_lonlat = self._get_pixel_lonlat(other_index, other_x, other_y)
        other_pixel_label.setText(
            self._format_pixel_label_text(
                other_x, other_y, other_value, lonlat=other_lonlat, nodata_value=other_nodata
            )
        )
        self._set_colorbar_current_value(other_colorbar, other_value)
    
    def convert_to_db(self, show_message: bool = True, confirm: bool = True):
        """将显示的图像转换为dB (10*log10)"""
        if self.image_count == 0:
            return
        
        try:
            # 确认操作
            if confirm:
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
            self.show_image(1, reset_view=True)
            self.show_image(2, reset_view=True)
            self.image_viewer_1.fit_in_view(delayed=False)
            self.image_viewer_2.fit_in_view(delayed=False)
            self._update_image_stats_to_render_settings()
            self._apply_render_settings_update()
            
            # 如果有选中的像素，更新曲线
            if self.selected_pixel:
                self.update_time_series_plot()
            
            self._sync_db_toggle_widgets(True)
            if show_message:
                QMessageBox.information(self, "成功", "已转换为dB (10*log10)")
            
        except Exception as e:
            if show_message:
                QMessageBox.critical(self, "错误", f"转换为dB失败: {str(e)}")
            traceback.print_exc()
