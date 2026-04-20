'''
Author: Yibo Yuan 2633669459@qq.com
Description: 图像局部查看器对话框

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
import numpy as np
import h5py
from pathlib import Path
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                                QFileDialog, QLabel, QMessageBox, QSplitter,
                                QGroupBox, QButtonGroup, QRadioButton, QListWidget,
                                QDialogButtonBox, QInputDialog, QComboBox, QFrame,
                                QCheckBox, QApplication)
from PySide6.QtCore import Qt, QSettings, QTimer

# 配置文件路径
def get_settings():
    config_dir = Path.home() / ".toolbox"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "local_image_viewer.ini"
    return QSettings(str(config_file), QSettings.IniFormat)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import traceback

from src.widgets import InteractiveImageViewer, ColormapComboBox, RenderSettingsWidget, ColorbarWidget
from src.utils.gamma_file_process import (
    GAMMA_FORMATS,
    read_gamma_region,
    read_gamma_pixel,
    find_valid_par_for_binary,
    validate_dimensions,
    complex_to_phase,
    is_gamma_binary_file,
)
from src.utils.image_io import (
    calculate_hillshade,
    read_tiff_region,
    read_tiff_pixel,
    read_image_region,
    list_h5_datasets,
    read_h5_dataset,
    get_geotransform,
    pixel_to_lonlat,
)
from src.utils.display_pyramid import (
    DEFAULT_PYRAMID_THRESHOLD_MB,
    read_gamma_pyramid_display,
    read_gdal_pyramid_display,
    read_h5_dataset_pyramid_display,
    read_standard_pyramid_display,
    write_derived_raster_cache,
    write_full_derived_raster_cache,
)
from src.dialogs.gamma_dialogs import GammaSingleFileDialog
from src.rendering.sources import GdalRasterSource, GammaVrtRasterSource, H5DatasetRasterSource, HillshadeCompositeRasterSource
from src.rendering.sources import StandardImageSource


class LocalImageViewerDialog(QDialog):
    """图像局部查看器对话框"""
    
    def __init__(self, parent=None, pyramid_threshold_mb=DEFAULT_PYRAMID_THRESHOLD_MB):
        super().__init__(parent)
        
        # 显示金字塔配置
        self.pyramid_threshold_mb = pyramid_threshold_mb
        
        self.setWindowTitle("图像局部查看器")
        self.resize(1400, 800)
        
        # 图像数据
        self.image_data = None  # 显示用数据，可能来自 overview
        self.image_source = None
        self._base_render_source = None
        self._hillshade_cache_key = None
        self.image_file = None
        self.nodata_value = None
        self.polyline_path_points = None  # 存储折线路径上的所有点
        
        # 大图像显示相关
        self.original_width = None   # 原始图像宽度
        self.original_height = None  # 原始图像高度
        self.downsample_factor = 1   # 显示数据到原始图像的坐标比例
        self.is_tiff = False         # 是否为TIFF格式
        self.is_h5 = False           # 是否为H5/NC格式
        
        # GAMMA二进制文件相关
        self.is_gamma = False           # 是否为GAMMA二进制文件
        self.gamma_format = "float32"   # GAMMA数据格式
        self.gamma_par_file = None      # PAR文件路径
        
        # dB转换标志
        self._converted_to_db = False   # 是否已转换为dB

        # 拟合曲线显示开关（持久化）
        settings = get_settings()
        self.show_fit_curve = settings.value("show_fit_curve", True, type=bool)
        
        # 地理信息
        self.geotransform = None  # GDAL地理变换参数
        self.projection = None    # 投影信息
        
        # 创建UI
        self._create_ui()
        self._loading_title_text = self.windowTitle()
        self._render_update_timer = QTimer(self)
        self._render_update_timer.setSingleShot(True)
        self._render_update_timer.timeout.connect(self._apply_render_settings_update)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

    def on_theme_mode_changed(self, _mode: str) -> None:
        if hasattr(self, "image_viewer") and self.image_viewer is not None:
            self.image_viewer._apply_background_from_palette()
    
    def _update_render_settings_bands(self):
        """根据当前图像更新渲染设置的波段数和统计信息"""
        if self.image_data is not None:
            if self.image_data.ndim == 3:
                num_bands = self.image_data.shape[2]
            else:
                num_bands = 1
            self.render_settings.set_num_bands(num_bands)
            
            # 同时更新图像统计信息（最大最小值）
            self._update_image_stats_to_render_settings()

    def _compose_image_info(self, parts):
        info_parts = [str(part) for part in parts if part]
        if self.nodata_value is not None and not any(part.startswith("Nodata:") for part in info_parts):
            info_parts.append(f"Nodata: {self.nodata_value}")
        info_parts.append(f"金字塔阈值: {self.pyramid_threshold_mb} MB")
        if self._converted_to_db and "dB" not in info_parts:
            info_parts.append("dB")
        return " | ".join(info_parts)

    def _show_loading_indicator(self, message):
        self.setWindowTitle(f"{self._loading_title_text} - 加载中")
        if hasattr(self, 'image_info_label'):
            self.image_info_label.setText(message.replace("\n", " | "))
        QApplication.processEvents()

    def _hide_loading_indicator(self):
        self.setWindowTitle(self._loading_title_text)
        self._refresh_image_info_label()

    def _set_viewer_source_or_array(self, original_size=None):
        if self.image_source is not None:
            self._base_render_source = self.image_source
            self._hillshade_cache_key = None
            self.image_viewer.set_raster_source(self.image_source)
        else:
            self._base_render_source = None
            self._hillshade_cache_key = None
            self.image_viewer.set_raster_array(self.image_data, original_size=original_size)

    def _reset_render_controls_for_new_image(self):
        """新图像使用默认渲染参数，避免继承上一幅图的显示状态。"""
        if self.image_data is None:
            return
        num_bands = self.image_data.shape[2] if self.image_data.ndim == 3 else 1
        self.render_settings.reset_to_defaults(num_bands)
        self.colormap_combo.blockSignals(True)
        self.colormap_combo.setCurrentText("gray")
        self.colormap_combo.blockSignals(False)
        self.image_viewer.current_colormap = "gray"
        self.image_viewer.render_config.colormap_name = "gray"

    def _create_standard_source(self, file_path):
        try:
            return GdalRasterSource(file_path, pyramid_threshold_mb=self.pyramid_threshold_mb)
        except Exception:
            return StandardImageSource(file_path)

    def _refresh_image_info_label(self):
        if self.image_data is None or not self.image_file:
            return

        display_shape = self.image_data.shape

        if self.is_h5 and hasattr(self, 'h5_dataset_name'):
            info_parts = [f"{os.path.basename(self.image_file)} [{self.h5_dataset_name}]"]
            if getattr(self, 'h5_frame_index', None) is not None:
                info_parts.append(f"帧: {self.h5_frame_index}")
        elif self.is_gamma:
            info_parts = [os.path.basename(self.image_file), f"GAMMA {self.gamma_format}"]
            if self.gamma_format.startswith('cpx'):
                info_parts.append("显示: 相位")
            if self.gamma_par_file:
                info_parts.append(f"PAR: {os.path.basename(self.gamma_par_file)}")
        else:
            info_parts = [os.path.basename(self.image_file)]

        if self.downsample_factor > 1:
            info_parts.append(f"原始: {self.original_width}x{self.original_height}")
            info_parts.append(f"显示: {display_shape[1]}x{display_shape[0]} (1/{self.downsample_factor})")
        else:
            info_parts.append(f"尺寸: {self.original_width}x{self.original_height}")

        if not self.is_gamma:
            if self.image_data.ndim == 2:
                info_parts.append("单波段")
            elif self.image_data.ndim == 3:
                info_parts.append(f"{self.image_data.shape[2]}波段")

        self.image_info_label.setText(self._compose_image_info(info_parts))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            if self.image_viewer.cancel_active_drawing():
                event.accept()
                if self.image_data is not None:
                    self.show_image_histogram()
                return
            event.ignore()
            return
        super().keyPressEvent(event)

    def _convert_array_to_db(self, image_data):
        """将当前图像数据转换为 dB，保留 nodata 语义。"""
        data_copy = image_data.copy()

        if self.is_gamma or self.nodata_value == 0:
            nodata_mask = (data_copy == 0)
            min_positive = np.min(data_copy[data_copy > 0]) if np.any(data_copy > 0) else 1e-10
            data_copy[(data_copy <= 0) & ~nodata_mask] = min_positive
            db_data = np.zeros_like(data_copy, dtype=np.float32)
            valid_mask = ~nodata_mask
            db_data[valid_mask] = 10 * np.log10(data_copy[valid_mask])
        else:
            min_positive = np.min(data_copy[data_copy > 0]) if np.any(data_copy > 0) else 1e-10
            data_copy[data_copy <= 0] = min_positive
            db_data = 10 * np.log10(data_copy)

        if self.is_gamma:
            self.nodata_value = 0

        return db_data.astype(np.float32)

    def _convert_block_to_db(self, block):
        data_copy = np.asarray(block).copy()
        if self.is_gamma or self.nodata_value == 0:
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

    def _create_ui(self):
        """创建用户界面"""
        main_layout = QVBoxLayout(self)
        
        # 第一行：文件操作和绘制模式
        control_layout1 = QHBoxLayout()
        
        self.open_btn = QPushButton("打开图像")
        self.open_btn.clicked.connect(self.open_image)
        control_layout1.addWidget(self.open_btn)
        
        self.open_gamma_btn = QPushButton("打开GAMMA文件")
        self.open_gamma_btn.clicked.connect(self.open_gamma_file)
        control_layout1.addWidget(self.open_gamma_btn)
        
        self.open_h5_btn = QPushButton("打开h5文件")
        self.open_h5_btn.clicked.connect(self.open_h5_file)
        control_layout1.addWidget(self.open_h5_btn)
        
        self.set_nodata_btn = QPushButton("设置Nodata值")
        self.set_nodata_btn.clicked.connect(self.set_nodata_value)
        control_layout1.addWidget(self.set_nodata_btn)
        
        self.to_db_btn = QPushButton("转为dB")
        self.to_db_btn.clicked.connect(self.convert_to_db)
        self.to_db_btn.setEnabled(False)
        control_layout1.addWidget(self.to_db_btn)

        # 绘制模式选择
        control_layout1.addWidget(QLabel("绘制模式:"))
        self.mode_group = QButtonGroup(self)
        
        self.mode_none_radio = QRadioButton("浏览")
        self.mode_none_radio.setChecked(True)
        self.mode_none_radio.clicked.connect(self.on_mode_changed)
        self.mode_group.addButton(self.mode_none_radio, 0)
        control_layout1.addWidget(self.mode_none_radio)
        
        self.mode_rect_radio = QRadioButton("直方图")
        self.mode_rect_radio.clicked.connect(self.on_mode_changed)
        self.mode_group.addButton(self.mode_rect_radio, 1)
        control_layout1.addWidget(self.mode_rect_radio)
        
        self.mode_polyline_radio = QRadioButton("剖线图")
        self.mode_polyline_radio.clicked.connect(self.on_mode_changed)
        self.mode_group.addButton(self.mode_polyline_radio, 2)
        control_layout1.addWidget(self.mode_polyline_radio)
        
        self.clear_btn = QPushButton("清除绘制")
        self.clear_btn.clicked.connect(self.clear_drawing)
        control_layout1.addWidget(self.clear_btn)

        self.fit_curve_check = QCheckBox("拟合曲线")
        self.fit_curve_check.setChecked(self.show_fit_curve)
        self.fit_curve_check.toggled.connect(self.on_fit_curve_toggled)
        control_layout1.addWidget(self.fit_curve_check)
        
        control_layout1.addStretch()
        
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
        
        # 第二排：文件信息
        info_layout = QHBoxLayout()
        self.image_info_label = QLabel("未加载图像")
        info_layout.addWidget(self.image_info_label)
        info_layout.addStretch()
        main_layout.addLayout(info_layout)
        
        # 创建主分割器：左侧图像，右侧图表
        splitter = QSplitter(Qt.Horizontal)
        
        # ========== 左侧：图像查看区 ==========
        left_widget = QGroupBox("图像查看")
        left_main_layout = QVBoxLayout(left_widget)
        
        # 创建水平布局：图像查看器 + Colorbar
        image_layout = QHBoxLayout()
        
        # 图像查看器
        self.image_viewer = InteractiveImageViewer()
        self.image_viewer.files_dropped.connect(self._on_viewer_files_dropped)
        self.image_viewer.mouse_moved.connect(self.on_mouse_moved)
        self.image_viewer.rect_drawn.connect(self.on_rect_drawn)
        self.image_viewer.polyline_drawn.connect(self.on_polyline_drawn)
        self.image_viewer.polyline_hover.connect(self.on_polyline_hover)
        image_layout.addWidget(self.image_viewer)
        
        # Colorbar组件
        self.colorbar = ColorbarWidget()
        image_layout.addWidget(self.colorbar)
        
        left_main_layout.addLayout(image_layout)
        
        # 像素信息显示
        self.pixel_info_label = QLabel("像素信息: -")
        left_main_layout.addWidget(self.pixel_info_label)
        
        splitter.addWidget(left_widget)
        
        # ========== 右侧：图表显示区 ==========
        right_widget = QGroupBox("数据分析")
        right_layout = QVBoxLayout(right_widget)
        
        # Matplotlib图表
        self.figure = Figure(figsize=(6, 6))
        self.canvas = FigureCanvas(self.figure)
        self.toolbar = NavigationToolbar(self.canvas, self)
        
        right_layout.addWidget(self.toolbar)
        right_layout.addWidget(self.canvas)
        
        # 图表信息
        self.chart_info_label = QLabel("请绘制矩形或折线以查看数据")
        right_layout.addWidget(self.chart_info_label)
        
        splitter.addWidget(right_widget)
        
        # 设置分割器比例：图像窗口占4/5，图表窗口占1/5
        # 使用setSizes设置具体尺寸（像素）
        total_width = 1400
        splitter.setSizes([int(total_width * 0.6), int(total_width * 0.4)])
        
        main_layout.addWidget(splitter)
        
    def _on_viewer_files_dropped(self, paths: list[str]) -> None:
        mode, target = self._classify_drop_target(paths)
        if mode == "image":
            self.open_image(target)
            return
        if mode == "h5":
            self.open_h5_file(target)
            return
        if mode == "gamma":
            self.open_gamma_file(target)
            return
        QMessageBox.warning(self, "拖拽打开失败", "未识别拖入数据类型，无法打开。")

    def _classify_drop_target(self, paths: list[str]) -> tuple[str | None, str | None]:
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
            h5_file = next((entry for entry in file_entries if entry.suffix.lower() in h5_exts), None)
            if h5_file is not None:
                return "h5", str(h5_file)
            raster_file = next((entry for entry in file_entries if entry.suffix.lower() in raster_exts), None)
            if raster_file is not None:
                return "image", str(raster_file)
            gamma_file = next((entry for entry in file_entries if "par" not in entry.name.lower()), None)
            if gamma_file is not None:
                return "gamma", str(gamma_file)
            return None, None
        if files:
            first = files[0]
            ext = Path(first).suffix.lower()
            if ext in h5_exts:
                return "h5", first
            if ext in raster_exts:
                return "image", first
            return "gamma", first
        return None, None

    def open_image(self, file_path: str | None = None):
        """打开图像文件"""
        # 读取上次打开的路径
        settings = get_settings()
        last_path = settings.value("last_file_path", "")

        if not file_path:
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "打开图像文件",
                last_path,
                "图像文件 (*.tif *.tiff *.grd *.png *.jpg *.jpeg *.bmp *.h5 *.hdf5 *.nc);;所有文件 (*.*)",
                options=QFileDialog.Option.DontUseNativeDialog
            )
        
        if not file_path:
            return
        
        # 保存当前路径
        settings.setValue("last_file_path", os.path.dirname(file_path))
        
        try:
            self._show_loading_indicator(f"正在加载图像...\n{os.path.basename(file_path)}")
            self.image_file = file_path
            self.nodata_value = None
            self._converted_to_db = False
            ext = os.path.splitext(file_path)[1].lower()
            self.is_tiff = ext in ['.tif', '.tiff', '.grd']
            self.is_h5 = ext in ['.h5', '.hdf5', '.nc']
            
            if self.is_h5:
                # H5/NC文件需要选择数据集
                datasets = list_h5_datasets(file_path, min_ndim=2)
                if not datasets:
                    raise ValueError("HDF5/NC文件中没有找到2维及以上的数据集")
                
                # 弹出对话框让用户选择数据集
                from PySide6.QtWidgets import QDialog, QVBoxLayout, QListWidget, QDialogButtonBox
                dialog = QDialog(self)
                dialog.setWindowTitle("选择数据集")
                dialog.resize(500, 300)
                layout = QVBoxLayout()
                
                info_label = QLabel("请选择要打开的数据集：")
                layout.addWidget(info_label)
                
                list_widget = QListWidget()
                for name, shape_str, shape in datasets:
                    ndim = len(shape)
                    if ndim == 2:
                        desc = "2D图像"
                    elif ndim == 3:
                        desc = f"3D数据 ({shape[0]}景)"
                    else:
                        desc = f"{ndim}D数据"
                    list_widget.addItem(f"{name} {shape_str} - {desc}")
                
                # 支持双击直接打开
                list_widget.itemDoubleClicked.connect(dialog.accept)
                layout.addWidget(list_widget)
                
                buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
                buttons.accepted.connect(dialog.accept)
                buttons.rejected.connect(dialog.reject)
                layout.addWidget(buttons)
                
                dialog.setLayout(layout)
                
                if dialog.exec() != QDialog.Accepted or list_widget.currentRow() < 0:
                    return
                
                # 获取选中的数据集
                selected_idx = list_widget.currentRow()
                selected_dataset = datasets[selected_idx][0]  # 名称
                selected_shape = datasets[selected_idx][2]    # 形状元组
                selected_ndim = len(selected_shape)
                
                # 根据维度处理
                if selected_ndim == 2:
                    # 2D数据，直接打开
                    self.h5_dataset_name = selected_dataset
                    self.h5_frame_index = None
                    
                    self.image_data, original_size, self.downsample_factor, _mode = \
                        read_h5_dataset_pyramid_display(file_path, selected_dataset, threshold_mb=self.pyramid_threshold_mb)
                    
                    if self.image_data is None:
                        raise ValueError(f"无法读取数据集: {selected_dataset}")
                    self.original_width, self.original_height = original_size
                    self.image_source = H5DatasetRasterSource(file_path, selected_dataset, None, self.pyramid_threshold_mb)
                        
                elif selected_ndim == 3:
                    # 3D数据，让用户选择第几景
                    num_frames = selected_shape[0]
                    
                    # 判断是否是多波段图像（第一维很小，如RGB的3）
                    if num_frames <= 4 and num_frames < selected_shape[1] and num_frames < selected_shape[2]:
                        # 可能是多波段图像，直接打开
                        self.h5_dataset_name = selected_dataset
                        self.h5_frame_index = None
                        
                        self.image_data, original_size, self.downsample_factor, _mode = \
                            read_h5_dataset_pyramid_display(file_path, selected_dataset, threshold_mb=self.pyramid_threshold_mb)
                        
                        if self.image_data is None:
                            raise ValueError(f"无法读取数据集: {selected_dataset}")
                        self.original_width, self.original_height = original_size
                        self.image_source = H5DatasetRasterSource(file_path, selected_dataset, None, self.pyramid_threshold_mb)
                    else:
                        # 时序数据，让用户选择景
                        frame_idx, ok = QInputDialog.getInt(
                            self, "选择数据景", 
                            f"该数据集包含 {num_frames} 景数据\n请选择要打开的景（0-{num_frames-1}）：",
                            0, 0, num_frames-1, 1)
                        
                        if not ok:
                            return
                        
                        self.h5_dataset_name = selected_dataset
                        self.h5_frame_index = frame_idx
                        
                        self.image_data, original_size, self.downsample_factor, _mode = \
                            read_h5_dataset_pyramid_display(file_path, selected_dataset, frame_idx, self.pyramid_threshold_mb)
                        
                        if self.image_data is None:
                            raise ValueError(f"无法读取数据集: {selected_dataset} 的第 {frame_idx} 景")
                        self.original_width, self.original_height = original_size
                        self.image_source = H5DatasetRasterSource(file_path, selected_dataset, frame_idx, self.pyramid_threshold_mb)
                else:
                    # 其他维度不支持
                    raise ValueError(f"不支持的数据维度: {selected_ndim}D\n只支持2D图像或3D时序数据")
            elif self.is_tiff:
                self.image_source = GdalRasterSource(file_path, pyramid_threshold_mb=self.pyramid_threshold_mb)
                metadata = self.image_source.metadata()
                self.original_width, self.original_height = metadata.width, metadata.height
                self.nodata_value = metadata.nodata
                self.downsample_factor = 1
                self.image_data = self.image_source.read_window_native(0, 0, 1, 1)
            else:
                self.image_source = self._create_standard_source(file_path)
                metadata = self.image_source.metadata()
                self.original_width, self.original_height = metadata.width, metadata.height
                self.nodata_value = metadata.nodata
                self.downsample_factor = 1
                self.image_data = self.image_source.read_window_native(0, 0, 1, 1)

            self._reset_render_controls_for_new_image()
            if self.is_h5:
                self.colormap_combo.blockSignals(True)
                self.colormap_combo.setCurrentText('jet')
                self.colormap_combo.blockSignals(False)
                self.image_viewer.current_colormap = 'jet'
                self.image_viewer.render_config.colormap_name = 'jet'

            # 显示图像
            self._set_viewer_source_or_array(None)
            
            # H5文件默认使用jet colormap
            if self.is_h5:
                self.colormap_combo.setCurrentText('jet')
                self.image_viewer.set_colormap('jet')
            
            # 获取地理信息
            self.geotransform, self.projection = get_geotransform(file_path)
            
            # 设置地理信息到图像查看器（用于hillshade计算）
            self.image_viewer.set_geotransform(self.geotransform, self.projection)
            
            # 设置Nodata值到图像查看器
            self.image_viewer.set_nodata_value(self.nodata_value)
            
            # 确保图像居中显示（使用延迟模式）
            self.image_viewer.fit_in_view(delayed=True)
            
            # 更新信息
            info = self._compose_image_info([
                os.path.basename(file_path),
                f"尺寸: {self.original_width}x{self.original_height}",
            ])
            
            if self.image_data.ndim == 2:
                info += " | 单波段"
            elif self.image_data.ndim == 3:
                info += f" | {self.image_data.shape[2]}波段"
            
            self.image_info_label.setText(info)
            
            # 设置Nodata值到图像查看器
            self.image_viewer.set_nodata_value(self.nodata_value)
            
            # 更新渲染设置的波段数
            self._update_render_settings_bands()
            self._apply_render_settings_update()
            
            # 自动显示整个图像的直方图
            self.show_image_histogram()
            
            # 启用转dB按钮
            self.to_db_btn.setEnabled(True)
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开图像失败: {str(e)}")
            traceback.print_exc()
        finally:
            self._hide_loading_indicator()
    
    def _read_tiff_pyramid_display(self, file_path):
        """
        使用金字塔读取TIFF/GRD图像显示预览。
        """
        data, nodata, original_size, factor, _mode = read_gdal_pyramid_display(file_path, self.pyramid_threshold_mb)
        if data is None:
            raise IOError(f"无法打开TIFF文件: {file_path}")
        self.nodata_value = nodata
        return data, original_size, factor
    
    def _read_image_pyramid_display(self, file_path):
        """
        使用金字塔读取普通图像显示预览。
        """
        data, _nodata, original_size, factor, _mode = read_standard_pyramid_display(file_path, self.pyramid_threshold_mb)
        if data is None:
            raise IOError(f"无法打开图像文件: {file_path}")
        self.nodata_value = None
        return data, original_size, factor
    
    def _read_original_region(self, x1, y1, x2, y2):
        """
        从原始图像文件读取指定区域的数据（用于精确分析）
        坐标是原始图像坐标
        """
        if self.is_h5 and hasattr(self, 'h5_dataset_name'):
            # H5文件使用专门的区域读取函数
            with h5py.File(self.image_file, 'r') as h5f:
                if self.h5_dataset_name in h5f:
                    dataset = h5f[self.h5_dataset_name]
                    if dataset.ndim == 2:
                        return dataset[y1:y2, x1:x2].astype(np.float32)
                    elif dataset.ndim == 3:
                        if hasattr(self, 'h5_frame_index') and self.h5_frame_index is not None:
                            return dataset[self.h5_frame_index, y1:y2, x1:x2].astype(np.float32)
                        else:
                            # 多波段情况
                            return np.moveaxis(dataset[:, y1:y2, x1:x2], 0, -1).astype(np.float32)
            return None
        if self.image_file is None:
            return None
        
        # 确保坐标在有效范围内
        x1 = max(0, min(x1, self.original_width - 1))
        x2 = max(0, min(x2, self.original_width))
        y1 = max(0, min(y1, self.original_height - 1))
        y2 = max(0, min(y2, self.original_height))
        
        width = x2 - x1
        height = y2 - y1
        
        if width <= 0 or height <= 0:
            return None
        
        try:
            # GAMMA文件使用专用读取方法
            if self.is_gamma:
                return self._read_gamma_original_region(x1, y1, x2, y2)
            elif self.is_tiff:
                return read_tiff_region(self.image_file, x1, y1, x2, y2)
            else:
                return read_image_region(self.image_file, x1, y1, x2, y2)
        except Exception as e:
            traceback.print_exc()
            return None
    
    def _read_original_pixel(self, x, y):
        """
        从原始图像文件读取指定像素的值
        坐标是原始图像坐标
        """
        if self.image_file is None:
            return None
        
        # 确保坐标在有效范围内
        if x < 0 or x >= self.original_width or y < 0 or y >= self.original_height:
            return None
        
        try:
            # H5文件使用专门的读取方法
            if self.is_h5 and hasattr(self, 'h5_dataset_name'):
                with h5py.File(self.image_file, 'r') as h5f:
                    if self.h5_dataset_name in h5f:
                        dataset = h5f[self.h5_dataset_name]
                        if dataset.ndim == 2:
                            return dataset[y, x].astype(np.float32)
                        elif dataset.ndim == 3:
                            if hasattr(self, 'h5_frame_index') and self.h5_frame_index is not None:
                                return dataset[self.h5_frame_index, y, x].astype(np.float32)
                            else:
                                # 多波段情况
                                return dataset[:, y, x].astype(np.float32)
                return None
            # GAMMA文件使用专用读取方法
            elif self.is_gamma:
                return self._read_gamma_original_pixel(x, y)
            elif self.is_tiff:
                return read_tiff_pixel(self.image_file, x, y)
            else:
                # 对于非TIFF图像，读取单个像素区域
                region = read_image_region(self.image_file, x, y, x+1, y+1)
                if region is not None:
                    if region.ndim == 2:
                        return region[0, 0]
                    else:
                        return region[0, 0, :]
                return None
        except Exception as e:
            traceback.print_exc()
            return None

    def on_colormap_changed(self, colormap_name):
        """颜色映射改变"""
        # 跳过分隔符项（分隔符以"━"开头）
        if colormap_name.startswith('━'):
            return
        if self.image_data is not None:
            self._show_loading_indicator("正在重新渲染图像...")
        
        self.image_viewer.set_colormap(colormap_name)
        
        # 更新colorbar
        if hasattr(self, 'colorbar'):
            reversed = self.render_settings.reverse_check.isChecked() if hasattr(self, 'render_settings') else False
            self.colorbar.set_colormap(colormap_name, reversed)
        if self.image_data is not None:
            self._hide_loading_indicator()
    
    def on_render_settings_changed(self):
        """渲染设置变化时延迟更新图像显示，避免频繁重绘。"""
        if self.image_data is not None:
            self._show_loading_indicator("正在重新渲染图像...")
        self._render_update_timer.start(150)

    def _apply_render_settings_update(self):
        try:
            settings = self.render_settings.get_all_settings()
            if settings.get("display_mode") != "晕渲地貌":
                self._restore_base_render_source()
            if self.render_settings.is_auto_range():
                self._update_image_stats_to_render_settings()
                settings = self.render_settings.get_all_settings()
            viewer_settings = self._prepare_render_source_for_settings(settings)
            self.image_viewer.set_render_settings(viewer_settings)
            
            # 更新colorbar
            if hasattr(self, 'colorbar'):
                self.colorbar.set_range(settings['value_min'], settings['value_max'])
                self.colorbar.set_colormap(self.colormap_combo.currentText(), settings['colormap_reversed'])
        finally:
            self._hide_loading_indicator()

    def _restore_base_render_source(self) -> None:
        if self._base_render_source is not None and self.image_source is not self._base_render_source:
            self.image_source = self._base_render_source
            self._hillshade_cache_key = None
            self.image_data = self.image_source.read_window_native(0, 0, 1, 1)
            self.image_viewer.set_raster_source(self.image_source, reset_view=False)

    def _prepare_render_source_for_settings(self, settings: dict) -> dict:
        """为晕渲地貌这类派生图像准备缓存源，避免缩放/平移时反复计算。"""
        if settings.get("display_mode") != "晕渲地貌" or self.image_source is None:
            self._restore_base_render_source()
            return settings

        base_source = self._base_render_source or self.image_source
        params = settings.get("hillshade_params", {})
        gray_band = int(settings.get("gray_band", 1))
        key = (
            f"hillshade_full_v3_b{gray_band}_az{float(params.get('azimuth', 315.0)):.3f}"
            f"_alt{float(params.get('altitude', 45.0)):.3f}"
            f"_z{float(params.get('z_factor', 1.0)):.6f}"
        )
        if self.image_source is not base_source and self._hillshade_cache_key == key:
            return self._hillshade_view_settings(settings)

        meta = base_source.metadata()
        base_gt = meta.geotransform

        def _transform(full_array):
            arr = np.asarray(full_array)
            if arr.ndim == 3:
                band = min(max(gray_band, 1), arr.shape[2]) - 1
                arr = arr[:, :, band]
            return calculate_hillshade(
                arr,
                azimuth=float(params.get("azimuth", 315.0)),
                altitude=float(params.get("altitude", 45.0)),
                z_factor=float(params.get("z_factor", 1.0)),
                nodata_value=meta.nodata,
                geotransform=base_gt,
                projection=meta.crs_wkt,
            )

        cache_path = write_full_derived_raster_cache(
            base_source,
            key,
            _transform,
            output_band_count=1,
            invalidate_on_source_mtime=False,
            stable_cache_key=True,
            pyramid_threshold_mb=self.pyramid_threshold_mb,
        )
        hillshade_source = GdalRasterSource(str(cache_path), source_path=meta.path, pyramid_threshold_mb=self.pyramid_threshold_mb)
        self.image_source = HillshadeCompositeRasterSource(base_source, hillshade_source)
        self.image_data = self.image_source.read_window_native(0, 0, 1, 1)
        self._hillshade_cache_key = key
        self.image_viewer.set_raster_source(self.image_source, reset_view=False)
        return self._hillshade_view_settings(settings)

    def _hillshade_view_settings(self, settings: dict) -> dict:
        view_settings = dict(settings)
        view_settings["display_mode"] = "灰度"
        return view_settings
    
    def on_suggest_colormap(self, colormap_name):
        """接收建议的colormap并切换"""
        self.colormap_combo.setCurrentText(colormap_name)
    
    def _update_image_stats_to_render_settings(self):
        """从当前图像计算统计信息并更新到渲染设置"""
        if self.image_source is not None:
            settings = self.render_settings.get_all_settings()
            value_range = self.image_source.value_range_for_settings(settings)
            if value_range is not None:
                self.render_settings.set_image_stats(*value_range)
                if hasattr(self, 'colorbar'):
                    self.colorbar.set_range(*value_range)
                return

        if self.image_data is not None:
            arr = self.image_data
            # 创建有效掩码
            valid_mask = np.isfinite(arr)
            if self.nodata_value is not None:
                valid_mask = valid_mask & (arr != self.nodata_value)
            
            if np.any(valid_mask):
                valid_data = arr[valid_mask]
                min_val = float(np.min(valid_data))
                max_val = float(np.max(valid_data))
                self.render_settings.set_image_stats(min_val, max_val)
                
                # 更新colorbar范围
                if hasattr(self, 'colorbar'):
                    self.colorbar.set_range(min_val, max_val)
    
    def on_mode_changed(self):
        """绘制模式改变"""
        mode_id = self.mode_group.checkedId()
        self.image_viewer.set_draw_mode(mode_id)
        
        # 切换到折线模式时，重置完成状态，允许绘制新折线
        if mode_id == 2:  # MODE_POLYLINE
            self.image_viewer.polyline_completed = False
    
    def clear_drawing(self):
        """清除绘制"""
        self.image_viewer.clear_rect()
        self.image_viewer.clear_polyline()
        
        # 恢复显示整个图像的直方图
        if self.image_data is not None:
            self.show_image_histogram()
        else:
            self.figure.clear()
            self.canvas.draw()
            self.chart_info_label.setText("请绘制矩形或折线以查看数据")

    def on_fit_curve_toggled(self, checked):
        """拟合曲线开关切换"""
        self.show_fit_curve = checked
        settings = get_settings()
        settings.setValue("show_fit_curve", checked)
        self._refresh_analysis_chart()

    def _normalize_plot_values(self, values):
        """把 GDAL/PIL/HDF5 读出的标量或数组规范成绘图可识别的形状。"""
        normalized = []
        for value in values or []:
            if value is None:
                normalized.append(np.nan)
                continue
            arr = np.asarray(value)
            if arr.ndim == 0:
                normalized.append(float(arr))
            else:
                normalized.append(arr.astype(np.float64, copy=False).ravel())
        return normalized

    def _refresh_analysis_chart(self):
        """根据当前绘制状态刷新分析图"""
        if self.image_data is None:
            return

        mode_id = self.mode_group.checkedId() if hasattr(self, 'mode_group') else 0
        current_rect = getattr(self.image_viewer, 'current_rect', None)
        polyline_points = getattr(self.image_viewer, 'polyline_points', None)

        if mode_id == 1 and current_rect is not None:
            self.on_rect_drawn(None)
        elif mode_id == 2 and polyline_points and len(polyline_points) >= 2:
            self.on_polyline_drawn(None)
        else:
            self.show_image_histogram()
    
    def show_image_histogram(self):
        """显示整个图像的直方图"""
        if self.image_data is None:
            return
        
        try:
            # 准备数据列表
            data_list = []
            
            if self.image_data.ndim == 2:
                # 单波段图像
                flat_data = self.image_data.flatten()
                # 排除Nodata值
                if self.nodata_value is not None:
                    valid_data = flat_data[flat_data != self.nodata_value]
                else:
                    valid_data = flat_data
                data_list.append(valid_data)
                
            elif self.image_data.ndim == 3:
                # 多波段图像
                num_bands = self.image_data.shape[2]
                for band_idx in range(num_bands):
                    band_data = self.image_data[:, :, band_idx].flatten()
                    # 排除Nodata值
                    if self.nodata_value is not None:
                        valid_data = band_data[band_data != self.nodata_value]
                    else:
                        valid_data = band_data
                    data_list.append(valid_data)
            
            # 绘制直方图
            self.plot_histogram(data_list)
            self.chart_info_label.setText(f"整幅图像直方图: 共{sum(len(d) for d in data_list)}个像素")
            
        except Exception as e:
            print(f"显示直方图失败: {str(e)}")
            traceback.print_exc()
    
    def on_mouse_moved(self, x, y, value):
        """鼠标移动事件"""
        if value is not None:
            # 计算原始坐标
            if self.image_source is None and self.downsample_factor > 1:
                orig_x = int(x * self.downsample_factor)
                orig_y = int(y * self.downsample_factor)
                # 确保在有效范围内
                orig_x = min(orig_x, self.original_width - 1)
                orig_y = min(orig_y, self.original_height - 1)
                coord_str = f"原始坐标: ({orig_x}, {orig_y})"
            else:
                orig_x, orig_y = x, y
                coord_str = f"像素位置: ({x}, {y})"
            
            # 显示像素值
            if isinstance(value, (int, float, np.integer, np.floating)):
                base_text = f"{coord_str} | 值: {value:.6g}"
                # 更新colorbar当前值指示
                if hasattr(self, 'colorbar'):
                    if not (np.isnan(value) if isinstance(value, float) else False):
                        self.colorbar.set_current_value(float(value))
                    else:
                        self.colorbar.set_current_value(None)
            elif isinstance(value, np.ndarray):
                if value.ndim == 0:
                    base_text = f"{coord_str} | 值: {value:.6g}"
                else:
                    value_str = ", ".join([f"{v:.6g}" for v in value])
                    base_text = f"{coord_str} | 值: [{value_str}]"
                # RGB图像不显示colorbar指示
                if hasattr(self, 'colorbar'):
                    self.colorbar.set_current_value(None)
            
            # 如果有地理信息，添加经纬度
            if self.geotransform is not None:
                lon, lat = pixel_to_lonlat(orig_x, orig_y, self.geotransform, self.projection)
                if lon is not None and lat is not None:
                    base_text += f" | 经纬度: ({lon:.6f}, {lat:.6f})"
            
            self.pixel_info_label.setText(base_text)
        else:
            self.pixel_info_label.setText("像素信息: -")
            if hasattr(self, 'colorbar'):
                self.colorbar.set_current_value(None)
    
    def on_rect_drawn(self, rect):
        """矩形绘制完成"""
        try:
            # 获取当前矩形的坐标（显示坐标）
            current_rect = self.image_viewer.current_rect
            if current_rect is None:
                return
            
            # 计算原始图像坐标
            if self.image_source is None and self.downsample_factor > 1:
                # 将显示坐标转换为原始坐标
                x1 = int(current_rect.x() * self.downsample_factor)
                y1 = int(current_rect.y() * self.downsample_factor)
                x2 = int((current_rect.x() + current_rect.width()) * self.downsample_factor)
                y2 = int((current_rect.y() + current_rect.height()) * self.downsample_factor)
                
                # 从原始文件读取区域数据
                region_data = self._read_original_region(x1, y1, x2, y2)
                if region_data is None:
                    # 如果无法读取原始数据，使用显示数据
                    region_data = self.image_viewer.get_rect_region()
            else:
                # 显示尺寸与原始尺寸一致，直接使用显示数据
                region_data = self.image_viewer.get_rect_region()
            
            if region_data is None:
                return
            
            # 排除Nodata值
            if self.nodata_value is not None:
                mask = region_data != self.nodata_value
                if region_data.ndim == 3:
                    # 多波段，对每个波段应用mask
                    valid_data = []
                    for i in range(region_data.shape[2]):
                        band_data = region_data[:, :, i][mask[:, :, i]]
                        valid_data.append(band_data)
                else:
                    valid_data = [region_data[mask]]
            else:
                if region_data.ndim == 3:
                    valid_data = [region_data[:, :, i].flatten() for i in range(region_data.shape[2])]
                else:
                    valid_data = [region_data.flatten()]
            
            # 绘制直方图
            self.plot_histogram(valid_data)
            
        except Exception as e:
            QMessageBox.warning(self, "警告", f"绘制直方图失败: {str(e)}")
            traceback.print_exc()
    
    def on_polyline_drawn(self, points):
        """折线绘制完成"""
        try:
            # 获取折线路径上所有像素的显示坐标
            display_path_points, _ = self.image_viewer.get_polyline_path_values()
            if display_path_points is None or len(display_path_points) == 0:
                return
            
            # 根据显示比例计算原始坐标和获取原始像素值
            if self.image_source is None and self.downsample_factor > 1:
                # 将显示坐标转换为原始坐标
                original_path_points = []
                path_values = []
                
                for (dx, dy) in display_path_points:
                    # 转换到原始坐标
                    ox = int(dx * self.downsample_factor)
                    oy = int(dy * self.downsample_factor)
                    
                    # 确保坐标在有效范围内
                    ox = min(ox, self.original_width - 1)
                    oy = min(oy, self.original_height - 1)
                    
                    original_path_points.append((ox, oy))
                    
                    # 从原始文件读取像素值
                    value = self._read_original_pixel(ox, oy)
                    path_values.append(value if value is not None else np.nan)
                
                # 存储原始坐标用于悬停标记（但显示时仍使用显示坐标）
                self.polyline_path_points = display_path_points  # 保持显示坐标用于图像标记
                self.polyline_original_points = original_path_points  # 原始坐标用于显示
            else:
                # 显示尺寸与原始尺寸一致，直接使用
                _, path_values = self.image_viewer.get_polyline_path_values()
                self.polyline_path_points = display_path_points
                self.polyline_original_points = display_path_points
            
            # 排除Nodata值并绘制折线图
            self.plot_polyline(self._normalize_plot_values(path_values), self.polyline_original_points)
            
        except Exception as e:
            QMessageBox.warning(self, "警告", f"绘制折线图失败: {str(e)}")
            traceback.print_exc()
    
    def on_polyline_hover(self, idx):
        """折线悬停事件（从图像传来）"""
        # 更新折线图中的悬停标记
        if hasattr(self, 'hover_line') and self.hover_line:
            try:
                self.hover_line.set_xdata([idx, idx])
                self.hover_line.set_visible(True)
                self.canvas.draw_idle()
            except:
                pass

    def _ensure_chart_hover(self):
        if not hasattr(self, "_chart_hover_connection"):
            self._chart_hover_connection = self.canvas.mpl_connect('motion_notify_event', self.on_chart_mouse_move)
        self._chart_annotation = None
    
    def on_chart_mouse_move(self, event):
        """图表鼠标移动事件（从图表传来）"""
        if event.inaxes is None:
            # 鼠标不在图表坐标轴内，隐藏悬停标记
            self.image_viewer._hide_hover_marker()
            if hasattr(self, 'hover_line') and self.hover_line:
                self.hover_line.set_visible(False)
            return

        if event.xdata is None:
            self.canvas.draw_idle()
            return
        
        # 获取鼠标位置的x坐标（索引）
        x_pos = int(round(event.xdata))
        
        # 检查索引是否有效
        if hasattr(self, 'polyline_path_points') and self.polyline_path_points:
            if 0 <= x_pos < len(self.polyline_path_points):
                try:
                    # 获取对应的图像坐标
                    px, py = self.polyline_path_points[x_pos]
                    
                    # 在图像上显示标记
                    self.image_viewer._show_hover_marker_at(px, py)
                    
                    # 更新悬停线
                    if hasattr(self, 'hover_line') and self.hover_line:
                        self.hover_line.set_xdata([x_pos, x_pos])
                        self.hover_line.set_visible(True)
                        self.canvas.draw_idle()
                except Exception as e:
                    # 忽略错误，避免弹窗
                    pass
            else:
                # 索引超出范围，隐藏悬停标记
                self.image_viewer._hide_hover_marker()
                if hasattr(self, 'hover_line') and self.hover_line:
                    self.hover_line.set_visible(False)
                    self.canvas.draw_idle()
        else:
            # 没有折线数据，隐藏悬停标记
            self.image_viewer._hide_hover_marker()
            if hasattr(self, 'hover_line') and self.hover_line:
                self.hover_line.set_visible(False)
                self.canvas.draw_idle()

    def _compute_histogram_spec(self, values, bins=128, low_pct=1.0, high_pct=99.0):
        """参考 HSBA 直方图规格：固定 bins + 百分位范围。"""
        flat = np.asarray(values, dtype=np.float64).ravel()
        flat = flat[np.isfinite(flat)]
        if flat.size == 0:
            return None
        
        low = float(np.percentile(flat, low_pct))
        high = float(np.percentile(flat, high_pct))
        if not np.isfinite(low) or not np.isfinite(high) or low >= high:
            low = float(np.min(flat))
            high = float(np.max(flat))
        if not np.isfinite(low) or not np.isfinite(high) or low >= high:
            return None
        
        bin_edges = np.linspace(low, high, bins + 1, dtype=np.float64)
        bin_centers = 0.5 * (bin_edges[:-1] + bin_edges[1:])
        return {
            "bins": bins,
            "value_range": (low, high),
            "bin_edges": bin_edges,
            "bin_centers": bin_centers,
            "bin_width": float(bin_edges[1] - bin_edges[0]),
        }
    
    def _compute_histogram(self, values, spec):
        """在固定 bins/range 下计算直方图及密度。"""
        flat = np.asarray(values, dtype=np.float64).ravel()
        flat = flat[np.isfinite(flat)]
        if flat.size == 0:
            return None
        
        counts, _ = np.histogram(flat, bins=spec["bin_edges"])
        counts = counts.astype(np.float64)
        total = max(float(np.sum(counts)), 1.0)
        density = counts / (total * spec["bin_width"])
        return {
            "counts": counts,
            "density": density,
        }
    
    def _gaussian_kernel(self, sigma, radius=None):
        if sigma <= 0:
            return np.array([1.0], dtype=np.float64)
        if radius is None:
            radius = int(max(1, round(3.0 * sigma)))
        x = np.arange(-radius, radius + 1, dtype=np.float64)
        kernel = np.exp(-0.5 * (x / float(sigma)) ** 2)
        kernel_sum = float(np.sum(kernel))
        if kernel_sum > 0:
            kernel /= kernel_sum
        return kernel
    
    def _smooth_1d(self, y, sigma):
        """高斯平滑（参考 HSBA 的直方图平滑）。"""
        arr = np.asarray(y, dtype=np.float64)
        if arr.size == 0:
            return arr
        kernel = self._gaussian_kernel(sigma)
        pad = len(kernel) // 2
        padded = np.pad(arr, (pad, pad), mode="reflect")
        return np.convolve(padded, kernel, mode="valid")
    
    def _smooth_1d_with_nans(self, y, sigma):
        """在含 NaN 的序列上做高斯平滑。"""
        arr = np.asarray(y, dtype=np.float64)
        if arr.size == 0:
            return arr
        valid = np.isfinite(arr)
        if not np.any(valid):
            return np.full_like(arr, np.nan)
        kernel = self._gaussian_kernel(sigma)
        pad = len(kernel) // 2
        data = np.where(valid, arr, 0.0)
        weight = valid.astype(np.float64)
        data_padded = np.pad(data, (pad, pad), mode="reflect")
        weight_padded = np.pad(weight, (pad, pad), mode="reflect")
        smooth_data = np.convolve(data_padded, kernel, mode="valid")
        smooth_weight = np.convolve(weight_padded, kernel, mode="valid")
        with np.errstate(invalid="ignore", divide="ignore"):
            smooth = smooth_data / smooth_weight
        smooth[smooth_weight <= 1e-6] = np.nan
        return smooth
    
    def _fit_gmm_on_histogram(self, centers, counts, n_components=2, max_iter=200, tol=1e-5):
        """在固定直方图上做简单 GMM-EM 拟合（参考 HSBA 的思路）。"""
        x = np.asarray(centers, dtype=np.float64)
        w = np.asarray(counts, dtype=np.float64)
        if x.size == 0 or w.size == 0 or x.size != w.size:
            return None
        if np.sum(w) <= 0:
            return None
        
        # 量化初始化：分位数做均值，整体方差做初始 std
        total = float(np.sum(w))
        cdf = np.cumsum(w) / total
        quantiles = np.linspace(0.1, 0.9, n_components)
        means = np.interp(quantiles, cdf, x)
        std_global = float(np.sqrt(np.average((x - np.average(x, weights=w)) ** 2, weights=w)))
        stds = np.full(n_components, max(std_global, 1e-3), dtype=np.float64)
        weights = np.full(n_components, 1.0 / n_components, dtype=np.float64)
        
        prev_ll = -np.inf
        for _ in range(max_iter):
            # E-step
            pdfs = []
            for k in range(n_components):
                std = max(float(stds[k]), 1e-6)
                norm = 1.0 / (std * np.sqrt(2.0 * np.pi))
                pdf = norm * np.exp(-0.5 * ((x - means[k]) / std) ** 2)
                pdfs.append(weights[k] * pdf)
            pdfs = np.vstack(pdfs)  # (k, n)
            sum_pdfs = np.sum(pdfs, axis=0)
            sum_pdfs = np.maximum(sum_pdfs, 1e-12)
            resp = pdfs / sum_pdfs
            
            # M-step (weighted by counts)
            weighted_resp = resp * w
            nk = np.sum(weighted_resp, axis=1)
            nk = np.maximum(nk, 1e-12)
            weights = nk / total
            means = np.sum(weighted_resp * x, axis=1) / nk
            variances = np.sum(weighted_resp * (x - means[:, None]) ** 2, axis=1) / nk
            stds = np.sqrt(np.maximum(variances, 1e-6))
            
            # Log-likelihood
            ll = float(np.sum(w * np.log(sum_pdfs)))
            if abs(ll - prev_ll) < tol:
                break
            prev_ll = ll
        
        # 输出拟合曲线（密度）
        mixture = np.zeros_like(x, dtype=np.float64)
        for k in range(n_components):
            std = max(float(stds[k]), 1e-6)
            norm = 1.0 / (std * np.sqrt(2.0 * np.pi))
            mixture += weights[k] * norm * np.exp(-0.5 * ((x - means[k]) / std) ** 2)
        return {
            "weights": weights,
            "means": means,
            "stds": stds,
            "mixture": mixture,
        }
    
    def _select_gmm_fit(self, centers, counts, target_density):
        """在 2/3 峰中选择更接近直方图密度的拟合。"""
        best = None
        best_sse = None
        for n_components in (2, 3):
            if len(centers) < n_components * 4:
                continue
            fit = self._fit_gmm_on_histogram(centers, counts, n_components=n_components)
            if fit is None:
                continue
            sse = float(np.sum((fit["mixture"] - target_density) ** 2))
            if best_sse is None or sse < best_sse:
                best = fit
                best_sse = sse
        return best
    
    def plot_histogram(self, data_list):
        """绘制直方图（使用填充折线图）"""
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        self._chart_points = []
        
        # 绘制每个波段的直方图
        colors = ['red', 'green', 'blue', 'cyan', 'magenta', 'yellow']
        x_ranges = []
        
        for i, data in enumerate(data_list):
            if len(data) == 0:
                continue
            
            # 过滤NaN和inf值
            finite_data = data[np.isfinite(data)]
            if len(finite_data) == 0:
                continue
            
            color = colors[i % len(colors)]
            label = f'波段{i+1}' if len(data_list) > 1 else '像素值'
            
            # 参考 HSBA：固定 bins + 百分位范围，计算密度直方图
            spec = self._compute_histogram_spec(finite_data, bins=128, low_pct=1.0, high_pct=99.0)
            if spec is None:
                continue
            hist = self._compute_histogram(finite_data, spec)
            if hist is None:
                continue
            counts = hist["counts"]
            density = hist["density"]
            bin_centers = spec["bin_centers"]
            bin_width = spec["bin_width"]
            x_ranges.append(spec["value_range"])
            
            # 绘制密度直方图（更平滑）
            ax.bar(
                bin_centers,
                density,
                width=bin_width,
                color=color,
                alpha=0.55,
                label=f'{label}直方图'
            )
            self._chart_points.extend(
                (float(x), float(y), f"{label}: {x:.6g}, 密度 {y:.6g}")
                for x, y in zip(bin_centers, density, strict=False)
            )
            
            # 绘制平滑后的经验曲线
            smooth_density = self._smooth_1d(density, sigma=1.0)
            ax.plot(
                bin_centers,
                smooth_density,
                color=color,
                linewidth=1.6,
                alpha=0.9,
                label=f'{label}平滑'
            )
            
            # 拟合曲线只叠加平滑后的经验密度，不重新估计额外峰值，避免引入不存在的极小值。
            if self.show_fit_curve:
                ax.plot(
                    bin_centers,
                    smooth_density,
                    color='black',
                    linewidth=2.2,
                    alpha=0.9,
                    label=f'{label}拟合'
                )
        
        if x_ranges:
            x_min = min(r[0] for r in x_ranges)
            x_max = max(r[1] for r in x_ranges)
            if x_max > x_min:
                ax.set_xlim(x_min, x_max)
        
        ax.set_xlabel('像素值')
        ax.set_ylabel('密度')
        ax.set_title('矩形区域像素值直方图')
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend()
        ax.grid(True, alpha=0.3)
        
        self.figure.tight_layout()
        self.canvas.draw()
        self._ensure_chart_hover()
        
        if self.show_fit_curve:
            self.chart_info_label.setText(f"直方图(平滑+拟合): 共{sum(len(d) for d in data_list)}个像素")
        else:
            self.chart_info_label.setText(f"直方图(平滑): 共{sum(len(d) for d in data_list)}个像素")
    
    def plot_polyline(self, values, points):
        """绘制折线图（改进版：平滑曲线+折点标记+填充）"""
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        self._chart_points = []
        
        if not values:
            self.canvas.draw()
            self.chart_info_label.setText("折线图: 无有效数据")
            return
        
        indices = list(range(len(values)))
        
        # 获取原始折线折点位置
        if hasattr(self.image_viewer, 'polyline_points'):
            polyline_points = self.image_viewer.polyline_points
            # 找到折点在路径中的索引
            path_points, _ = self.image_viewer.get_polyline_path_values()
            corner_indices = []
            for corner_x, corner_y in polyline_points:
                for idx, (px, py) in enumerate(path_points):
                    if px == corner_x and py == corner_y:
                        corner_indices.append(idx)
                        break
        else:
            corner_indices = []
        
        # 检查数据类型
        if isinstance(values[0], (int, float, np.integer, np.floating)):
            # 单波段
            # 排除Nodata
            valid_indices = []
            valid_values = []
            for i, v in enumerate(values):
                if self.nodata_value is None or v != self.nodata_value:
                    valid_indices.append(i)
                    valid_values.append(v)
            
            # 绘制填充曲线
            ax.fill_between(valid_indices, valid_values, alpha=0.3, color='red', label='像素值')
            ax.plot(valid_indices, valid_values, color='red', linewidth=1)
            self._chart_points.extend(
                (float(idx), float(val), f"点 {idx}: {val:.6g}")
                for idx, val in zip(valid_indices, valid_values, strict=False)
            )
            
            # 平滑拟合曲线（参考 HSBA 的平滑思路）
            series = np.full(len(values), np.nan, dtype=np.float64)
            for idx, val in zip(valid_indices, valid_values, strict=False):
                series[idx] = float(val)
            smooth_series = self._smooth_1d_with_nans(series, sigma=2.0)
            smooth_valid = np.isfinite(smooth_series)
            if self.show_fit_curve and np.any(smooth_valid):
                ax.plot(
                    np.arange(len(values))[smooth_valid],
                    smooth_series[smooth_valid],
                    color='black',
                    linewidth=2.0,
                    alpha=0.9,
                    label='拟合'
                )
            
            # 标记折点
            corner_x = [i for i in corner_indices if i in valid_indices]
            corner_y = [valid_values[valid_indices.index(i)] for i in corner_x]
            ax.scatter(corner_x, corner_y, color='red', s=40, zorder=5, marker='o', 
                      edgecolors='darkred', linewidths=1.5, label='折点')
            
        elif isinstance(values[0], np.ndarray):
            # 多波段
            num_bands = len(values[0])
            colors = ['red', 'green', 'blue', 'cyan', 'magenta', 'yellow']
            
            for band_idx in range(num_bands):
                valid_indices = []
                valid_values = []
                
                for i, v in enumerate(values):
                    val = v[band_idx]
                    if self.nodata_value is None or val != self.nodata_value:
                        valid_indices.append(i)
                        valid_values.append(val)
                
                color = colors[band_idx % len(colors)]
                
                # 绘制填充曲线
                ax.fill_between(valid_indices, valid_values, alpha=0.2, color=color)
                ax.plot(valid_indices, valid_values, color=color, linewidth=1, 
                       label=f'波段{band_idx+1}')
                self._chart_points.extend(
                    (float(idx), float(val), f"点 {idx} 波段{band_idx+1}: {val:.6g}")
                    for idx, val in zip(valid_indices, valid_values, strict=False)
                )
                
                # 平滑拟合曲线
                series = np.full(len(values), np.nan, dtype=np.float64)
                for idx, val in zip(valid_indices, valid_values, strict=False):
                    series[idx] = float(val)
                smooth_series = self._smooth_1d_with_nans(series, sigma=2.0)
                smooth_valid = np.isfinite(smooth_series)
                if self.show_fit_curve and np.any(smooth_valid):
                    ax.plot(
                        np.arange(len(values))[smooth_valid],
                        smooth_series[smooth_valid],
                        color='black',
                        linewidth=2.0,
                        alpha=0.9,
                        label=f'波段{band_idx+1}拟合'
                    )
                
                # 标记折点
                corner_x = [i for i in corner_indices if i in valid_indices]
                corner_y = [valid_values[valid_indices.index(i)] for i in corner_x]
                ax.scatter(corner_x, corner_y, color=color, s=80, zorder=5, 
                          edgecolors='black', linewidths=1.5)
            
            # 如果是RGB（3波段），添加灰度值
            if num_bands == 3:
                gray_indices = []
                gray_values = []
                
                for i, v in enumerate(values):
                    if self.nodata_value is not None:
                        if any(v[j] == self.nodata_value for j in range(3)):
                            continue
                    
                    gray = 0.299 * v[0] + 0.587 * v[1] + 0.114 * v[2]
                    gray_indices.append(i)
                    gray_values.append(gray)
                
                # 绘制灰度值曲线
                ax.fill_between(gray_indices, gray_values, alpha=0.15, color='black')
                ax.plot(gray_indices, gray_values, color='black', linewidth=1, 
                       linestyle='--', alpha=0.7, label='灰度值')
                
                # 平滑灰度曲线
                gray_series = np.full(len(values), np.nan, dtype=np.float64)
                for idx, val in zip(gray_indices, gray_values, strict=False):
                    gray_series[idx] = float(val)
                smooth_gray = self._smooth_1d_with_nans(gray_series, sigma=2.0)
                smooth_valid = np.isfinite(smooth_gray)
                if self.show_fit_curve and np.any(smooth_valid):
                    ax.plot(
                        np.arange(len(values))[smooth_valid],
                        smooth_gray[smooth_valid],
                        color='black',
                        linewidth=2.0,
                        alpha=0.95,
                        label='灰度拟合'
                    )
                
                # 标记折点
                corner_x = [i for i in corner_indices if i in gray_indices]
                corner_y = [gray_values[gray_indices.index(i)] for i in corner_x]
                ax.scatter(corner_x, corner_y, color='black', s=100, zorder=5, 
                          marker='s', edgecolors='white', linewidths=1.5)
        
        # 添加悬停线
        y_min, y_max = ax.get_ylim()
        self.hover_line = ax.axvline(x=-1, color='yellow', linewidth=2, 
                                      linestyle='--', alpha=0.8, visible=False)
        
        # 自动调整y轴范围，让曲线更明显
        if y_max > y_min:
            margin = (y_max - y_min) * 0.1  # 添加10%的边距
            ax.set_ylim(y_min - margin, y_max + margin)
        
        ax.set_xlabel('折线点索引')
        ax.set_ylabel('像素值')
        ax.set_title('折线像素值变化')
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        self.figure.tight_layout()
        self.canvas.draw()
        
        # 连接鼠标移动事件
        self._ensure_chart_hover()
        
        self.chart_info_label.setText(f"折线图: 共{len(points)}个点")    
    def open_h5_file(self, file_path: str | None = None):
        """打开h5文件（逐级选择）"""
        # 读取上次打开的路径
        settings = get_settings()
        last_path = settings.value("last_h5_path", "")
        
        if not file_path:
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "打开h5文件",
                last_path,
                "HDF5/NetCDF Files (*.h5 *.hdf5 *.nc);;所有文件 (*.*)",
                options=QFileDialog.Option.DontUseNativeDialog
            )
        
        if not file_path:
            return
        
        # 保存当前路径
        settings.setValue("last_h5_path", os.path.dirname(file_path))
        
        try:
            self._show_loading_indicator(f"正在分析 h5 文件...\n{os.path.basename(file_path)}")
            # 使用image_io模块列出所有数据集
            datasets = list_h5_datasets(file_path, min_ndim=2)
            
            if not datasets:
                QMessageBox.warning(self, "警告", "h5文件中没有找到合适的图像数据集！")
                return
            
            # 逐级让用户选择数据集
            selected_dataset = self._show_dataset_selection_dialog(datasets)
            if not selected_dataset:
                return
            
            # 获取选中数据集的形状
            dataset_shape = None
            for name, _, shape in datasets:
                if name == selected_dataset:
                    dataset_shape = shape
                    break
            
            if dataset_shape is None:
                QMessageBox.critical(self, "错误", "无法获取数据集信息")
                return
            
            # 检查数据维度
            if len(dataset_shape) < 2:
                QMessageBox.warning(self, "警告", 
                    f"数据集 '{selected_dataset}' 不是图像数据（维度：{len(dataset_shape)}）")
                return
            elif len(dataset_shape) == 2:
                # 2D数据，直接加载
                frame_index = None
            elif len(dataset_shape) == 3:
                # 3D数据，判断是多波段还是多景
                first_dim = dataset_shape[0]
                # 如果第一维小于其他维度，可能是多波段（如RGB）
                if first_dim <= 4 and first_dim < dataset_shape[1] and first_dim < dataset_shape[2]:
                    # 可能是多波段，直接加载
                    frame_index = None
                else:
                    # 多景数据，让用户选择
                    frame_idx, ok = QInputDialog.getInt(
                        self, "选择帧", 
                        f"数据集包含 {first_dim} 景数据，请选择要显示的景（0-{first_dim-1}）:",
                        0, 0, first_dim-1)
                    if ok:
                        frame_index = frame_idx
                    else:
                        return
            else:
                QMessageBox.warning(self, "警告", 
                    f"数据集维度过高（{len(dataset_shape)}D），无法显示")
                return
            
            # 使用金字塔读取显示预览
            self._show_loading_indicator(f"正在加载 h5 图像...\n{os.path.basename(file_path)}")
            data, original_size, downsample_factor, _mode = read_h5_dataset_pyramid_display(
                file_path, selected_dataset, frame_index, self.pyramid_threshold_mb
            )
            
            if data is None:
                QMessageBox.critical(self, "错误", "无法读取数据集")
                return
            
            # 验证是否为有效图像
            if data.ndim < 2:
                QMessageBox.warning(self, "警告", "读取的数据不是有效的图像")
                return
            
            # 设置图像数据和相关属性
            self.image_data = data
            self.image_file = file_path
            self.image_source = H5DatasetRasterSource(file_path, selected_dataset, frame_index, self.pyramid_threshold_mb)
            self.nodata_value = None
            self.is_gamma = False
            self.is_tiff = False
            self.is_h5 = True  # 标记为H5文件
            self.h5_dataset_name = selected_dataset  # 保存数据集名称
            self.h5_frame_index = frame_index  # 保存帧索引
            self.original_width = original_size[0] if original_size else data.shape[1]
            self.original_height = original_size[1] if original_size else data.shape[0]
            self.downsample_factor = downsample_factor
            self._converted_to_db = False
            self._reset_render_controls_for_new_image()
            self.colormap_combo.blockSignals(True)
            self.colormap_combo.setCurrentText('jet')
            self.colormap_combo.blockSignals(False)
            self.image_viewer.current_colormap = 'jet'
            self.image_viewer.render_config.colormap_name = 'jet'
            
            # 显示图像
            display_original_size = original_size if downsample_factor > 1 else None
            self._set_viewer_source_or_array(display_original_size)
            
            # 设置Nodata值到图像查看器
            self.image_viewer.set_nodata_value(self.nodata_value)
            
            # 设置默认colormap为jet（h5数据）
            self.colormap_combo.setCurrentText('jet')
            self.image_viewer.set_colormap('jet')
            
            # 确保图像居中显示（使用延迟模式）
            self.image_viewer.fit_in_view(delayed=True)
            
            # 更新信息
            info_parts = [f"{os.path.basename(file_path)} [{selected_dataset}]"]
            
            if frame_index is not None:
                info_parts.append(f"帧: {frame_index}")
            
            if downsample_factor > 1:
                info_parts.append(f"原始: {self.original_width}x{self.original_height}")
                info_parts.append(f"显示: {data.shape[1]}x{data.shape[0]} (1/{downsample_factor})")
            else:
                info_parts.append(f"尺寸: {self.original_width}x{self.original_height}")
            
            if self.image_data.ndim == 2:
                info_parts.append("单波段")
            elif self.image_data.ndim == 3:
                info_parts.append(f"{self.image_data.shape[2]}波段")
            
            self.image_info_label.setText(self._compose_image_info(info_parts))
            
            # 更新渲染设置的波段数
            self._update_render_settings_bands()
            self._apply_render_settings_update()
            
            # 自动显示整个图像的直方图
            self.show_image_histogram()
            
            # 启用转dB按钮
            self.to_db_btn.setEnabled(True)
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开h5文件失败: {str(e)}")
            traceback.print_exc()
        finally:
            self._hide_loading_indicator()
    
    def _show_dataset_selection_dialog(self, datasets):
        """显示数据集选择对话框
        
        Args:
            datasets: 数据集列表，每项为(name, shape_str, shape)元组
            
        Returns:
            选中的数据集名称，或None
        """
        dialog = QDialog(self)
        dialog.setWindowTitle("选择数据集")
        dialog.resize(500, 400)
        
        layout = QVBoxLayout(dialog)
        
        label = QLabel("h5文件包含多个数据集，请双击要打开的数据集：")
        layout.addWidget(label)
        
        # 列表控件
        list_widget = QListWidget()
        for name, shape_str, _ in datasets:
            list_widget.addItem(f"{name}  {shape_str}")
        list_widget.setCurrentRow(0)
        layout.addWidget(list_widget)
        
        # 连接双击事件
        list_widget.doubleClicked.connect(dialog.accept)
        
        # 按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)
        
        if dialog.exec() == QDialog.Accepted:
            selected_idx = list_widget.currentRow()
            if selected_idx >= 0:
                return datasets[selected_idx][0]
        
        return None
    
    def set_nodata_value(self):
        """设置Nodata值"""
        # 获取当前Nodata值
        if np.isnan(self.nodata_value) if isinstance(self.nodata_value, float) else False:
            current_text = "nan"
        else:
            current_text = str(self.nodata_value) if self.nodata_value is not None else ""
        
        # 弹出对话框让用户输入
        text, ok = QInputDialog.getText(self, "设置Nodata值", 
                                        "请输入Nodata值（nan表示NaN，留空表示取消设置）:",
                                        text=current_text)
        
        if ok:
            if text.strip() == "":
                # 取消Nodata设置
                self.nodata_value = None
                self.image_viewer.set_nodata_value(None)
                QMessageBox.information(self, "成功", "已取消Nodata值设置")
            else:
                try:
                    # 支持nan值
                    if text.lower().strip() == "nan":
                        nodata_value = np.nan
                    else:
                        nodata_value = float(text)
                    
                    self.nodata_value = nodata_value
                    self.image_viewer.set_nodata_value(nodata_value)
                    
                    # 重新计算图像统计信息（排除新的Nodata值）
                    self._update_image_stats_to_render_settings()
                    
                    # 重新计算并显示直方图（排除Nodata值）
                    self.show_image_histogram()
                    
                    QMessageBox.information(self, "成功", f"已设置Nodata值为: {nodata_value}")
                except ValueError:
                    QMessageBox.warning(self, "错误", "请输入有效的数字或'nan'！")
            
            # 更新图像信息
            if self.image_file:
                shape = self.image_data.shape
                if self.image_data.ndim == 2:
                    info = self._compose_image_info([
                        os.path.basename(self.image_file),
                        f"尺寸: {shape[1]}x{shape[0]}",
                        "单波段",
                    ])
                elif self.image_data.ndim == 3:
                    info = self._compose_image_info([
                        os.path.basename(self.image_file),
                        f"尺寸: {shape[1]}x{shape[0]}",
                        f"{shape[2]}波段",
                    ])
                else:
                    info = self._compose_image_info([
                        os.path.basename(self.image_file),
                        f"尺寸: {shape}",
                    ])
                
                self.image_info_label.setText(info)

    def open_gamma_file(self, file_path: str | None = None):
        """打开GAMMA二进制文件"""
        settings = get_settings()
        last_path = settings.value("last_gamma_path", "")
        last_format = settings.value("last_gamma_format", "float32")

        if not file_path:
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "打开GAMMA二进制文件",
                last_path,
                "所有文件 (*.*)",
                options=QFileDialog.Option.DontUseNativeDialog
            )
        
        if not file_path:
            return
        
        # 保存当前路径
        settings.setValue("last_gamma_path", os.path.dirname(file_path))
        
        try:
            self._show_loading_indicator(f"正在分析 GAMMA 文件...\n{os.path.basename(file_path)}")
            # 先尝试以float32格式查找PAR文件
            auto_par_file, auto_dims = find_valid_par_for_binary(file_path, "float32")
            auto_format = "float32"
            
            # 如果没找到，再尝试cpxfloat32
            if auto_par_file is None:
                auto_par_file, auto_dims = find_valid_par_for_binary(file_path, "cpxfloat32")
                auto_format = "cpxfloat32"
            
            # 如果自动找到了PAR文件，直接使用
            if auto_par_file is not None and auto_dims is not None:
                gamma_format = auto_format
                width, height = auto_dims
                par_file_used = auto_par_file
                
                # 显示自动检测信息
                QMessageBox.information(self, "自动检测成功", 
                    f"自动检测到PAR文件: {os.path.basename(auto_par_file)}\n"
                    f"尺寸: {width} x {height}\n"
                    f"格式: {gamma_format}")
            else:
                # 没找到，弹出对话框让用户选择
                format_dialog = GammaSingleFileDialog(self, last_format, file_path)
                if format_dialog.exec() != QDialog.Accepted:
                    return
                
                gamma_format = format_dialog.get_selected_format()
                manual_width = format_dialog.get_manual_width()
                manual_height = format_dialog.get_manual_height()
                selected_par = format_dialog.get_selected_par()
                
                # 确定尺寸
                if manual_width is not None and manual_height is not None:
                    # 使用手动输入的尺寸
                    if not validate_dimensions(file_path, manual_width, manual_height, gamma_format):
                        QMessageBox.critical(self, "错误", 
                            f"输入的尺寸 {manual_width}x{manual_height} 与文件大小不匹配！")
                        return
                    width, height = manual_width, manual_height
                    par_file_used = None
                elif selected_par:
                    # 使用选择的PAR文件
                    from src.utils.gamma_file_process import get_dimensions_from_par
                    width, height = get_dimensions_from_par(selected_par)
                    if not validate_dimensions(file_path, width, height, gamma_format):
                        QMessageBox.critical(self, "错误", 
                            f"PAR文件中的尺寸 {width}x{height} 与二进制文件不匹配！")
                        return
                    par_file_used = selected_par
                else:
                    # 自动查找PAR文件
                    par_file_used, dims = find_valid_par_for_binary(file_path, gamma_format)
                    if par_file_used is None or dims is None:
                        QMessageBox.critical(self, "错误", 
                            "无法自动找到匹配的PAR文件！请手动指定尺寸或PAR文件。")
                        return
                    width, height = dims
            
            # 保存用户选择的格式
            settings.setValue("last_gamma_format", gamma_format)
            
            # 设置GAMMA相关属性
            self.is_gamma = True
            self.is_tiff = False
            self.gamma_format = gamma_format
            self.gamma_par_file = par_file_used
            self.original_width = width
            self.original_height = height
            self.image_file = file_path
            self.image_source = GammaVrtRasterSource(file_path, width, height, gamma_format, self.pyramid_threshold_mb)
            
            # 读取显示预览（使用 GAMMA VRT + overview）
            self._show_loading_indicator(f"正在加载 GAMMA 图像...\n{os.path.basename(file_path)}")
            data, _nodata, original_size, downsample_factor, _mode = read_gamma_pyramid_display(
                file_path, width, height, gamma_format, self.pyramid_threshold_mb
            )
            
            self.downsample_factor = downsample_factor
            
            is_complex = gamma_format.startswith('cpx')
            if is_complex:
                self.image_data = data.astype(np.float32)
                data_type_str = "相位"
            else:
                self.image_data = data.astype(np.float32) if data.dtype != np.float32 else data
                data_type_str = "幅度"
            
            # GAMMA文件默认nodata为0
            self.nodata_value = 0
            self._converted_to_db = False
            self._reset_render_controls_for_new_image()
            
            # 显示图像
            self._set_viewer_source_or_array(original_size)
            
            # 设置Nodata值到图像查看器
            self.image_viewer.set_nodata_value(self.nodata_value)
            
            # 设置默认colormap
            if is_complex:
                self.colormap_combo.setCurrentText('hsv')  # 相位使用hsv
                self.image_viewer.set_colormap('hsv')
            else:
                self.colormap_combo.setCurrentText('gray')
                self.image_viewer.set_colormap('gray')
            
            # 确保图像居中显示（使用延迟模式）
            self.image_viewer.fit_in_view(delayed=True)
            
            # 更新信息
            info_parts = [os.path.basename(file_path), f"GAMMA {gamma_format}"]
            if downsample_factor > 1:
                info_parts.append(f"原始: {width}x{height}")
                info_parts.append(f"显示: {self.image_data.shape[1]}x{self.image_data.shape[0]} (1/{downsample_factor})")
            else:
                info_parts.append(f"尺寸: {width}x{height}")
            if is_complex:
                info_parts.append(f"显示: {data_type_str}")
            if par_file_used:
                info_parts.append(f"PAR: {os.path.basename(par_file_used)}")
            
            self.image_info_label.setText(self._compose_image_info(info_parts))
            
            # 更新渲染设置的波段数
            self._update_render_settings_bands()
            self._apply_render_settings_update()
            
            # 显示直方图
            self.show_image_histogram()
            
            # 启用转dB按钮
            self.to_db_btn.setEnabled(True)
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开GAMMA文件失败: {str(e)}")
            traceback.print_exc()
        finally:
            self._hide_loading_indicator()
    
    def _read_gamma_original_region(self, x1, y1, x2, y2):
        """
        从GAMMA二进制文件读取指定区域的数据（原始坐标）
        """
        if not self.is_gamma or self.image_file is None:
            return None
        
        try:
            data = read_gamma_region(
                self.image_file, x1, y1, x2, y2,
                self.original_width, self.original_height,
                self.gamma_format
            )
            
            # 处理复数数据
            if self.gamma_format.startswith('cpx'):
                data = complex_to_phase(data)
            
            data = data.astype(np.float32)
            
            # 如果已转换为dB，应用转换
            if self._converted_to_db:
                # 创建nodata mask
                nodata_mask = (data == 0)
                # 将<=0且不是nodata的值设为一个很小的正数
                min_positive = np.min(data[data > 0]) if np.any(data > 0) else 1e-10
                data[(data <= 0) & ~nodata_mask] = min_positive
                # 转换为dB，但保持nodata为0
                db_data = np.zeros_like(data, dtype=np.float32)
                valid_mask = ~nodata_mask
                db_data[valid_mask] = 10 * np.log10(data[valid_mask])
                data = db_data
            
            return data
        except Exception as e:
            traceback.print_exc()
            return None
    
    def _read_gamma_original_pixel(self, x, y):
        """
        从GAMMA二进制文件读取指定像素的值（原始坐标）
        """
        if not self.is_gamma or self.image_file is None:
            return None
        
        try:
            value = read_gamma_pixel(
                self.image_file, x, y,
                self.original_width, self.original_height,
                self.gamma_format
            )
            
            # 处理复数数据
            if self.gamma_format.startswith('cpx'):
                value = np.angle(value)
            
            # 如果已转换为dB，应用转换
            if self._converted_to_db:
                if value == 0:
                    # 保持nodata为0
                    pass
                elif value > 0:
                    value = 10 * np.log10(value)
                else:
                    value = 10 * np.log10(1e-10)
            
            return value
        except Exception as e:
            traceback.print_exc()
            return None
    
    def convert_to_db(self):
        """将显示的图像转换为dB (10*log10)"""
        if self.image_data is None:
            return
        
        try:
            self._show_loading_indicator("正在生成 dB 派生图像缓存...")
            if self.image_source is not None:
                cache_path = write_derived_raster_cache(
                    self.image_source,
                    "db10",
                    self._convert_block_to_db,
                    pyramid_threshold_mb=self.pyramid_threshold_mb,
                )
                self.image_source = GdalRasterSource(str(cache_path), source_path=self.image_file, pyramid_threshold_mb=self.pyramid_threshold_mb)
                self.image_data = self.image_source.read_window_native(0, 0, 1, 1)
                self.downsample_factor = 1
                self._set_viewer_source_or_array(None)
            else:
                self.image_data = self._convert_array_to_db(self.image_data)
                original_size = (self.original_width, self.original_height) if self.downsample_factor > 1 else None
                self._set_viewer_source_or_array(original_size)

            self._converted_to_db = True
            
            # 设置Nodata值到图像查看器
            self.image_viewer.set_nodata_value(self.nodata_value)
            self._update_image_stats_to_render_settings()
            self._apply_render_settings_update()
            self.image_viewer.fit_in_view(delayed=True)
            
            # 更新信息标签（添加dB标记）
            current_info = self.image_info_label.text()
            if " | dB" not in current_info:
                self.image_info_label.setText(current_info + " | dB")
            
            # 重新显示直方图
            self.show_image_histogram()
            
            QMessageBox.information(self, "成功", "已转换为dB (10*log10)")
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"转换为dB失败: {str(e)}")
            traceback.print_exc()
