"""
栅格数据获取工具主界面。
"""

from __future__ import annotations

import os
import tempfile
import traceback
import webbrowser
from typing import List, Optional, Tuple

from PySide6.QtCore import QSettings, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QProgressDialog, QRadioButton, QSplitter, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from src.dialogs.local_raster_source_dialog import LocalRasterSourceConfigDialog
from src.dialogs.online_raster_source_dialog import OnlineRasterSourceConfigDialog
from src.utils import (
    DATASETS_CONFIG, LocalRasterProcessor, OpenTopographyClient, OpenTopographyError,
    RasterSourceConfigManager, build_rule_preview, calculate_area_km2,
    extract_bounding_box_from_raster, extract_bounding_box_from_vector,
    extract_bounding_box_from_gamma_par, extract_gamma_par_corners,
    AdministrativeBoundarySelector, ADMIN_BOUNDARY_AVAILABLE,
)

try:
    from src.widgets import LeafletMapWidget, WEBENGINE_AVAILABLE
except Exception:
    LeafletMapWidget = None
    WEBENGINE_AVAILABLE = False


class OnlineRasterDownloadWorker(QThread):
    progress_updated = Signal(int, str)
    download_completed = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, client, dataset_name, south, north, west, east, output_path):
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
        try:
            self.progress_updated.emit(10, "正在验证区域...")
            validation = self.client.validate_area_for_dataset(self.south, self.north, self.west, self.east, self.dataset_name)
            if not validation["is_within_limit"]:
                self.error_occurred.emit(f"区域面积超出数据集限制: {validation['area']:.0f} / {validation['limit']} km²")
                return
            self.progress_updated.emit(30, "开始下载在线栅格数据...")
            result = self.client.download(
                dataset_name=self.dataset_name,
                south=self.south,
                north=self.north,
                west=self.west,
                east=self.east,
                output_path=self.output_path,
                gui_logger=lambda msg: self.progress_updated.emit(50, msg),
                is_running=lambda: self.is_running,
            )
            if result:
                self.progress_updated.emit(100, "下载完成")
                self.download_completed.emit(result)
            else:
                self.error_occurred.emit("下载失败")
        except OpenTopographyError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"下载错误: {exc}")

    def stop(self):
        self.is_running = False


class OnlineSourceTestWorker(QThread):
    progress_updated = Signal(int, str)
    test_completed = Signal(bool, str)

    def __init__(self, api_key: str):
        super().__init__()
        self.api_key = api_key
        self.is_running = True

    def run(self):
        try:
            client = OpenTopographyClient(self.api_key)
            ok = client.validate_api_key(
                progress_callback=lambda p, msg: self.progress_updated.emit(p, msg),
                is_running=lambda: self.is_running,
            )
            self.test_completed.emit(ok, "API key 测试通过。" if ok else "API key 无效或认证失败。")
        except Exception as exc:
            self.test_completed.emit(False, f"测试失败: {exc}")

    def stop(self):
        self.is_running = False


class LocalRasterWorker(QThread):
    progress_updated = Signal(int, str)
    process_completed = Signal(str)
    error_occurred = Signal(str)
    files_found = Signal(list)

    def __init__(self, source_config, south, north, west, east, output_path, clip_to_bounds=False, reference_tif=None, resample_method="双线性插值"):
        super().__init__()
        self.source_config = source_config
        self.south = south
        self.north = north
        self.west = west
        self.east = east
        self.output_path = output_path
        self.clip_to_bounds = clip_to_bounds
        self.reference_tif = reference_tif
        self.resample_method = resample_method
        self.is_running = True

    def run(self):
        try:
            processor = LocalRasterProcessor()
            if not self.is_running:
                return
            self.progress_updated.emit(10, "正在计算所需瓦片...")
            found_files, missing_tiles, details = processor.collect_tiles(self.source_config, self.south, self.north, self.west, self.east)
            if not self.is_running:
                return
            self.files_found.emit(found_files)
            for detail in details[:8]:
                self.progress_updated.emit(18, detail)
            if not found_files:
                self.error_occurred.emit(f"未找到任何栅格瓦片: {', '.join(missing_tiles[:5])}")
                return
            if missing_tiles and not self.source_config.allow_missing_tiles:
                self.error_occurred.emit(f"存在缺失瓦片且当前配置不允许继续: {', '.join(missing_tiles[:5])}")
                return
            temp_merged = os.path.join(tempfile.gettempdir(), "toolbox_raster_merge_temp.tif")
            self.progress_updated.emit(45, "正在合并栅格瓦片...")
            if not processor.merge_tiles(found_files, temp_merged):
                self.error_occurred.emit("栅格合并失败")
                return
            if self.reference_tif and os.path.exists(self.reference_tif):
                ok = processor.clip_and_resample_to_reference(
                    temp_merged, self.reference_tif, self.output_path, resample_method=self.resample_method
                )
            elif self.clip_to_bounds:
                ok = processor.clip_to_bounds(
                    temp_merged, self.output_path, self.south, self.north, self.west, self.east, resample_method=self.resample_method
                )
            else:
                ok = processor.merge_tiles([temp_merged], self.output_path)
            try:
                if os.path.exists(temp_merged):
                    os.remove(temp_merged)
            except OSError:
                pass
            if not ok:
                self.error_occurred.emit("输出栅格失败")
                return
            self.progress_updated.emit(100, "处理完成")
            self.process_completed.emit(self.output_path)
        except Exception as exc:
            traceback.print_exc()
            self.error_occurred.emit(str(exc))

    def stop(self):
        self.is_running = False


class RasterDataAcquisitionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("栅格数据获取工具")
        self.resize(1180, 700)
        self.setMinimumWidth(1120)

        self.config_manager = RasterSourceConfigManager()
        self.settings = QSettings(str(self.config_manager.config_dir / "raster_acquisition.ini"), QSettings.IniFormat)
        self.download_worker = None
        self.local_worker = None
        self.online_test_worker = None
        self.online_test_progress_dialog = None
        self.reference_tif_path = None
        self.vector_bounds = None
        self.tif_bounds = None
        self.gamma_bounds = None
        self.saved_map_bounds = None
        self.admin_selector = None
        self._last_vector_dir = self.settings.value("last_vector_dir", "")
        self._last_tif_dir = self.settings.value("last_tif_dir", "")
        self._last_gamma_par_dir = self.settings.value("last_gamma_par_dir", "")
        self._last_output_dir = self.settings.value("last_output_dir", "")

        self._create_ui()
        self._init_admin_data()
        self._load_sources()
        self._load_settings()
        self._update_source_mode()
        self.setAcceptDrops(True)
        self.log("栅格数据获取工具已就绪")

    def _create_ui(self):
        layout = QHBoxLayout(self)
        self.splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(self.splitter)

        left = QGroupBox("区域预览")
        left_layout = QVBoxLayout(left)
        self.map_widget = LeafletMapWidget(center_lat=35, center_lng=105, zoom=4) if LeafletMapWidget else None
        if self.map_widget:
            self.map_widget.boundsDrawn.connect(self._on_map_bounds_drawn)
            self.map_widget.boundsCleared.connect(lambda: setattr(self, "saved_map_bounds", None))
            left_layout.addWidget(self.map_widget)
        else:
            left_layout.addWidget(QLabel("地图功能不可用"))
        self.splitter.addWidget(left)

        right = QWidget()
        right.setMinimumWidth(520)
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(self._create_region_group())
        right_layout.addWidget(self._create_source_group())
        right_layout.addWidget(self._create_output_group())
        right_layout.addWidget(self._create_log_group())
        button_row = QHBoxLayout()
        self.start_button = QPushButton("开始获取")
        self.start_button.clicked.connect(self.start_acquisition)
        button_row.addWidget(self.start_button)
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_acquisition)
        button_row.addWidget(self.stop_button)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        button_row.addWidget(close_btn)
        right_layout.addLayout(button_row)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 4)

    def _create_region_group(self):
        group = QGroupBox("区域选择")
        layout = QVBoxLayout(group)
        self.region_tab = QTabWidget()
        if WEBENGINE_AVAILABLE and self.map_widget:
            map_tab = QWidget()
            map_layout = QVBoxLayout(map_tab)
            self.map_info_label = QLabel("在左侧地图绘制矩形区域")
            self.map_area_label = QLabel("区域面积: -- km²")
            map_layout.addWidget(self.map_info_label)
            map_layout.addWidget(self.map_area_label)
            map_layout.addStretch()
            self.region_tab.addTab(map_tab, "地图绘制")
        manual_tab = QWidget()
        manual_layout = QGridLayout(manual_tab)
        self.north_spin = QDoubleSpinBox(); self.north_spin.setRange(-90, 90); self.north_spin.setDecimals(6)
        self.south_spin = QDoubleSpinBox(); self.south_spin.setRange(-90, 90); self.south_spin.setDecimals(6)
        self.west_spin = QDoubleSpinBox(); self.west_spin.setRange(-180, 180); self.west_spin.setDecimals(6)
        self.east_spin = QDoubleSpinBox(); self.east_spin.setRange(-180, 180); self.east_spin.setDecimals(6)
        for spin in [self.north_spin, self.south_spin, self.west_spin, self.east_spin]:
            spin.valueChanged.connect(self._update_manual_area)
        self.manual_area_label = QLabel("区域面积: -- km²")
        manual_layout.addWidget(QLabel("北纬"), 0, 0); manual_layout.addWidget(self.north_spin, 0, 1)
        manual_layout.addWidget(QLabel("南纬"), 0, 2); manual_layout.addWidget(self.south_spin, 0, 3)
        manual_layout.addWidget(QLabel("西经"), 1, 0); manual_layout.addWidget(self.west_spin, 1, 1)
        manual_layout.addWidget(QLabel("东经"), 1, 2); manual_layout.addWidget(self.east_spin, 1, 3)
        manual_layout.addWidget(self.manual_area_label, 2, 0, 1, 4)
        self.region_tab.addTab(manual_tab, "手动输入")
        self.region_tab.addTab(self._create_admin_tab(), "行政区划")
        self.region_tab.addTab(self._create_file_tab("矢量文件", self._browse_vector_file, "vector"), "矢量文件")
        self.region_tab.addTab(self._create_file_tab("TIF文件", self._browse_tif_file, "tif"), "TIF文件")
        self.region_tab.addTab(self._create_file_tab("GAMMA par文件", self._browse_gamma_par_file, "gamma"), "GAMMA par文件")
        self.region_tab.currentChanged.connect(self._on_region_tab_changed)
        layout.addWidget(self.region_tab)
        return group

    def _create_admin_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        row = QHBoxLayout()
        self.province_combo = QComboBox()
        self.city_combo = QComboBox()
        self.district_combo = QComboBox()
        self.province_combo.currentTextChanged.connect(self._on_province_changed)
        self.city_combo.currentTextChanged.connect(self._on_city_changed)
        self.district_combo.currentTextChanged.connect(self._on_district_changed)
        row.addWidget(QLabel("省份")); row.addWidget(self.province_combo)
        row.addWidget(QLabel("城市")); row.addWidget(self.city_combo)
        row.addWidget(QLabel("区县")); row.addWidget(self.district_combo)
        layout.addLayout(row)
        self.admin_bounds_label = QLabel("边界坐标: --")
        self.admin_area_label = QLabel("区域面积: -- km²")
        layout.addWidget(self.admin_bounds_label)
        layout.addWidget(self.admin_area_label)
        return widget

    def _create_file_tab(self, label_text, browse_handler, file_type):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        left = QVBoxLayout()
        row = QHBoxLayout()
        edit = QLineEdit(); edit.setReadOnly(True)
        if file_type == "vector":
            self.vector_file_edit = edit
            self.vector_info_label = QLabel("边界坐标: --")
            self.vector_area_label = QLabel("区域面积: -- km²")
        elif file_type == "tif":
            self.tif_file_edit = edit
            self.tif_info_label = QLabel("边界坐标(WGS84): --")
            self.tif_area_label = QLabel("区域面积: -- km²")
        else:
            self.gamma_par_file_edit = edit
            self.gamma_par_info_label = QLabel("边界坐标(WGS84): --")
            self.gamma_par_area_label = QLabel("区域面积: -- km²")
            self.gamma_par_info_label.setWordWrap(True)
            self.gamma_par_area_label.setWordWrap(True)
        row.addWidget(QLabel(label_text)); row.addWidget(edit)
        btn = QPushButton("浏览"); btn.clicked.connect(browse_handler)
        row.addWidget(btn)
        left.addLayout(row)
        if file_type == "vector":
            left.addWidget(self.vector_info_label)
            left.addWidget(self.vector_area_label)
        elif file_type == "tif":
            left.addWidget(self.tif_info_label)
            left.addWidget(self.tif_area_label)
        else:
            left.addWidget(self.gamma_par_info_label)
            left.addWidget(self.gamma_par_area_label)
        layout.addLayout(left, 1)
        frame = QFrame(); frame.setFixedSize(120, 80); frame.setStyleSheet("border:1px dashed #777;")
        frame_layout = QVBoxLayout(frame); frame_layout.addWidget(QLabel("拖放文件到此处"))
        layout.addWidget(frame)
        return widget

    def _create_source_group(self):
        group = QGroupBox("数据源选择")
        layout = QVBoxLayout(group)
        self.source_mode_group = QButtonGroup(self)
        row1 = QHBoxLayout()
        self.local_radio = QRadioButton("本地数据源")
        self.local_radio.toggled.connect(self._update_source_mode)
        self.source_mode_group.addButton(self.local_radio, 0)
        row1.addWidget(self.local_radio)
        self.local_source_combo = QComboBox(); self.local_source_combo.currentTextChanged.connect(self._update_local_summary)
        row1.addWidget(self.local_source_combo)
        local_cfg = QPushButton("配置"); local_cfg.clicked.connect(self._open_local_config); row1.addWidget(local_cfg)
        local_test = QPushButton("测试"); local_test.clicked.connect(self._test_local_source); row1.addWidget(local_test)
        layout.addLayout(row1)
        self.local_summary_label = QTextEdit()
        self.local_summary_label.setReadOnly(True)
        self.local_summary_label.setMinimumHeight(92)
        self.local_summary_label.setMaximumHeight(150)
        layout.addWidget(self.local_summary_label)

        row2 = QHBoxLayout()
        self.online_radio = QRadioButton("在线数据源")
        self.source_mode_group.addButton(self.online_radio, 1)
        self.online_radio.toggled.connect(self._update_source_mode)
        row2.addWidget(self.online_radio)
        self.online_source_combo = QComboBox(); self.online_source_combo.currentTextChanged.connect(self._update_online_summary)
        row2.addWidget(self.online_source_combo)
        online_cfg = QPushButton("配置"); online_cfg.clicked.connect(self._open_online_config); row2.addWidget(online_cfg)
        online_test = QPushButton("测试"); online_test.clicked.connect(self._test_online_source); row2.addWidget(online_test)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("在线数据集"))
        self.online_dataset_combo = QComboBox()
        for key, info in DATASETS_CONFIG.items():
            self.online_dataset_combo.addItem(f"{key} ({info['resolution']})", key)
        row3.addWidget(self.online_dataset_combo)
        api_btn = QPushButton("获取 API key"); api_btn.clicked.connect(lambda: webbrowser.open("https://portal.opentopography.org/myopentopo"))
        row3.addWidget(api_btn)
        layout.addLayout(row3)
        self.online_summary_label = QTextEdit()
        self.online_summary_label.setReadOnly(True)
        self.online_summary_label.setMinimumHeight(68)
        self.online_summary_label.setMaximumHeight(120)
        layout.addWidget(self.online_summary_label)
        return group

    def _create_output_group(self):
        group = QGroupBox("输出设置")
        layout = QVBoxLayout(group)
        row = QHBoxLayout()
        row.addWidget(QLabel("输出文件"))
        self.output_file_edit = QLineEdit()
        self.output_file_edit.setPlaceholderText("选择输出栅格文件路径...")
        row.addWidget(self.output_file_edit)
        btn = QPushButton("浏览"); btn.clicked.connect(self._browse_output_file); row.addWidget(btn)
        layout.addLayout(row)
        option_row = QHBoxLayout()
        self.merge_only_checkbox = QCheckBox("仅合并瓦片"); self.merge_only_checkbox.setChecked(True); self.merge_only_checkbox.toggled.connect(self._on_output_option_changed)
        self.clip_to_bounds_checkbox = QCheckBox("裁剪至输入范围"); self.clip_to_bounds_checkbox.toggled.connect(self._on_output_option_changed)
        self.resample_to_tif_checkbox = QCheckBox("裁剪重采样至相同范围和分辨率"); self.resample_to_tif_checkbox.toggled.connect(self._on_output_option_changed)
        option_row.addWidget(self.merge_only_checkbox); option_row.addWidget(self.clip_to_bounds_checkbox); option_row.addWidget(self.resample_to_tif_checkbox)
        layout.addLayout(option_row)
        return group

    def _create_log_group(self):
        group = QGroupBox("日志")
        layout = QVBoxLayout(group)
        self.log_text = QTextEdit(); self.log_text.setReadOnly(True); self.log_text.setMinimumHeight(140)
        layout.addWidget(self.log_text)
        return group

    def _init_admin_data(self):
        self.province_combo.clear()
        self.city_combo.clear()
        self.district_combo.clear()
        self.province_combo.addItem("选择省份")
        self.city_combo.addItem("选择城市")
        self.district_combo.addItem("选择区县")
        if not ADMIN_BOUNDARY_AVAILABLE:
            return
        try:
            self.admin_selector = AdministrativeBoundarySelector()
            if self.admin_selector and self.admin_selector.is_available():
                for province in self.admin_selector.get_provinces():
                    self.province_combo.addItem(province)
        except Exception:
            self.admin_selector = None

    def _on_province_changed(self, province_name):
        if not self.admin_selector or province_name == "选择省份":
            return
        self.city_combo.clear()
        self.city_combo.addItem("选择城市")
        for city in self.admin_selector.get_cities(province_name):
            self.city_combo.addItem(city)
        self.district_combo.clear()
        self.district_combo.addItem("选择区县")
        self._update_admin_bounds(province_name, None, None)

    def _on_city_changed(self, city_name):
        province = self.province_combo.currentText()
        if not self.admin_selector or province == "选择省份" or city_name == "选择城市":
            return
        self.district_combo.clear()
        self.district_combo.addItem("选择区县")
        for district in self.admin_selector.get_districts(province, city_name):
            self.district_combo.addItem(district)
        self._update_admin_bounds(province, city_name, None)

    def _on_district_changed(self, district_name):
        province = self.province_combo.currentText()
        city = self.city_combo.currentText()
        if not self.admin_selector or province == "选择省份":
            return
        city = None if city == "选择城市" else city
        district = None if district_name == "选择区县" else district_name
        self._update_admin_bounds(province, city, district)

    def _update_admin_bounds(self, province, city, district):
        if not self.admin_selector:
            return
        boundary = self.admin_selector.get_boundary(province, city, district)
        if not boundary:
            return
        west, south, east, north = boundary
        self.admin_bounds_label.setText(f"边界: 西={west:.6f}, 东={east:.6f}, 南={south:.6f}, 北={north:.6f}")
        self.admin_area_label.setText(f"区域面积: {calculate_area_km2(south, north, west, east):.2f} km²")
        self._show_bounds_on_map(south, north, west, east)

    def _load_sources(self):
        state = self.config_manager.get_ui_state()
        self.local_source_combo.clear()
        self.online_source_combo.clear()
        for source in self.config_manager.get_local_sources():
            self.local_source_combo.addItem(source.name)
        for source in self.config_manager.get_online_sources():
            self.online_source_combo.addItem(source.name)
        local_name = state.get("selected_local_source", "SRTM")
        online_name = state.get("selected_online_source", "OpenTopography")
        if self.local_source_combo.findText(local_name) >= 0:
            self.local_source_combo.setCurrentText(local_name)
        if self.online_source_combo.findText(online_name) >= 0:
            self.online_source_combo.setCurrentText(online_name)
        if state.get("source_mode", "online") == "local":
            self.local_radio.setChecked(True)
        else:
            self.online_radio.setChecked(True)
        self._update_local_summary()
        self._update_online_summary()

    def _load_settings(self):
        # 输出选项不持久化：每次打开都回到默认“仅合并瓦片”
        self.merge_only_checkbox.setChecked(True)
        self.clip_to_bounds_checkbox.setChecked(False)
        self.resample_to_tif_checkbox.setChecked(False)
        self._update_resample_option_state()

    def _save_settings(self):
        self.settings.setValue("last_vector_dir", self._last_vector_dir)
        self.settings.setValue("last_tif_dir", self._last_tif_dir)
        self.settings.setValue("last_gamma_par_dir", self._last_gamma_par_dir)
        self.settings.setValue("last_output_dir", self._last_output_dir)
        self.config_manager.set_ui_state_value("source_mode", "local" if self.local_radio.isChecked() else "online")
        self.config_manager.set_ui_state_value("selected_local_source", self.local_source_combo.currentText())
        self.config_manager.set_ui_state_value("selected_online_source", self.online_source_combo.currentText())

    def _update_local_summary(self):
        source = self.config_manager.get_local_source(self.local_source_combo.currentText())
        if not source:
            self.local_summary_label.setPlainText("未选择本地数据源")
            return
        self.local_summary_label.setPlainText(
            f"根目录: {source.root_dir or '未设置'}\n"
            f"经纬度间隔: {source.latitude_interval} x {source.longitude_interval}\n"
            f"命名锚点: {source.naming_anchor}\n"
            f"是否压缩包: {'是' if source.is_archive else '否'}\n"
            f"插值方式: {source.resample_method}\n"
            f"缺失瓦片继续处理: {'是' if source.allow_missing_tiles else '否'}\n"
            f"规则预览: {build_rule_preview(source)}"
        )

    def _update_online_summary(self):
        source = self.config_manager.get_online_source(self.online_source_combo.currentText())
        if not source:
            self.online_summary_label.setPlainText("未选择在线数据源")
            return
        if self.online_dataset_combo.findData(source.default_dataset) >= 0:
            self.online_dataset_combo.setCurrentIndex(self.online_dataset_combo.findData(source.default_dataset))
        self.online_summary_label.setPlainText(
            f"平台名称: {source.platform_type}\n"
            f"默认产品/数据集: {source.default_dataset or '未设置'}\n"
            f"API key: {'已配置' if source.api_key else '未配置'}"
        )

    def _update_source_mode(self):
        local_enabled = self.local_radio.isChecked()
        self.local_source_combo.setEnabled(local_enabled)
        self.online_source_combo.setEnabled(not local_enabled)
        self.online_dataset_combo.setEnabled(not local_enabled)
        # 输出选项允许先选，真正执行时再按数据源判断是否生效
        self.merge_only_checkbox.setEnabled(True)
        self.clip_to_bounds_checkbox.setEnabled(True)
        self._update_resample_option_state()

    def _open_local_config(self):
        previous_name = self.local_source_combo.currentText()
        dialog = LocalRasterSourceConfigDialog(self, manager=self.config_manager, selected_name=self.local_source_combo.currentText())
        dialog.exec()
        self._load_sources()
        self.local_source_combo.setCurrentText(dialog.selected_name or previous_name)

    def _open_online_config(self):
        previous_name = self.online_source_combo.currentText()
        dialog = OnlineRasterSourceConfigDialog(self, manager=self.config_manager, selected_name=self.online_source_combo.currentText())
        dialog.exec()
        self._load_sources()
        self.online_source_combo.setCurrentText(dialog.selected_name or previous_name)

    def _test_local_source(self):
        source = self.config_manager.get_local_source(self.local_source_combo.currentText())
        if not source:
            QMessageBox.warning(self, "提示", "请先选择本地数据源")
            return
        processor = LocalRasterProcessor()
        point = source.last_test_point or (39.9042, 116.4074)
        result = processor.test_config(source, point[0], point[1], processor.get_raster_extent_wgs84)
        QMessageBox.information(self, "测试结果", "\n".join([result.message] + result.details))
        self.log(f"本地数据源测试: {source.name} -> {result.message}")

    def _test_online_source(self):
        source = self.config_manager.get_online_source(self.online_source_combo.currentText())
        if not source:
            QMessageBox.warning(self, "提示", "请先选择在线数据源")
            return
        if not source.api_key:
            QMessageBox.warning(self, "提示", "当前在线数据源未配置 API key")
            return
        if self.online_test_worker and self.online_test_worker.isRunning():
            QMessageBox.information(self, "提示", "在线数据源测试正在进行，请稍候。")
            return
        self.online_test_progress_dialog = QProgressDialog("准备测试在线数据源...", "取消", 0, 100, self)
        self.online_test_progress_dialog.setWindowTitle("测试在线数据源")
        self.online_test_progress_dialog.setWindowModality(Qt.WindowModal)
        self.online_test_progress_dialog.setAutoClose(False)
        self.online_test_progress_dialog.setAutoReset(False)
        self.online_test_progress_dialog.setValue(0)
        self.online_test_progress_dialog.show()

        self.online_test_worker = OnlineSourceTestWorker(source.api_key)
        self.online_test_worker.progress_updated.connect(self._on_online_test_progress)
        self.online_test_worker.test_completed.connect(
            lambda ok, message, source_name=source.name: self._on_online_test_finished(source_name, ok, message)
        )
        self.online_test_progress_dialog.canceled.connect(self.online_test_worker.stop)
        self.online_test_worker.start()
        self.log(f"开始测试在线数据源: {source.name}")

    def _on_online_test_progress(self, progress: int, message: str):
        if self.online_test_progress_dialog:
            self.online_test_progress_dialog.setLabelText(message)
            self.online_test_progress_dialog.setValue(max(0, min(progress, 100)))
        self.log(f"[测试 {progress}%] {message}")

    def _on_online_test_finished(self, source_name: str, ok: bool, message: str):
        if self.online_test_progress_dialog:
            self.online_test_progress_dialog.setValue(100)
            self.online_test_progress_dialog.close()
            self.online_test_progress_dialog = None
        self.online_test_worker = None
        self.log(f"在线数据源测试: {source_name} -> {message}")
        if ok:
            QMessageBox.information(self, "测试结果", message)
        else:
            QMessageBox.warning(self, "测试结果", message)

    def _browse_output_file(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "保存栅格文件", self._last_output_dir, "GeoTIFF (*.tif)")
        if file_path:
            if not file_path.lower().endswith(".tif"):
                file_path += ".tif"
            self.output_file_edit.setText(file_path)
            self._last_output_dir = os.path.dirname(file_path)

    def _browse_vector_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择矢量文件", self._last_vector_dir, "矢量文件 (*.json *.geojson *.kml *.kmz *.shp *.gpkg)")
        if not file_path:
            return
        self._load_vector_file(file_path)

    def _browse_tif_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择 TIF 文件", self._last_tif_dir, "TIF文件 (*.tif *.tiff)")
        if not file_path:
            return
        self._load_tif_file(file_path)

    def _browse_gamma_par_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择 GAMMA par 文件", self._last_gamma_par_dir, "PAR文件 (*.par);;所有文件 (*.*)")
        if not file_path:
            return
        self._load_gamma_par_file(file_path)

    def _load_vector_file(self, file_path: str):
        self._last_vector_dir = os.path.dirname(file_path)
        self.vector_file_edit.setText(file_path)
        result = extract_bounding_box_from_vector(file_path)
        if not result:
            QMessageBox.warning(self, "提示", "无法提取矢量范围")
            return
        self._set_file_bounds(result, vector=True)

    def _load_tif_file(self, file_path: str):
        self._last_tif_dir = os.path.dirname(file_path)
        self.tif_file_edit.setText(file_path)
        self.reference_tif_path = file_path
        result = extract_bounding_box_from_raster(file_path)
        if not result:
            QMessageBox.warning(self, "提示", "无法提取 TIF 范围")
            return
        self._set_file_bounds(result, vector=False)
        self._update_resample_option_state()

    def _load_gamma_par_file(self, file_path: str):
        self._last_gamma_par_dir = os.path.dirname(file_path)
        self.gamma_par_file_edit.setText(file_path)
        result = extract_bounding_box_from_gamma_par(file_path)
        corners = extract_gamma_par_corners(file_path)
        if not result or not corners:
            QMessageBox.warning(self, "提示", "无法从 GAMMA par 文件提取范围。")
            return
        west, south, east, north = result
        self.gamma_bounds = (south, north, west, east)
        summary_text = (
            f"边界(WGS84): 西={west:.4f}, 东={east:.4f}, 南={south:.4f}, 北={north:.4f}\n"
            f"四角: UL({corners['UL'][1]:.2f}, {corners['UL'][0]:.2f})  "
            f"UR({corners['UR'][1]:.2f}, {corners['UR'][0]:.2f})  "
            f"LR({corners['LR'][1]:.2f}, {corners['LR'][0]:.2f})  "
            f"LL({corners['LL'][1]:.2f}, {corners['LL'][0]:.2f})"
        )
        detail_text = (
            f"边界(WGS84): 西={west:.6f}, 东={east:.6f}, 南={south:.6f}, 北={north:.6f}\n"
            f"UL=({corners['UL'][1]:.6f}, {corners['UL'][0]:.6f})\n"
            f"UR=({corners['UR'][1]:.6f}, {corners['UR'][0]:.6f})\n"
            f"LR=({corners['LR'][1]:.6f}, {corners['LR'][0]:.6f})\n"
            f"LL=({corners['LL'][1]:.6f}, {corners['LL'][0]:.6f})"
        )
        self.gamma_par_info_label.setText(summary_text)
        self.gamma_par_info_label.setToolTip(detail_text)
        self.gamma_par_area_label.setText(f"区域面积: {calculate_area_km2(south, north, west, east):.2f} km²")
        self._show_bounds_on_map(south, north, west, east)

    def _set_file_bounds(self, result, vector):
        west, south, east, north = result
        bounds = (south, north, west, east)
        if vector:
            self.vector_bounds = bounds
            self.vector_info_label.setText(f"边界: 西={west:.6f}, 东={east:.6f}, 南={south:.6f}, 北={north:.6f}")
            self.vector_area_label.setText(f"区域面积: {calculate_area_km2(south, north, west, east):.2f} km²")
        else:
            self.tif_bounds = bounds
            self.tif_info_label.setText(f"边界(WGS84): 西={west:.6f}, 东={east:.6f}, 南={south:.6f}, 北={north:.6f}")
            self.tif_area_label.setText(f"区域面积: {calculate_area_km2(south, north, west, east):.2f} km²")
        self._show_bounds_on_map(south, north, west, east)

    def _update_manual_area(self):
        south, north, west, east = self.south_spin.value(), self.north_spin.value(), self.west_spin.value(), self.east_spin.value()
        if south < north and west < east:
            self.manual_area_label.setText(f"区域面积: {calculate_area_km2(south, north, west, east):.2f} km²")
            self._show_bounds_on_map(south, north, west, east)
        else:
            self.manual_area_label.setText("区域面积: -- km²")

    def _on_map_bounds_drawn(self, south, north, west, east):
        self.saved_map_bounds = (south, north, west, east)
        self.map_info_label.setText(f"边界: 南={south:.6f}, 北={north:.6f}, 西={west:.6f}, 东={east:.6f}")
        self.map_area_label.setText(f"区域面积: {calculate_area_km2(south, north, west, east):.2f} km²")

    def _show_bounds_on_map(self, south, north, west, east):
        if self.map_widget and self.map_widget.is_available():
            self.map_widget.show_bounds(south, north, west, east)

    def _on_output_option_changed(self):
        sender = self.sender()
        if sender is self.merge_only_checkbox and self.merge_only_checkbox.isChecked():
            self.clip_to_bounds_checkbox.setChecked(False); self.resample_to_tif_checkbox.setChecked(False)
        elif sender is self.clip_to_bounds_checkbox and self.clip_to_bounds_checkbox.isChecked():
            self.merge_only_checkbox.setChecked(False); self.resample_to_tif_checkbox.setChecked(False)
        elif sender is self.resample_to_tif_checkbox and self.resample_to_tif_checkbox.isChecked():
            if not self._can_use_resample_option():
                self.resample_to_tif_checkbox.setChecked(False)
                self.merge_only_checkbox.setChecked(True)
                return
            self.merge_only_checkbox.setChecked(False); self.clip_to_bounds_checkbox.setChecked(False)
        elif not any([self.merge_only_checkbox.isChecked(), self.clip_to_bounds_checkbox.isChecked(), self.resample_to_tif_checkbox.isChecked()]):
            self.merge_only_checkbox.setChecked(True)

    def _tab_offset(self) -> int:
        return 1 if WEBENGINE_AVAILABLE and self.map_widget else 0

    def _vector_tab_index(self) -> int:
        return 2 + self._tab_offset()

    def _tif_tab_index(self) -> int:
        return 3 + self._tab_offset()

    def _gamma_tab_index(self) -> int:
        return 4 + self._tab_offset()

    def _is_tif_region_selected(self) -> bool:
        return self.region_tab.currentIndex() == self._tif_tab_index()

    def _can_use_resample_option(self) -> bool:
        if not self.reference_tif_path or not os.path.exists(self.reference_tif_path):
            QMessageBox.warning(self, "提示", "请先导入有效的 TIF 文件后再使用“裁剪重采样至相同范围和分辨率”。")
            return False
        if not self._is_tif_region_selected():
            QMessageBox.warning(self, "提示", "“裁剪重采样至相同范围和分辨率”仅在区域选择为“TIF文件”时可用。")
            return False
        return True

    def _update_resample_option_state(self):
        enabled = self.reference_tif_path is not None and os.path.exists(self.reference_tif_path) and self._is_tif_region_selected()
        self.resample_to_tif_checkbox.setEnabled(enabled)
        if not enabled and self.resample_to_tif_checkbox.isChecked():
            self.resample_to_tif_checkbox.setChecked(False)
            if not self.clip_to_bounds_checkbox.isChecked():
                self.merge_only_checkbox.setChecked(True)

    def _on_region_tab_changed(self, _index: int):
        self._update_resample_option_state()

    def _get_region_bounds(self) -> Optional[Tuple[float, float, float, float]]:
        current = self.region_tab.currentIndex()
        offset = self._tab_offset()
        if WEBENGINE_AVAILABLE and self.map_widget and current == 0:
            if self.saved_map_bounds:
                return self.saved_map_bounds
            QMessageBox.warning(self, "提示", "请先在地图上绘制区域")
            return None
        if current == 0 + offset:
            south, north, west, east = self.south_spin.value(), self.north_spin.value(), self.west_spin.value(), self.east_spin.value()
            if south >= north or west >= east:
                QMessageBox.warning(self, "提示", "请输入有效的经纬度范围")
                return None
            return south, north, west, east
        if current == 1 + offset:
            if not self.admin_selector:
                QMessageBox.warning(self, "提示", "行政区划功能不可用")
                return None
            province = self.province_combo.currentText()
            if province == "选择省份":
                QMessageBox.warning(self, "提示", "请选择行政区划")
                return None
            city = self.city_combo.currentText()
            district = self.district_combo.currentText()
            city = None if city == "选择城市" else city
            district = None if district == "选择区县" else district
            boundary = self.admin_selector.get_boundary(province, city, district)
            if not boundary:
                QMessageBox.warning(self, "提示", "未找到行政区划边界")
                return None
            west, south, east, north = boundary
            return south, north, west, east
        if current == 2 + offset:
            if not self.vector_bounds:
                QMessageBox.warning(self, "提示", "请先导入范围文件")
                return None
            return self.vector_bounds
        if current == 3 + offset:
            if not self.tif_bounds:
                QMessageBox.warning(self, "提示", "请先导入范围文件")
                return None
            return self.tif_bounds
        if current == 4 + offset:
            if not self.gamma_bounds:
                QMessageBox.warning(self, "提示", "请先导入范围文件")
                return None
            return self.gamma_bounds
        return None

    def start_acquisition(self):
        if self.resample_to_tif_checkbox.isChecked() and not self._can_use_resample_option():
            return
        bounds = self._get_region_bounds()
        if not bounds:
            return
        output_path = self.output_file_edit.text().strip()
        if not output_path:
            QMessageBox.warning(self, "提示", "请指定输出文件路径")
            return
        south, north, west, east = bounds
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        if self.local_radio.isChecked():
            source = self.config_manager.get_local_source(self.local_source_combo.currentText())
            if not source:
                QMessageBox.warning(self, "提示", "请选择本地数据源")
                return
            self.local_worker = LocalRasterWorker(
                source,
                south,
                north,
                west,
                east,
                output_path,
                clip_to_bounds=self.clip_to_bounds_checkbox.isChecked(),
                reference_tif=self.reference_tif_path if self.resample_to_tif_checkbox.isChecked() else None,
                resample_method=source.resample_method,
            )
            self.local_worker.progress_updated.connect(self._on_progress_updated)
            self.local_worker.process_completed.connect(self._on_completed)
            self.local_worker.error_occurred.connect(self._on_error)
            self.local_worker.files_found.connect(self._on_files_found)
            self.local_worker.start()
            self.log(f"开始处理本地栅格数据源: {source.name}（插值方式: {source.resample_method}）")
        else:
            source = self.config_manager.get_online_source(self.online_source_combo.currentText())
            if not source or not source.api_key:
                QMessageBox.warning(self, "提示", "请先配置在线数据源 API key")
                return
            self.download_worker = OnlineRasterDownloadWorker(OpenTopographyClient(source.api_key), self.online_dataset_combo.currentData(), south, north, west, east, output_path)
            self.download_worker.progress_updated.connect(self._on_progress_updated)
            self.download_worker.download_completed.connect(self._on_completed)
            self.download_worker.error_occurred.connect(self._on_error)
            self.download_worker.start()
            self.log(f"开始下载在线栅格数据: {source.name}")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)

    def _on_files_found(self, files: List[str]):
        if files:
            self.log("使用以下本地栅格文件:")
            for path in files[:10]:
                self.log(f"  - {os.path.basename(path)}")

    def _on_progress_updated(self, progress, message):
        self.log(f"[{progress}%] {message}")

    def _on_completed(self, output_path):
        self._reset_ui()
        self.log(f"处理完成: {output_path}")
        self._show_result_on_map(output_path)
        QMessageBox.information(self, "完成", f"栅格数据已保存至:\n{output_path}")

    def _on_error(self, message):
        self._reset_ui()
        self.log(f"错误: {message}")
        QMessageBox.critical(self, "错误", message)

    def _show_result_on_map(self, raster_path):
        try:
            bounds_list = []
            selected = self._get_region_bounds()
            if selected:
                s, n, w, e = selected
                bounds_list.append({"south": s, "north": n, "west": w, "east": e, "color": "#3388ff", "name": "选择区域"})
            result = extract_bounding_box_from_raster(raster_path)
            if result:
                w, s, e, n = result
                bounds_list.append({"south": s, "north": n, "west": w, "east": e, "color": "#e74c3c", "name": "输出栅格范围"})
            if bounds_list and self.map_widget and self.map_widget.is_available():
                self.map_widget.show_bounds_with_legend(bounds_list)
        except Exception as exc:
            self.log(f"结果显示失败: {exc}")

    def _reset_ui(self):
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def stop_acquisition(self):
        if self.download_worker:
            self.download_worker.stop()
            self.download_worker = None
        if self.local_worker:
            self.local_worker.stop()
            self.local_worker = None
        self._reset_ui()
        self.log("当前操作已停止")

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls:
                path = urls[0].toLocalFile().lower()
                if any(path.endswith(ext) for ext in [".shp", ".geojson", ".json", ".kml", ".kmz", ".gpkg", ".tif", ".tiff", ".par"]):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        urls = event.mimeData().urls()
        if not urls:
            event.ignore()
            return
        file_path = urls[0].toLocalFile()
        ext = os.path.splitext(file_path)[1].lower()
        if ext in [".shp", ".geojson", ".json", ".kml", ".kmz", ".gpkg"]:
            self._load_vector_file(file_path)
            self.region_tab.setCurrentIndex(self._vector_tab_index())
            event.acceptProposedAction()
            return
        if ext in [".tif", ".tiff"]:
            self._load_tif_file(file_path)
            self.region_tab.setCurrentIndex(self._tif_tab_index())
            event.acceptProposedAction()
            return
        if ext == ".par":
            self._load_gamma_par_file(file_path)
            self.region_tab.setCurrentIndex(self._gamma_tab_index())
            event.acceptProposedAction()
            return
        event.ignore()

    def log(self, text):
        self.log_text.append(text)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def closeEvent(self, event):
        if self.online_test_worker and self.online_test_worker.isRunning():
            self.online_test_worker.stop()
        self._save_settings()
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(100, lambda: self.splitter.setSizes([700, 480]))


DEMAcquisitionDialog = RasterDataAcquisitionDialog
