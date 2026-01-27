'''
Author: Yibo Yuan 2633669459@qq.com
Date: 2026-01-26
Description: DEM数据获取对话框
    支持从本地全球DEM数据文件夹或OpenTopography获取DEM数据
    集成Leaflet地图用于区域选择和可视化

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
import sys
import json
from typing import Optional, Tuple, List

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QComboBox, QCheckBox, 
    QGroupBox, QTabWidget, QWidget, QDoubleSpinBox,
    QProgressBar, QTextEdit, QMessageBox, QRadioButton, QButtonGroup,
    QSpinBox, QSplitter, QFrame, QSizePolicy, QScrollArea
)
from PySide6.QtCore import Qt, QThread, Signal, QSettings, QMimeData, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent

# 导入自定义组件
try:
    from src.widgets import LeafletMapWidget, WEBENGINE_AVAILABLE
except ImportError:
    try:
        from ..widgets import LeafletMapWidget, WEBENGINE_AVAILABLE
    except ImportError:
        LeafletMapWidget = None
        WEBENGINE_AVAILABLE = False

# 导入自定义工具模块
try:
    from src.utils import (
        LocalDEMProcessor, calculate_area_km2,
        OpenTopographyClient, DATASETS_CONFIG, OpenTopographyError,
        extract_bounding_box_from_vector, extract_bounding_box_from_raster,
        AdministrativeBoundarySelector, ADMIN_BOUNDARY_AVAILABLE
    )
    UTILS_AVAILABLE = True
except ImportError:
    try:
        from ..utils import (
            LocalDEMProcessor, calculate_area_km2,
            OpenTopographyClient, DATASETS_CONFIG, OpenTopographyError,
            extract_bounding_box_from_vector, extract_bounding_box_from_raster,
            AdministrativeBoundarySelector, ADMIN_BOUNDARY_AVAILABLE
        )
        UTILS_AVAILABLE = True
    except ImportError as e:
        print(f"警告: 无法导入工具模块: {e}")
        UTILS_AVAILABLE = False
        ADMIN_BOUNDARY_AVAILABLE = False


class DownloadWorker(QThread):
    """OpenTopography下载工作线程"""
    
    progress_updated = Signal(int, str)
    download_completed = Signal(str)
    error_occurred = Signal(str)
    
    def __init__(self, client, dataset_name: str, 
                 south: float, north: float, west: float, east: float,
                 output_path: str):
        super().__init__()
        self.client = client
        self.dataset_name = dataset_name
        self.south = south
        self.north = north
        self.west = west
        self.east = east
        self.output_path = output_path
        self.is_running = True
    
    def run(self):
        """执行下载任务"""
        try:
            self.progress_updated.emit(10, "正在验证区域...")
            
            if not self.is_running:
                return
            
            validation = self.client.validate_area_for_dataset(
                self.south, self.north, self.west, self.east, self.dataset_name
            )
            
            if not validation['is_within_limit']:
                self.error_occurred.emit(
                    f"区域面积 ({validation['area']:.0f} km²) 超出数据集限制 ({validation['limit']} km²)"
                )
                return
            
            self.progress_updated.emit(20, f"区域验证通过 ({validation['area']:.0f} km²)...")
            
            if not self.is_running:
                return
            
            self.progress_updated.emit(30, "开始下载...")
            
            def log_callback(msg):
                self.progress_updated.emit(50, msg)
            
            result = self.client.download(
                dataset_name=self.dataset_name,
                south=self.south,
                north=self.north,
                west=self.west,
                east=self.east,
                output_path=self.output_path,
                gui_logger=log_callback,
                is_running=lambda: self.is_running
            )
            
            if not self.is_running:
                self.progress_updated.emit(0, "下载已停止")
                return
            
            if result:
                self.progress_updated.emit(100, "下载完成!")
                self.download_completed.emit(result)
            else:
                self.error_occurred.emit("下载失败")
            
        except OpenTopographyError as e:
            self.error_occurred.emit(str(e))
        except Exception as e:
            self.error_occurred.emit(f"下载错误: {e}")
    
    def stop(self):
        self.is_running = False


class LocalDEMWorker(QThread):
    """本地DEM处理工作线程"""
    
    progress_updated = Signal(int, str)
    process_completed = Signal(str)
    error_occurred = Signal(str)
    files_found = Signal(list)
    
    def __init__(self, dem_folder: str, dem_type: str, 
                 south: float, north: float, west: float, east: float,
                 output_path: str, merge_only: bool = True,
                 clip_to_bounds: bool = False,
                 reference_tif: str = None):
        super().__init__()
        self.dem_folder = dem_folder
        self.dem_type = dem_type
        self.south = south
        self.north = north
        self.west = west
        self.east = east
        self.output_path = output_path
        self.merge_only = merge_only
        self.clip_to_bounds = clip_to_bounds
        self.reference_tif = reference_tif
        self.is_running = True
    
    def run(self):
        """执行本地DEM处理"""
        try:
            processor = LocalDEMProcessor()
            
            self.progress_updated.emit(10, "正在计算所需瓦片...")
            
            if not self.is_running:
                return
            
            if self.dem_type == 'SRTM':
                tiles = processor.get_srtm_tiles(
                    self.south, self.north, self.west, self.east
                )
                found_files, missing = processor.find_srtm_files(
                    self.dem_folder, tiles
                )
            else:
                tiles = processor.get_copernicus_tiles(
                    self.south, self.north, self.west, self.east
                )
                found_files, missing = processor.find_copernicus_files(
                    self.dem_folder, tiles
                )
            
            if not self.is_running:
                return
            
            self.files_found.emit(found_files)
            self.progress_updated.emit(20, f"找到 {len(found_files)} 个瓦片, 缺少 {len(missing)} 个")
            
            if not found_files:
                missing_str = ', '.join(missing[:5])
                if len(missing) > 5:
                    missing_str += f"... 等共 {len(missing)} 个"
                self.error_occurred.emit(f"未找到任何DEM瓦片文件。缺少的瓦片: {missing_str}")
                return
            
            if missing:
                missing_str = ', '.join(missing[:3])
                if len(missing) > 3:
                    missing_str += "..."
                self.progress_updated.emit(25, f"警告: 缺少部分瓦片: {missing_str}")
            
            self.progress_updated.emit(40, "正在合并DEM瓦片...")
            
            # 如果需要重采样至参考TIF，使用clip_and_resample_to_reference
            if self.reference_tif and os.path.exists(self.reference_tif):
                import tempfile
                temp_merged = os.path.join(tempfile.gettempdir(), "merged_temp.tif")
                
                success = processor.merge_dem_tiles(found_files, temp_merged)
                if not success:
                    self.error_occurred.emit("DEM合并失败")
                    return
                
                self.progress_updated.emit(70, "正在裁剪重采样至参考TIF...")
                success = processor.clip_and_resample_to_reference(
                    temp_merged, self.reference_tif, self.output_path
                )
                
                if os.path.exists(temp_merged):
                    os.remove(temp_merged)
                
                if not success:
                    self.error_occurred.emit("裁剪重采样失败")
                    return
                    
                self.progress_updated.emit(95, "裁剪重采样完成")
            elif self.clip_to_bounds:
                import tempfile
                temp_merged = os.path.join(tempfile.gettempdir(), "merged_temp.tif")
                
                success = processor.merge_dem_tiles(found_files, temp_merged)
                if not success:
                    self.error_occurred.emit("DEM合并失败")
                    return
                
                self.progress_updated.emit(70, "正在裁剪至指定范围...")
                success = processor.clip_to_bounds(
                    temp_merged, self.output_path,
                    self.south, self.north, self.west, self.east
                )
                
                if os.path.exists(temp_merged):
                    os.remove(temp_merged)
                
                if not success:
                    self.error_occurred.emit("裁剪失败")
                    return
            else:
                # merge_only
                success = processor.merge_dem_tiles(found_files, self.output_path)
                if not success:
                    self.error_occurred.emit("DEM合并失败")
                    return
            
            self.progress_updated.emit(100, "处理完成!")
            self.process_completed.emit(self.output_path)
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error_occurred.emit(str(e))
    
    def stop(self):
        self.is_running = False


class DEMAcquisitionDialog(QDialog):
    """DEM数据获取对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.setWindowTitle("DEM数据获取")
        self.resize(1000, 600)
        
        # 使用INI文件格式存储配置，实现跨平台一致性（问题9）
        # 配置文件保存在用户目录下：~/.toolbox/dem_acquisition.ini
        import pathlib
        config_dir = pathlib.Path.home() / ".toolbox"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "dem_acquisition.ini"
        self.settings = QSettings(str(config_file), QSettings.IniFormat)
        
        self.download_worker = None
        self.local_worker = None
        self.admin_selector = None
        
        self.file_south = None
        self.file_north = None
        self.file_west = None
        self.file_east = None
        
        # 地图绘制的边界（保存用户绘制的区域）
        self.map_south = None
        self.map_north = None
        self.map_west = None
        self.map_east = None
        
        # 保存的绘制区域（用于切换标签页时恢复）
        self.saved_map_bounds = None
        
        self.reference_tif_path = None
        
        # 分离保存各类文件的上次目录
        self._last_vector_dir = ""
        self._last_tif_dir = ""
        self._last_output_dir = ""
        
        # 初始化UI控件变量，防止在创建过程中触发回调时报错
        self.resample_to_tif_checkbox = None
        
        self._create_ui()
        self._init_data()
        self._load_settings()
        
        self.source_button_group.buttonClicked.connect(self._update_local_options_state)
        self._update_local_options_state()
        
        # 启用拖放
        self.setAcceptDrops(True)
    
    def _create_ui(self):
        """创建用户界面"""
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(8, 8, 8, 8)
        
        # 左侧面板 - 地图
        left_panel = self._create_map_panel()
        
        # 右侧面板 - 控制区
        right_panel = self._create_control_panel()
        
        # 使用分割器
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(left_panel)
        self.splitter.addWidget(right_panel)
        
        main_layout.addWidget(self.splitter)
    
    def _create_map_panel(self) -> QWidget:
        """创建地图面板"""
        panel = QGroupBox("区域预览")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        
        # 使用独立的地图组件
        self.map_widget = LeafletMapWidget(center_lat=35, center_lng=105, zoom=4)
        self.map_widget.setMinimumSize(200, 200)
        
        # 连接信号
        self.map_widget.boundsDrawn.connect(self._on_map_bounds_drawn)
        self.map_widget.boundsCleared.connect(self._on_map_bounds_cleared)
        
        layout.addWidget(self.map_widget)
        
        return panel
    
    def _create_control_panel(self) -> QWidget:
        """创建控制面板"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(6)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # 区域选择
        region_group = self._create_region_group()
        layout.addWidget(region_group)
        
        # 数据源选择
        source_group = self._create_source_group()
        layout.addWidget(source_group)
        
        # 输出设置
        output_group = self._create_output_group()
        layout.addWidget(output_group)
        
        # 进度和日志
        progress_group = self._create_progress_group()
        layout.addWidget(progress_group)
        
        # 按钮
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        
        self.start_button = QPushButton("开始获取")
        self.start_button.setMinimumHeight(36)
        self.start_button.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #229954; }
            QPushButton:pressed { background-color: #1e8449; }
            QPushButton:disabled { background-color: #95a5a6; }
        """)
        self.start_button.clicked.connect(self.start_acquisition)
        button_layout.addWidget(self.start_button)
        
        self.stop_button = QPushButton("停止")
        self.stop_button.setMinimumHeight(36)
        self.stop_button.setEnabled(False)
        self.stop_button.setStyleSheet("""
            QPushButton {
                background-color: #e74c3c;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #c0392b; }
            QPushButton:pressed { background-color: #a93226; }
            QPushButton:disabled { background-color: #95a5a6; }
        """)
        self.stop_button.clicked.connect(self.stop_acquisition)
        button_layout.addWidget(self.stop_button)
        
        self.close_button = QPushButton("关闭")
        self.close_button.setMinimumHeight(36)
        self.close_button.clicked.connect(self.close)
        button_layout.addWidget(self.close_button)
        
        layout.addLayout(button_layout)
        
        return panel
    
    def _create_region_group(self) -> QGroupBox:
        """创建区域选择分组"""
        group = QGroupBox("区域选择")
        group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #bdc3c7;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                padding: 0 5px;
            }
        """)
        
        layout = QVBoxLayout(group)
        layout.setSpacing(2)
        layout.setContentsMargins(4, 12, 4, 4)
        
        self.region_tab = QTabWidget()
        self.region_tab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.region_tab.currentChanged.connect(self._on_region_tab_changed)
        
        # 地图绘制 - 放在第一位
        if WEBENGINE_AVAILABLE:
            map_widget = self._create_map_draw_widget()
            self.region_tab.addTab(map_widget, "地图绘制")
        
        # 手动输入
        manual_widget = self._create_manual_input_widget()
        self.region_tab.addTab(manual_widget, "手动输入")
        
        # 行政区划
        admin_widget = self._create_admin_widget()
        self.region_tab.addTab(admin_widget, "行政区划")
        
        # 矢量文件
        vector_widget = self._create_vector_import_widget()
        self.region_tab.addTab(vector_widget, "矢量文件")
        
        # TIF文件
        tif_widget = self._create_tif_import_widget()
        self.region_tab.addTab(tif_widget, "TIF文件")
        
        layout.addWidget(self.region_tab)
        
        return group
    
    def _create_manual_input_widget(self) -> QWidget:
        """创建手动输入部件"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(3)
        
        grid_container = QHBoxLayout()
        grid_container.addStretch()
        
        grid_layout = QGridLayout()
        grid_layout.setHorizontalSpacing(8)
        grid_layout.setVerticalSpacing(2)
        
        grid_layout.addWidget(QLabel("北纬:"), 0, 1, Qt.AlignRight)
        self.north_spin = QDoubleSpinBox()
        self.north_spin.setRange(-90, 90)
        self.north_spin.setDecimals(6)
        self.north_spin.setFixedWidth(120)
        self.north_spin.valueChanged.connect(self._update_manual_area)
        grid_layout.addWidget(self.north_spin, 0, 2)
        
        grid_layout.addWidget(QLabel("西经:"), 1, 0, Qt.AlignRight)
        self.west_spin = QDoubleSpinBox()
        self.west_spin.setRange(-180, 180)
        self.west_spin.setDecimals(6)
        self.west_spin.setFixedWidth(120)
        self.west_spin.valueChanged.connect(self._update_manual_area)
        grid_layout.addWidget(self.west_spin, 1, 1)
        
        grid_layout.addWidget(QLabel("东经:"), 1, 2, Qt.AlignRight)
        self.east_spin = QDoubleSpinBox()
        self.east_spin.setRange(-180, 180)
        self.east_spin.setDecimals(6)
        self.east_spin.setFixedWidth(120)
        self.east_spin.valueChanged.connect(self._update_manual_area)
        grid_layout.addWidget(self.east_spin, 1, 3)
        
        grid_layout.addWidget(QLabel("南纬:"), 2, 1, Qt.AlignRight)
        self.south_spin = QDoubleSpinBox()
        self.south_spin.setRange(-90, 90)
        self.south_spin.setDecimals(6)
        self.south_spin.setFixedWidth(120)
        self.south_spin.valueChanged.connect(self._update_manual_area)
        grid_layout.addWidget(self.south_spin, 2, 2)
        
        grid_container.addLayout(grid_layout)
        grid_container.addStretch()
        layout.addLayout(grid_container)
        
        bottom_layout = QHBoxLayout()
        self.manual_area_label = QLabel("区域面积: -- km²")
        bottom_layout.addWidget(self.manual_area_label)
        bottom_layout.addStretch()
        
        layout.addLayout(bottom_layout)
        
        return widget
    
    def _create_admin_widget(self) -> QWidget:
        """创建行政区划选择部件"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)
        
        admin_layout = QHBoxLayout()
        
        admin_layout.addWidget(QLabel("省份:"))
        self.province_combo = QComboBox()
        self.province_combo.setMinimumWidth(100)
        self.province_combo.currentTextChanged.connect(self._on_province_changed)
        admin_layout.addWidget(self.province_combo)
        
        admin_layout.addWidget(QLabel("城市:"))
        self.city_combo = QComboBox()
        self.city_combo.setMinimumWidth(100)
        self.city_combo.currentTextChanged.connect(self._on_city_changed)
        admin_layout.addWidget(self.city_combo)
        
        admin_layout.addWidget(QLabel("区县:"))
        self.district_combo = QComboBox()
        self.district_combo.setMinimumWidth(100)
        self.district_combo.currentTextChanged.connect(self._on_district_changed)
        admin_layout.addWidget(self.district_combo)
        
        layout.addLayout(admin_layout)
        
        info_layout = QHBoxLayout()
        self.admin_coord_label = QLabel("边界坐标: --")
        info_layout.addWidget(self.admin_coord_label)
        self.admin_area_label = QLabel("区域面积: -- km²")
        info_layout.addWidget(self.admin_area_label)
        info_layout.addStretch()
        layout.addLayout(info_layout)
        
        return widget
    
    def _create_vector_import_widget(self) -> QWidget:
        """创建矢量文件导入部件"""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)
        
        # 左侧：文件选择区域
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(3)
        
        file_layout = QHBoxLayout()
        file_layout.addWidget(QLabel("矢量文件:"))
        self.vector_file_edit = QLineEdit()
        self.vector_file_edit.setMinimumWidth(50)  # 允许更小的宽度，避免撑大面板
        self.vector_file_edit.setReadOnly(True)
        self.vector_file_edit.setPlaceholderText("选择GeoJSON/KML/SHP/GPKG文件...")
        file_layout.addWidget(self.vector_file_edit)
        
        browse_btn = QPushButton("浏览")
        browse_btn.clicked.connect(self._browse_vector_file)
        file_layout.addWidget(browse_btn)
        left_layout.addLayout(file_layout)
        
        info_layout = QHBoxLayout()
        self.vector_coord_label = QLabel("边界坐标: --")
        info_layout.addWidget(self.vector_coord_label)
        self.vector_area_label = QLabel("区域面积: -- km²")
        info_layout.addWidget(self.vector_area_label)
        info_layout.addStretch()
        left_layout.addLayout(info_layout)
        
        layout.addWidget(left_widget, stretch=1)
        
        # 右侧：拖放区域
        drop_frame = QFrame()
        drop_frame.setFixedSize(120, 80)
        drop_frame.setStyleSheet("""
            QFrame {
                border: 2px dashed #555;
                border-radius: 8px;
                background-color: #3a3a3a;
            }
        """)
        drop_layout = QVBoxLayout(drop_frame)
        drop_layout.setContentsMargins(5, 5, 5, 5)
        drop_layout.setSpacing(2)
        
        # 云朵上传图标 (已移除)
        # icon_label = QLabel("☁️⬆")
        # icon_label.setAlignment(Qt.AlignCenter)
        # icon_label.setStyleSheet("color: #888; font-size: 18px; border: none;")
        # drop_layout.addWidget(icon_label)
        
        # 拖放提示文字
        text_label = QLabel("拖放文件到此处")
        text_label.setAlignment(Qt.AlignCenter)
        text_label.setStyleSheet("color: #aaa; font-size: 10px; border: none;")
        drop_layout.addWidget(text_label)
        
        # 支持格式
        format_label = QLabel("(shp, geojson, kml)")
        format_label.setAlignment(Qt.AlignCenter)
        format_label.setStyleSheet("color: #666; font-size: 9px; border: none;")
        drop_layout.addWidget(format_label)
        
        layout.addWidget(drop_frame)
        
        return widget
    
    def _create_tif_import_widget(self) -> QWidget:
        """创建TIF文件导入部件"""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)
        
        # 左侧：文件选择区域
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(3)
        
        file_layout = QHBoxLayout()
        file_layout.addWidget(QLabel("TIF文件:"))
        self.tif_file_edit = QLineEdit()
        self.tif_file_edit.setMinimumWidth(50)  # 允许更小的宽度，避免撑大面板
        self.tif_file_edit.setReadOnly(True)
        self.tif_file_edit.setPlaceholderText("选择TIF/TIFF文件...")
        file_layout.addWidget(self.tif_file_edit)
        
        browse_btn = QPushButton("浏览")
        browse_btn.clicked.connect(self._browse_tif_file)
        file_layout.addWidget(browse_btn)
        left_layout.addLayout(file_layout)
        
        info_layout = QHBoxLayout()
        self.tif_coord_label = QLabel("边界坐标(WGS84): --")
        info_layout.addWidget(self.tif_coord_label)
        self.tif_area_label = QLabel("区域面积: -- km²")
        info_layout.addWidget(self.tif_area_label)
        info_layout.addStretch()
        left_layout.addLayout(info_layout)
        
        layout.addWidget(left_widget, stretch=1)
        
        # 右侧：拖放区域
        drop_frame = QFrame()
        drop_frame.setFixedSize(120, 80)
        drop_frame.setStyleSheet("""
            QFrame {
                border: 2px dashed #555;
                border-radius: 8px;
                background-color: #3a3a3a;
            }
        """)
        drop_layout = QVBoxLayout(drop_frame)
        drop_layout.setContentsMargins(5, 5, 5, 5)
        drop_layout.setSpacing(2)
        
        # 云朵上传图标 (已移除)
        # icon_label = QLabel("☁️⬆")
        # icon_label.setAlignment(Qt.AlignCenter)
        # icon_label.setStyleSheet("color: #888; font-size: 18px; border: none;")
        # drop_layout.addWidget(icon_label)
        
        # 拖放提示文字
        text_label = QLabel("拖放文件到此处")
        text_label.setAlignment(Qt.AlignCenter)
        text_label.setStyleSheet("color: #aaa; font-size: 10px; border: none;")
        drop_layout.addWidget(text_label)
        
        # 支持格式
        format_label = QLabel("(tif, tiff)")
        format_label.setAlignment(Qt.AlignCenter)
        format_label.setStyleSheet("color: #666; font-size: 9px; border: none;")
        drop_layout.addWidget(format_label)
        
        layout.addWidget(drop_frame)
        
        return widget
    
    def _create_map_draw_widget(self) -> QWidget:
        """创建地图绘制标签页部件"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)
        
        info_label = QLabel("在左侧地图上使用矩形工具绘制选择区域，绘制后将自动显示坐标")
        info_label.setStyleSheet("color: #7f8c8d;")
        layout.addWidget(info_label)
        
        self.map_coord_label = QLabel("在地图上绘制矩形以选择区域")
        layout.addWidget(self.map_coord_label)
        
        self.map_area_label = QLabel("区域面积: -- km²")
        layout.addWidget(self.map_area_label)
        
        layout.addStretch()
        
        return widget
    
    def _create_source_group(self) -> QGroupBox:
        """创建数据源选择分组"""
        group = QGroupBox("数据源选择")
        group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #bdc3c7;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                padding: 0 5px;
            }
        """)
        
        layout = QVBoxLayout(group)
        layout.setSpacing(4)
        
        self.source_button_group = QButtonGroup(self)
        
        # 本地SRTM
        srtm_layout = QHBoxLayout()
        self.srtm_radio = QRadioButton("本地SRTM数据")
        self.source_button_group.addButton(self.srtm_radio, 0)
        srtm_layout.addWidget(self.srtm_radio)
        self.srtm_folder_edit = QLineEdit()
        self.srtm_folder_edit.setMinimumWidth(50)  # 允许更小的宽度，避免撑大面板
        self.srtm_folder_edit.setPlaceholderText("选择SRTM文件夹...")
        srtm_layout.addWidget(self.srtm_folder_edit)
        srtm_browse_btn = QPushButton("浏览")
        srtm_browse_btn.clicked.connect(lambda: self._browse_folder(self.srtm_folder_edit))
        srtm_layout.addWidget(srtm_browse_btn)
        layout.addLayout(srtm_layout)
        
        # 本地Copernicus
        cop_layout = QHBoxLayout()
        self.copernicus_radio = QRadioButton("本地Copernicus DEM")
        self.source_button_group.addButton(self.copernicus_radio, 1)
        cop_layout.addWidget(self.copernicus_radio)
        self.copernicus_folder_edit = QLineEdit()
        self.copernicus_folder_edit.setMinimumWidth(50)  # 允许更小的宽度，避免撑大面板
        self.copernicus_folder_edit.setPlaceholderText("选择Copernicus文件夹...")
        cop_layout.addWidget(self.copernicus_folder_edit)
        cop_browse_btn = QPushButton("浏览")
        cop_browse_btn.clicked.connect(lambda: self._browse_folder(self.copernicus_folder_edit))
        cop_layout.addWidget(cop_browse_btn)
        layout.addLayout(cop_layout)
        
        # OpenTopography
        ot_layout0 = QHBoxLayout()
        self.opentopo_radio = QRadioButton("OpenTopography在线下载")
        self.opentopo_radio.setChecked(True)
        self.source_button_group.addButton(self.opentopo_radio, 2)
        ot_layout0.addWidget(self.opentopo_radio)
        layout.addLayout(ot_layout0)
        
        ot_layout1 = QHBoxLayout()
        ot_layout1.setContentsMargins(20, 0, 0, 0)
        ot_layout1.addWidget(QLabel("DEM类型:"))
        self.dem_type_combo = QComboBox()
        self.dem_type_combo.setMinimumWidth(100)
        self.dem_type_combo.currentIndexChanged.connect(self._on_dem_type_changed)
        ot_layout1.addWidget(self.dem_type_combo)
        
        self.dem_info_label = QLabel("")
        self.dem_info_label.setStyleSheet("color: #7f8c8d;")
        ot_layout1.addWidget(self.dem_info_label)
        ot_layout1.addStretch()
        layout.addLayout(ot_layout1)
        
        # API密钥
        ot_layout2 = QHBoxLayout()
        ot_layout2.addSpacing(20)
        ot_layout2.addWidget(QLabel("API密钥:"))
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setMinimumWidth(50)  # 允许更小的宽度，避免撑大面板
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("输入OpenTopography API密钥（32位）...")
        ot_layout2.addWidget(self.api_key_edit)
        
        get_api_btn = QPushButton("获取API密钥")
        get_api_btn.clicked.connect(self._open_api_url)
        ot_layout2.addWidget(get_api_btn)
        
        test_api_btn = QPushButton("测试API")
        test_api_btn.clicked.connect(self._test_api_key)
        ot_layout2.addWidget(test_api_btn)
        layout.addLayout(ot_layout2)
        
        return group
    
    def _create_output_group(self) -> QGroupBox:
        """创建输出设置分组"""
        group = QGroupBox("输出设置")
        group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #bdc3c7;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                padding: 0 5px;
            }
        """)
        
        layout = QVBoxLayout(group)
        layout.setSpacing(4)
        
        output_layout = QHBoxLayout()
        output_layout.addWidget(QLabel("输出文件:"))
        self.output_file_edit = QLineEdit()
        self.output_file_edit.setMinimumWidth(50)  # 允许更小的宽度，避免撑大面板
        self.output_file_edit.setPlaceholderText("选择输出DEM文件路径...")
        output_layout.addWidget(self.output_file_edit)
        
        output_browse_btn = QPushButton("浏览")
        output_browse_btn.clicked.connect(self._browse_output_file)
        output_layout.addWidget(output_browse_btn)
        layout.addLayout(output_layout)
        
        options_layout = QHBoxLayout()
        
        self.merge_only_checkbox = QCheckBox("仅合并DEM瓦片")
        self.merge_only_checkbox.setToolTip("将找到的DEM瓦片合并为一个文件，不进行裁剪")
        self.merge_only_checkbox.setChecked(True)
        options_layout.addWidget(self.merge_only_checkbox)
        
        self.clip_to_bounds_checkbox = QCheckBox("裁剪至相同范围")
        self.clip_to_bounds_checkbox.setToolTip("将合并后的DEM裁剪至输入区域的精确经纬度范围")
        options_layout.addWidget(self.clip_to_bounds_checkbox)
        
        # 三个选项互斥
        self.output_option_group = QButtonGroup(self)
        self.output_option_group.setExclusive(False)  # 手动控制互斥
        
        self.merge_only_checkbox.toggled.connect(self._on_output_option_changed)
        self.clip_to_bounds_checkbox.toggled.connect(self._on_output_option_changed)
        
        self.resample_to_tif_checkbox = QCheckBox("裁剪重采样至相同范围和分辨率")
        self.resample_to_tif_checkbox.setToolTip("将获取的DEM裁剪重采样至与参考TIF相同的范围、分辨率和坐标系")
        self.resample_to_tif_checkbox.setEnabled(False)  # 默认禁用，仅TIF标签页可用
        self.resample_to_tif_checkbox.toggled.connect(self._on_output_option_changed)
        options_layout.addWidget(self.resample_to_tif_checkbox)
        
        options_layout.addStretch()
        layout.addLayout(options_layout)
        
        return group
    
    def _create_progress_group(self) -> QGroupBox:
        """创建日志分组"""
        group = QGroupBox("日志")
        layout = QVBoxLayout(group)
        layout.setSpacing(0)
        layout.setContentsMargins(4, 4, 4, 4)
        
        # 日志文本框
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(100)
        self.log_text.setStyleSheet("""
            QTextEdit {
                border: 1px solid #bdc3c7;
                padding: 2px;
                margin: 0px;
            }
        """)
        layout.addWidget(self.log_text)
        
        return group
    
    # ===== 地图相关方法 =====
    
    def _on_map_bounds_drawn(self, south: float, north: float, west: float, east: float):
        """处理从地图绘制的边界"""
        self.map_south = south
        self.map_north = north
        self.map_west = west
        self.map_east = east
        
        # 保存绘制的区域
        self.saved_map_bounds = (south, north, west, east)
        
        # 更新地图绘制标签页的显示
        if hasattr(self, 'map_coord_label'):
            coord_text = (
                f"边界坐标: 南={self.map_south:.6f}, 北={self.map_north:.6f}, "
                f"西={self.map_west:.6f}, 东={self.map_east:.6f}"
            )
            self.map_coord_label.setText(coord_text)
            self.map_coord_label.setStyleSheet("color: #2c3e50; font-weight: bold;")
        
        if UTILS_AVAILABLE and hasattr(self, 'map_area_label'):
            area = calculate_area_km2(self.map_south, self.map_north, 
                                      self.map_west, self.map_east)
            self.map_area_label.setText(f"区域面积: {area:.2f} km²")
            self.map_area_label.setStyleSheet("color: #27ae60; font-weight: bold;")
        
        # 自动切换到地图绘制标签页
        if WEBENGINE_AVAILABLE:
            self.region_tab.setCurrentIndex(0)  # 地图绘制现在是第一个标签页
        
        self.log(f"地图绘制区域: 南={self.map_south:.4f}, 北={self.map_north:.4f}, "
                f"西={self.map_west:.4f}, 东={self.map_east:.4f}")
    
    def _on_map_bounds_cleared(self):
        """处理地图边界清除"""
        self.map_south = None
        self.map_north = None
        self.map_west = None
        self.map_east = None
        self.saved_map_bounds = None
        
        if hasattr(self, 'map_coord_label'):
            self.map_coord_label.setText("在地图上绘制矩形以选择区域")
        if hasattr(self, 'map_area_label'):
            self.map_area_label.setText("区域面积: -- km²")
    
    def _clear_map_bounds(self):
        """清除地图上的边界"""
        if self.map_widget.is_available():
            self.map_widget.clear_bounds()
        self._on_map_bounds_cleared()
    
    def _show_bounds_on_map(self, south, north, west, east):
        """在地图上显示边界"""
        if self.map_widget.is_available() and south is not None:
            self.map_widget.show_bounds(south, north, west, east)
    
    def _on_region_tab_changed(self, index):
        """区域选择标签页切换时更新地图"""
        # 根据当前标签页索引显示对应的区域
        # 防止初始化时控件未创建导致的错误
        if not hasattr(self, 'resample_to_tif_checkbox') or self.resample_to_tif_checkbox is None:
            return

        # 注意：地图绘制现在是第0个标签页
        
        # 控制TIF重采样选项的可用性（只有选择TIF文件时才可用）
        is_tif_tab = (index == 4) or (index == 3 and not WEBENGINE_AVAILABLE)
        self.resample_to_tif_checkbox.setEnabled(is_tif_tab and self.reference_tif_path is not None)
        
        if not self.map_widget.is_available():
            return
        
        if index == 0 and WEBENGINE_AVAILABLE:  # 地图绘制
            # 恢复之前绘制的区域
            if self.saved_map_bounds is not None:
                south, north, west, east = self.saved_map_bounds
                self._show_bounds_on_map(south, north, west, east)
                # 更新右侧界面显示
                if hasattr(self, 'map_coord_label'):
                    coord_text = (
                        f"边界坐标: 南={south:.6f}, 北={north:.6f}, "
                        f"西={west:.6f}, 东={east:.6f}"
                    )
                    self.map_coord_label.setText(coord_text)
                    self.map_coord_label.setStyleSheet("color: #2c3e50; font-weight: bold;")
                if UTILS_AVAILABLE and hasattr(self, 'map_area_label'):
                    area = calculate_area_km2(south, north, west, east)
                    self.map_area_label.setText(f"区域面积: {area:.2f} km²")
                    self.map_area_label.setStyleSheet("color: #27ae60; font-weight: bold;")
            elif self.map_south is not None:
                self._show_bounds_on_map(self.map_south, self.map_north, self.map_west, self.map_east)
        elif index == 1:  # 手动输入
            south = self.south_spin.value()
            north = self.north_spin.value()
            west = self.west_spin.value()
            east = self.east_spin.value()
            if south < north and west < east:
                self._show_bounds_on_map(south, north, west, east)
        elif index == 2:  # 行政区划
            province = self.province_combo.currentText()
            if province and province != "选择省份" and self.admin_selector:
                city = self.city_combo.currentText()
                district = self.district_combo.currentText()
                city = city if city != "选择城市" else None
                district = district if district != "选择区县" else None
                boundary = self.admin_selector.get_boundary(province, city, district)
                if boundary:
                    west, south, east, north = boundary
                    self._show_bounds_on_map(south, north, west, east)
        elif index == 3:  # 矢量文件
            if self.file_south is not None:
                self._show_bounds_on_map(self.file_south, self.file_north, self.file_west, self.file_east)
        elif index == 4:  # TIF文件
            if self.file_south is not None:
                self._show_bounds_on_map(self.file_south, self.file_north, self.file_west, self.file_east)
    
    # ===== 其他方法 =====
    
    def _update_local_options_state(self):
        """更新本地DEM处理选项的可用状态"""
        source_id = self.source_button_group.checkedId()
        is_local = source_id in (0, 1)
        
        self.merge_only_checkbox.setEnabled(is_local)
        self.clip_to_bounds_checkbox.setEnabled(is_local)
        
        if not is_local:
            self.merge_only_checkbox.setChecked(True)
    
    def _init_data(self):
        """初始化数据"""
        if UTILS_AVAILABLE:
            self.dem_type_combo.clear()
            for name, info in DATASETS_CONFIG.items():
                self.dem_type_combo.addItem(f"{name} ({info['resolution']})", name)
            
            self._on_dem_type_changed()
            self.log("✓ DEM类型列表已加载")
        else:
            self.log("✗ 工具模块未加载，部分功能不可用")
            self.opentopo_radio.setEnabled(False)
        
        if ADMIN_BOUNDARY_AVAILABLE:
            try:
                self.admin_selector = AdministrativeBoundarySelector()
                if self.admin_selector.is_available():
                    provinces = self.admin_selector.get_provinces()
                    
                    self.province_combo.clear()
                    self.province_combo.addItem("选择省份")
                    for province in provinces:
                        self.province_combo.addItem(province)
                    
                    self.log("✓ 行政区划数据加载成功")
                else:
                    self.log("✗ 行政区划数据库不可用 (is_available返回False)")
                    
            except Exception as e:
                import traceback
                self.log(f"✗ 加载行政区划数据失败: {e}")
                self.log(f"[调试] 异常详情: {traceback.format_exc()}")
        else:
            self.log("✗ 行政区划功能不可用")
        
        if WEBENGINE_AVAILABLE:
            self.log("✓ 地图功能已启用")
        else:
            self.log("✗ 地图功能不可用 (需要安装 PyQt6-WebEngine)")
        
        self.log("DEM数据获取工具已就绪")
    
    def _load_settings(self):
        """加载上次保存的设置"""
        try:
            srtm_folder = self.settings.value("srtm_folder", "")
            if srtm_folder:
                self.srtm_folder_edit.setText(srtm_folder)
            
            copernicus_folder = self.settings.value("copernicus_folder", "")
            if copernicus_folder:
                self.copernicus_folder_edit.setText(copernicus_folder)
            
            api_key = self.settings.value("api_key", "")
            if api_key:
                self.api_key_edit.setText(api_key)
            
            source_id = self.settings.value("source_id", 2, type=int)
            if source_id == 0:
                self.srtm_radio.setChecked(True)
            elif source_id == 1:
                self.copernicus_radio.setChecked(True)
            else:
                self.opentopo_radio.setChecked(True)
            
            dem_type = self.settings.value("dem_type", "SRTMGL3")
            index = self.dem_type_combo.findData(dem_type)
            if index >= 0:
                self.dem_type_combo.setCurrentIndex(index)
            
            last_output_dir = self.settings.value("last_output_dir", "")
            if last_output_dir and os.path.exists(last_output_dir):
                self._last_output_dir = last_output_dir
            
            # 加载矢量文件和TIF文件的上次目录
            last_vector_dir = self.settings.value("last_vector_dir", "")
            if last_vector_dir and os.path.exists(last_vector_dir):
                self._last_vector_dir = last_vector_dir
            
            last_tif_dir = self.settings.value("last_tif_dir", "")
            if last_tif_dir and os.path.exists(last_tif_dir):
                self._last_tif_dir = last_tif_dir
            
            merge_only = self.settings.value("merge_only", True, type=bool)
            self.merge_only_checkbox.setChecked(merge_only)
            self.clip_to_bounds_checkbox.setChecked(not merge_only)
            
            self.log("✓ 已加载上次的配置")
            
        except Exception as e:
            self.log(f"加载配置失败: {e}")
    
    def _save_settings(self):
        """保存当前设置"""
        try:
            self.settings.setValue("srtm_folder", self.srtm_folder_edit.text())
            self.settings.setValue("copernicus_folder", self.copernicus_folder_edit.text())
            self.settings.setValue("api_key", self.api_key_edit.text())
            self.settings.setValue("source_id", self.source_button_group.checkedId())
            self.settings.setValue("dem_type", self.dem_type_combo.currentData())
            
            output_path = self.output_file_edit.text().strip()
            if output_path:
                output_dir = os.path.dirname(output_path)
                if output_dir and os.path.exists(output_dir):
                    self.settings.setValue("last_output_dir", output_dir)
            
            # 保存矢量文件和TIF文件的上次目录
            if self._last_vector_dir:
                self.settings.setValue("last_vector_dir", self._last_vector_dir)
            if self._last_tif_dir:
                self.settings.setValue("last_tif_dir", self._last_tif_dir)
            
            self.settings.setValue("merge_only", self.merge_only_checkbox.isChecked())
            
        except Exception as e:
            print(f"保存配置失败: {e}")
    
    def log(self, message: str):
        """添加日志消息"""
        self.log_text.append(message)
    
    def _update_manual_area(self):
        """更新手动输入的面积并自动显示在地图上"""
        try:
            south = self.south_spin.value()
            north = self.north_spin.value()
            west = self.west_spin.value()
            east = self.east_spin.value()
            
            if UTILS_AVAILABLE and south < north and west < east:
                area = calculate_area_km2(south, north, west, east)
                self.manual_area_label.setText(f"区域面积: {area:.2f} km²")
                # 自动显示在地图上
                self._show_bounds_on_map(south, north, west, east)
            else:
                self.manual_area_label.setText("区域面积: -- km²")
        except:
            self.manual_area_label.setText("区域面积: -- km²")
    
    def _on_province_changed(self, province_name):
        """省份选择改变"""
        if not self.admin_selector or not province_name or province_name == "选择省份":
            return
        
        cities = self.admin_selector.get_cities(province_name)
        self.city_combo.clear()
        self.city_combo.addItem("选择城市")
        for city in cities:
            self.city_combo.addItem(city)
        
        self.district_combo.clear()
        self.district_combo.addItem("选择区县")
        
        self._update_admin_boundary(province_name, None, None)
    
    def _on_city_changed(self, city_name):
        """城市选择改变"""
        if not self.admin_selector or not city_name or city_name == "选择城市":
            return
        
        province_name = self.province_combo.currentText()
        districts = self.admin_selector.get_districts(province_name, city_name)
        
        self.district_combo.clear()
        self.district_combo.addItem("选择区县")
        for district in districts:
            self.district_combo.addItem(district)
        
        self._update_admin_boundary(province_name, city_name, None)
    
    def _on_district_changed(self, district_name):
        """区县选择改变"""
        if not self.admin_selector or not district_name or district_name == "选择区县":
            return
        
        province_name = self.province_combo.currentText()
        city_name = self.city_combo.currentText()
        
        self._update_admin_boundary(province_name, city_name, district_name)
    
    def _update_admin_boundary(self, province, city, district):
        """更新行政区划边界显示"""
        if not self.admin_selector:
            return
        
        boundary = self.admin_selector.get_boundary(province, city, district)
        if boundary:
            west, south, east, north = boundary
            self.admin_coord_label.setText(
                f"边界: 南={south:.4f}, 北={north:.4f}, 西={west:.4f}, 东={east:.4f}"
            )
            
            if UTILS_AVAILABLE:
                area = calculate_area_km2(south, north, west, east)
                self.admin_area_label.setText(f"面积: {area:.2f} km²")
            
            # 在地图上显示
            self._show_bounds_on_map(south, north, west, east)
    
    def _browse_vector_file(self):
        """浏览矢量文件"""
        initial_dir = self._last_vector_dir or ""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择矢量文件",
            initial_dir,
            "矢量文件 (*.json *.geojson *.kml *.kmz *.shp *.gpkg);;所有文件 (*.*)"
        )
        
        if file_path:
            self._last_vector_dir = os.path.dirname(file_path)
            self.vector_file_edit.setText(file_path)
            
            try:
                if UTILS_AVAILABLE:
                    result = extract_bounding_box_from_vector(file_path)
                    if result:
                        west, south, east, north = result
                        
                        self.file_south = south
                        self.file_north = north
                        self.file_west = west
                        self.file_east = east
                        
                        self.vector_coord_label.setText(
                            f"边界: 西={west:.6f}, 东={east:.6f}, 南={south:.6f}, 北={north:.6f}"
                        )
                        
                        area = calculate_area_km2(south, north, west, east)
                        self.vector_area_label.setText(f"面积: {area:.2f} km²")
                        
                        self.log(f"矢量文件加载成功: {os.path.basename(file_path)}")
                        
                        # 在地图上显示
                        self._show_bounds_on_map(south, north, west, east)
                    else:
                        QMessageBox.warning(self, "警告", "无法从矢量文件提取边界")
                
            except Exception as e:
                QMessageBox.critical(self, "错误", f"读取矢量文件失败: {e}")
                self.log(f"读取矢量文件失败: {e}")
    
    def _browse_tif_file(self):
        """浏览TIF文件"""
        initial_dir = self._last_tif_dir or ""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择TIF文件",
            initial_dir,
            "TIF文件 (*.tif *.tiff);;所有文件 (*.*)"
        )
        
        if file_path:
            self._last_tif_dir = os.path.dirname(file_path)
            self.tif_file_edit.setText(file_path)
            self.reference_tif_path = file_path
            
            try:
                if UTILS_AVAILABLE:
                    result = extract_bounding_box_from_raster(file_path)
                    if result:
                        west, south, east, north = result
                        
                        self.file_south = south
                        self.file_north = north
                        self.file_west = west
                        self.file_east = east
                        
                        self.tif_coord_label.setText(
                            f"边界(WGS84): 西={west:.6f}, 东={east:.6f}, 南={south:.6f}, 北={north:.6f}"
                        )
                        
                        area = calculate_area_km2(south, north, west, east)
                        self.tif_area_label.setText(f"面积: {area:.2f} km²")
                        
                        self.log(f"TIF文件加载成功: {os.path.basename(file_path)}")
                        
                        # 启用重采样选项
                        current_tab = self.region_tab.currentIndex()
                        is_tif_tab = (current_tab == 4) or (current_tab == 3 and not WEBENGINE_AVAILABLE)
                        if is_tif_tab:
                            self.resample_to_tif_checkbox.setEnabled(True)
                        
                        # 在地图上显示
                        self._show_bounds_on_map(south, north, west, east)
                    else:
                        QMessageBox.warning(self, "警告", "无法从TIF文件提取边界")
                
            except Exception as e:
                QMessageBox.critical(self, "错误", f"读取TIF文件失败: {e}")
                self.log(f"读取TIF文件失败: {e}")
    
    def _browse_folder(self, line_edit):
        """浏览文件夹"""
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folder:
            line_edit.setText(folder)
    
    def _browse_output_file(self):
        """浏览输出文件"""
        initial_dir = self._last_output_dir or ""
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存DEM文件",
            initial_dir,
            "GeoTIFF文件 (*.tif);;所有文件 (*.*)"
        )
        
        if file_path:
            if not file_path.lower().endswith('.tif'):
                file_path += '.tif'
            self.output_file_edit.setText(file_path)
            self._last_output_dir = os.path.dirname(file_path)
    
    def _on_dem_type_changed(self):
        """DEM类型改变"""
        if UTILS_AVAILABLE and self.dem_type_combo.currentData():
            dataset_name = self.dem_type_combo.currentData()
            info = DATASETS_CONFIG.get(dataset_name, {})
            
            text = f"分辨率: {info.get('resolution', '未知')} | "
            text += f"最大面积: {info.get('limit', 0):,} km²"
            
            self.dem_info_label.setText(text)
    
    def _open_api_url(self):
        """打开API密钥获取页面"""
        import webbrowser
        webbrowser.open("https://portal.opentopography.org/myopentopo")
    
    def _test_api_key(self):
        """测试API密钥"""
        api_key = self.api_key_edit.text().strip()
        if not api_key:
            QMessageBox.warning(self, "警告", "请输入API密钥")
            return
        
        if len(api_key) != 32:
            QMessageBox.warning(self, "警告", f"API密钥长度应为32位，当前为{len(api_key)}位")
            return
        
        self.log("正在验证API密钥...")
        
        try:
            client = OpenTopographyClient(api_key)
            if client.validate_api_key():
                self.log("✓ API密钥验证成功")
                QMessageBox.information(self, "成功", "API密钥验证成功！")
            else:
                self.log("✗ API密钥验证失败")
                QMessageBox.warning(self, "警告", "API密钥可能无效")
        except Exception as e:
            self.log(f"验证失败: {e}")
            QMessageBox.information(self, "提示", "API密钥格式正确，请尝试下载以验证其有效性")
    
    def _get_region_bounds(self) -> Optional[Tuple[float, float, float, float]]:
        """获取当前选择的区域范围"""
        current_tab = self.region_tab.currentIndex()
        
        # 标签页顺序：0=地图绘制, 1=手动输入, 2=行政区划, 3=矢量文件, 4=TIF文件
        
        if current_tab == 0 and WEBENGINE_AVAILABLE:  # 地图绘制
            # 优先使用saved_map_bounds，这是用户绘制的区域
            if self.saved_map_bounds is not None:
                south, north, west, east = self.saved_map_bounds
                return south, north, west, east
            elif self.map_south is not None:
                return self.map_south, self.map_north, self.map_west, self.map_east
            else:
                QMessageBox.warning(self, "警告", "请先在地图上绘制选择区域")
                return None
        
        elif current_tab == 1 or (current_tab == 0 and not WEBENGINE_AVAILABLE):  # 手动输入
            south = self.south_spin.value()
            north = self.north_spin.value()
            west = self.west_spin.value()
            east = self.east_spin.value()
            
            if south >= north or west >= east:
                QMessageBox.warning(self, "警告", "坐标无效: 南纬度必须小于北纬度，西经度必须小于东经度")
                return None
            
            return south, north, west, east
            
        elif current_tab == 2 or (current_tab == 1 and not WEBENGINE_AVAILABLE):  # 行政区划
            province = self.province_combo.currentText()
            city = self.city_combo.currentText()
            district = self.district_combo.currentText()
            
            if province == "选择省份":
                QMessageBox.warning(self, "警告", "请选择省份")
                return None
            
            city = city if city != "选择城市" else None
            district = district if district != "选择区县" else None
            
            if self.admin_selector:
                boundary = self.admin_selector.get_boundary(province, city, district)
                if boundary:
                    west, south, east, north = boundary
                    return south, north, west, east
            
            QMessageBox.warning(self, "警告", "无法获取行政区划边界")
            return None
            
        elif current_tab == 3 or (current_tab == 2 and not WEBENGINE_AVAILABLE):  # 矢量文件
            if self.file_south is None:
                QMessageBox.warning(self, "警告", "请先导入矢量文件")
                return None
            
            return self.file_south, self.file_north, self.file_west, self.file_east
            
        elif current_tab == 4 or (current_tab == 3 and not WEBENGINE_AVAILABLE):  # TIF文件
            if self.file_south is None:
                QMessageBox.warning(self, "警告", "请先导入TIF文件")
                return None
            
            return self.file_south, self.file_north, self.file_west, self.file_east
        
        return None
    
    def start_acquisition(self):
        """开始获取DEM"""
        bounds = self._get_region_bounds()
        if not bounds:
            return
        
        south, north, west, east = bounds
        
        output_path = self.output_file_edit.text().strip()
        if not output_path:
            QMessageBox.warning(self, "警告", "请指定输出文件路径")
            return
        
        # 检查输出文件是否已存在（问题7）
        if os.path.exists(output_path):
            reply = QMessageBox.question(
                self, "确认替换", 
                f"文件已存在:\n{output_path}\n\n是否替换该文件？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return
        
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        
        source_id = self.source_button_group.checkedId()
        
        if source_id == 0:  # 本地SRTM
            folder = self.srtm_folder_edit.text().strip()
            if not folder or not os.path.exists(folder):
                QMessageBox.warning(self, "警告", "请选择有效的SRTM文件夹")
                return
            
            self._start_local_dem_process('SRTM', folder, south, north, west, east, output_path)
            
        elif source_id == 1:  # 本地Copernicus
            folder = self.copernicus_folder_edit.text().strip()
            if not folder or not os.path.exists(folder):
                QMessageBox.warning(self, "警告", "请选择有效的Copernicus文件夹")
                return
            
            self._start_local_dem_process('Copernicus', folder, south, north, west, east, output_path)
            
        elif source_id == 2:  # OpenTopography
            api_key = self.api_key_edit.text().strip()
            if not api_key:
                QMessageBox.warning(self, "警告", "请输入API密钥")
                return
            
            if len(api_key) != 32:
                QMessageBox.warning(self, "警告", f"API密钥长度应为32位，当前为{len(api_key)}位")
                return
            
            dataset_name = self.dem_type_combo.currentData()
            if not dataset_name:
                QMessageBox.warning(self, "警告", "请选择DEM类型")
                return
            
            self._start_opentopo_download(dataset_name, api_key, south, north, west, east, output_path)
    
    def _start_local_dem_process(self, dem_type, folder, south, north, west, east, output_path):
        """启动本地DEM处理"""
        merge_only = self.merge_only_checkbox.isChecked()
        clip_to_bounds = self.clip_to_bounds_checkbox.isChecked()
        
        # 检查是否需要重采样至TIF
        reference_tif = None
        if self.resample_to_tif_checkbox.isChecked() and self.reference_tif_path:
            reference_tif = self.reference_tif_path
        
        self.local_worker = LocalDEMWorker(
            folder, dem_type, south, north, west, east, output_path,
            merge_only=merge_only, clip_to_bounds=clip_to_bounds,
            reference_tif=reference_tif
        )
        
        self.local_worker.progress_updated.connect(self._on_progress_updated)
        self.local_worker.process_completed.connect(self._on_local_completed)
        self.local_worker.error_occurred.connect(self._on_error)
        self.local_worker.files_found.connect(self._on_files_found)
        
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        
        self.local_worker.start()
        self.log(f"开始处理本地 {dem_type} DEM 数据...")
    
    def _on_files_found(self, files: List[str]):
        """处理找到的文件列表"""
        if files:
            self.log(f"使用以下本地DEM文件:")
            for f in files:
                self.log(f"  - {os.path.basename(f)}")
    
    def _start_opentopo_download(self, dataset_name, api_key, south, north, west, east, output_path):
        """启动OpenTopography下载"""
        client = OpenTopographyClient(api_key)
        
        self.download_worker = DownloadWorker(
            client, dataset_name, south, north, west, east, output_path
        )
        
        self.download_worker.progress_updated.connect(self._on_progress_updated)
        self.download_worker.download_completed.connect(self._on_download_completed)
        self.download_worker.error_occurred.connect(self._on_error)
        
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        
        self.download_worker.start()
        self.log(f"开始从OpenTopography下载 {dataset_name} 数据...")
    
    def _on_progress_updated(self, progress, message):
        """进度更新"""
        # 只输出到日志，不再使用进度条
        if message:
            self.log(f"[{progress}%] {message}")
    
    def _on_local_completed(self, output_path):
        """本地处理完成"""
        self._reset_ui()
        self.log(f"✓ DEM处理完成: {output_path}")
        
        # 显示范围对比并加载DEM到地图
        self._show_dem_result_and_load_to_map(output_path)
        
        QMessageBox.information(self, "完成", f"DEM数据已保存至:\n{output_path}")
    
    def _on_download_completed(self, output_path):
        """下载完成"""
        self._reset_ui()
        self.log(f"✓ 下载完成: {output_path}")
        
        # 显示范围对比并加载DEM到地图
        self._show_dem_result_and_load_to_map(output_path)
        
        QMessageBox.information(self, "完成", f"DEM数据已保存至:\n{output_path}")
    
    def _on_error(self, error_msg):
        """错误处理"""
        self._reset_ui()
        self.log(f"✗ 错误: {error_msg}")
        QMessageBox.critical(self, "错误", error_msg)
    
    def _show_dem_result_and_load_to_map(self, dem_path: str):
        """显示DEM获取结果对比"""
        try:
            bounds_list = []
            
            # 1. 获取用户选择的区域（蓝色）
            selected_bounds = None
            if self.saved_map_bounds and self.region_tab.currentIndex() == 0:
                 selected_bounds = self.saved_map_bounds
            else:
                 selected_bounds = self._get_region_bounds()
            
            if selected_bounds:
                s, n, w, e = selected_bounds
                bounds_list.append({
                    'south': s, 'north': n, 'west': w, 'east': e,
                    'color': '#3388ff', 'name': '选择区域'
                })
                self.log(f"选择区域: 南={s:.4f}, 北={n:.4f}, 西={w:.4f}, 东={e:.4f}")
            
            # 2. 获取实际DEM的边界（红色）
            if UTILS_AVAILABLE and os.path.exists(dem_path):
                # 从TIF提取边界
                result_bounds = extract_bounding_box_from_raster(dem_path)
                if result_bounds:
                    w, s, e, n = result_bounds  # result is west, south, east, north
                    bounds_list.append({
                        'south': s, 'north': n, 'west': w, 'east': e,
                        'color': '#e74c3c', 'name': 'DEM范围'
                    })
                    self.log(f"DEM边界: 南={s:.4f}, 北={n:.4f}, 西={w:.4f}, 东={e:.4f}")
            
            # 在地图上显示边界框（使用legend）
            if bounds_list and self.map_widget.is_available():
                self.map_widget.show_bounds_with_legend(bounds_list)
                # 切换到地图标签页以便查看
                if WEBENGINE_AVAILABLE:
                    self.region_tab.setCurrentIndex(0)
                self.log("✓ 边界已显示在地图上（见右下角图例）")
            
            self.log(f"✓ DEM文件已保存: {dem_path}")
                
        except Exception as e:
            self.log(f"✗ 显示范围失败: {e}")
            import traceback
            traceback.print_exc()
    
    def _reset_ui(self):
        """重置UI状态"""
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
    
    def stop_acquisition(self):
        """停止获取"""
        reply = QMessageBox.question(
            self, "确认", "是否停止当前操作?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            if self.download_worker:
                self.download_worker.stop()
                self.download_worker = None
            
            if self.local_worker:
                self.local_worker.stop()
                self.local_worker = None
            
            self._reset_ui()
            self.log("操作已停止")
    
    def closeEvent(self, event):
        """关闭对话框时保存设置"""
        self._save_settings()
        super().closeEvent(event)
    
    def showEvent(self, event):
        """窗口显示时设置splitter比例"""
        super().showEvent(event)
        # 延迟设置splitter比例，确保窗口已完成布局
        # 使用较长的延迟确保布局完成
        QTimer.singleShot(200, self._set_splitter_sizes)
        # 再次延迟设置以确保生效
        QTimer.singleShot(500, self._set_splitter_sizes)
    
    def resizeEvent(self, event):
        """窗口大小改变时保持splitter比例"""
        super().resizeEvent(event)
        # 如果是首次显示后的resize，保持5:2比例
        if hasattr(self, '_splitter_initialized') and self._splitter_initialized:
            pass  # 允许用户自由调整后不再强制比例
        else:
            QTimer.singleShot(50, self._set_splitter_sizes)
    
    def _set_splitter_sizes(self):
        """设置splitter比例为3:2"""
        if hasattr(self, 'splitter') and self.splitter:
            # 设置伸缩因子，确保拖动窗口大小时保持比例
            self.splitter.setStretchFactor(0, 3)
            self.splitter.setStretchFactor(1, 2)

            total_width = self.splitter.width()
            if total_width > 100:  # 确保窗口已经有合理大小
                left_width = int(total_width * 3 / 5)
                right_width = total_width - left_width
                self.splitter.setSizes([left_width, right_width])
                self._splitter_initialized = True
    
    def _on_output_option_changed(self):
        """输出选项改变时的互斥处理"""
        sender = self.sender()
        if sender and sender.isChecked():
            # 取消其他选项的勾选
            if sender == self.merge_only_checkbox:
                self.clip_to_bounds_checkbox.setChecked(False)
                self.resample_to_tif_checkbox.setChecked(False)
            elif sender == self.clip_to_bounds_checkbox:
                self.merge_only_checkbox.setChecked(False)
                self.resample_to_tif_checkbox.setChecked(False)
            elif sender == self.resample_to_tif_checkbox:
                self.merge_only_checkbox.setChecked(False)
                self.clip_to_bounds_checkbox.setChecked(False)
    
    def dragEnterEvent(self, event: QDragEnterEvent):
        """拖动进入事件"""
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls:
                file_path = urls[0].toLocalFile().lower()
                # 支持矢量文件和TIF文件
                if any(file_path.endswith(ext) for ext in ['.shp', '.geojson', '.json', '.kml', '.kmz', '.gpkg', '.tif', '.tiff']):
                    event.acceptProposedAction()
                    return
        event.ignore()
    
    def dropEvent(self, event: QDropEvent):
        """拖放事件"""
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls:
                file_path = urls[0].toLocalFile()
                ext = os.path.splitext(file_path)[1].lower()
                
                if ext in ['.shp', '.geojson', '.json', '.kml', '.kmz', '.gpkg']:
                    # 矢量文件
                    self._load_vector_file(file_path)
                    # 切换到矢量文件标签页
                    self.region_tab.setCurrentIndex(3 if WEBENGINE_AVAILABLE else 2)
                elif ext in ['.tif', '.tiff']:
                    # TIF文件
                    self._load_tif_file(file_path)
                    # 切换到TIF文件标签页
                    self.region_tab.setCurrentIndex(4 if WEBENGINE_AVAILABLE else 3)
                
                event.acceptProposedAction()
                return
        event.ignore()
    
    def _load_vector_file(self, file_path: str):
        """加载矢量文件"""
        self.vector_file_edit.setText(file_path)
        
        try:
            if UTILS_AVAILABLE:
                result = extract_bounding_box_from_vector(file_path)
                if result:
                    west, south, east, north = result
                    
                    self.file_south = south
                    self.file_north = north
                    self.file_west = west
                    self.file_east = east
                    
                    self.vector_coord_label.setText(
                        f"边界: 西={west:.6f}, 东={east:.6f}, 南={south:.6f}, 北={north:.6f}"
                    )
                    
                    area = calculate_area_km2(south, north, west, east)
                    self.vector_area_label.setText(f"面积: {area:.2f} km²")
                    
                    self.log(f"矢量文件加载成功: {os.path.basename(file_path)}")
                    
                    # 在地图上显示
                    self._show_bounds_on_map(south, north, west, east)
                else:
                    QMessageBox.warning(self, "警告", "无法从矢量文件提取边界")
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"读取矢量文件失败: {e}")
            self.log(f"读取矢量文件失败: {e}")
    
    def _load_tif_file(self, file_path: str):
        """加载TIF文件"""
        self.tif_file_edit.setText(file_path)
        self.reference_tif_path = file_path
        
        try:
            if UTILS_AVAILABLE:
                result = extract_bounding_box_from_raster(file_path)
                if result:
                    west, south, east, north = result
                    
                    self.file_south = south
                    self.file_north = north
                    self.file_west = west
                    self.file_east = east
                    
                    self.tif_coord_label.setText(
                        f"边界(WGS84): 西={west:.6f}, 东={east:.6f}, 南={south:.6f}, 北={north:.6f}"
                    )
                    
                    area = calculate_area_km2(south, north, west, east)
                    self.tif_area_label.setText(f"面积: {area:.2f} km²")
                    
                    self.log(f"TIF文件加载成功: {os.path.basename(file_path)}")
                    
                    # 启用重采样选项
                    current_tab = self.region_tab.currentIndex()
                    is_tif_tab = (current_tab == 4) or (current_tab == 3 and not WEBENGINE_AVAILABLE)
                    if is_tif_tab:
                        self.resample_to_tif_checkbox.setEnabled(True)
                    
                    # 在地图上显示
                    self._show_bounds_on_map(south, north, west, east)
                else:
                    QMessageBox.warning(self, "警告", "无法从TIF文件提取边界")
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"读取TIF文件失败: {e}")
            self.log(f"读取TIF文件失败: {e}")