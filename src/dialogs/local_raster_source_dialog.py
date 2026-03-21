"""
本地栅格数据源配置管理窗口。
"""

from __future__ import annotations

import uuid

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QDoubleSpinBox, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from src.utils import (
    ANCHOR_OPTIONS, COORD_LOCATION_OPTIONS, LAT_FORMAT_OPTIONS, LON_FORMAT_OPTIONS,
    LocalRasterProcessor, LocalRasterSourceConfig, RasterSourceAutoDetector,
    RasterSourceConfigManager, ZIP_STRATEGY_OPTIONS, RESAMPLE_METHOD_OPTIONS, build_rule_preview,
)


BEIJING_LON = 116.4074
BEIJING_LAT = 39.9042


class LocalRasterSourceConfigDialog(QDialog):
    def __init__(self, parent=None, manager: RasterSourceConfigManager | None = None, selected_name: str | None = None):
        super().__init__(parent)
        self.manager = manager or RasterSourceConfigManager()
        self.selected_name = selected_name
        self.current_original_name = None
        self.current_config = None
        self._builtin_rule_widgets = []
        self._dirty = False
        self._suspend_item_change = False
        self._restoring_state = False
        self._tile_token_template = "{lat}{lon}"
        self._draft_configs: dict[str, LocalRasterSourceConfig] = {}
        self._current_draft_key: str | None = None

        self.setWindowTitle("本地数据源配置管理")
        self.resize(860, 560)
        self._create_ui()
        self._load_sources()

        if selected_name:
            self._select_by_name(selected_name)
        elif self.source_list.count():
            self.source_list.setCurrentRow(0)

    def _create_ui(self):
        layout = QVBoxLayout(self)
        body = QHBoxLayout()
        layout.addLayout(body)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("本地数据源列表"))
        self.source_list = QListWidget()
        self.source_list.currentItemChanged.connect(self._on_item_changed)
        left_layout.addWidget(self.source_list)

        left_buttons = QGridLayout()
        new_btn = QPushButton("新建")
        new_btn.clicked.connect(self._new_source)
        left_buttons.addWidget(new_btn, 0, 0)
        copy_btn = QPushButton("复制")
        copy_btn.clicked.connect(self._copy_source)
        left_buttons.addWidget(copy_btn, 0, 1)
        delete_btn = QPushButton("删除")
        delete_btn.clicked.connect(self._delete_source)
        left_buttons.addWidget(delete_btn, 1, 0, 1, 2)
        left_layout.addLayout(left_buttons)
        body.addWidget(left, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)

        top_row = QHBoxLayout()
        title = QLabel("当前配置详情")
        title.setStyleSheet("font-weight: bold;")
        top_row.addWidget(title)
        top_row.addStretch()
        import_btn = QPushButton("智能识别数据源配置")
        import_btn.setStyleSheet("font-weight: bold; padding: 6px 12px;")
        import_btn.clicked.connect(self._import_sample)
        top_row.addWidget(import_btn)
        right_layout.addLayout(top_row)

        basic_group = QGroupBox("基础信息")
        basic_form = QFormLayout(basic_group)
        self.name_edit = QLineEdit()
        basic_form.addRow(self._label_with_tip("配置名称", "数据源名称。内置数据源名称固定，自定义数据源可修改。"), self.name_edit)
        root_row = QHBoxLayout()
        self.root_dir_edit = QLineEdit()
        root_row.addWidget(self.root_dir_edit)
        browse_btn = QPushButton("浏览")
        browse_btn.clicked.connect(self._browse_root_dir)
        root_row.addWidget(browse_btn)
        basic_form.addRow(self._label_with_tip("数据根目录", "本地瓦片的根目录。内置数据源只需要配置这个路径即可。"), root_row)
        self.sample_path_edit = QLineEdit()
        self.sample_path_edit.setReadOnly(True)
        basic_form.addRow(self._label_with_tip("样例文件", "通过智能识别导入时记录的样例文件路径。"), self.sample_path_edit)
        self.description_edit = QTextEdit()
        self.description_edit.setMaximumHeight(54)
        basic_form.addRow(self._label_with_tip("描述信息", "用于备注数据源来源、覆盖范围或特殊说明。"), self.description_edit)
        right_layout.addWidget(basic_group)

        rule_group = QGroupBox("规则设置")
        rule_grid = QGridLayout(rule_group)
        rule_grid.setHorizontalSpacing(10)
        rule_grid.setVerticalSpacing(8)

        self.lat_interval_spin = QDoubleSpinBox(); self.lat_interval_spin.setRange(0.0001, 9999); self.lat_interval_spin.setDecimals(6); self.lat_interval_spin.setValue(1.0)
        self.lon_interval_spin = QDoubleSpinBox(); self.lon_interval_spin.setRange(0.0001, 9999); self.lon_interval_spin.setDecimals(6); self.lon_interval_spin.setValue(1.0)
        self.anchor_combo = QComboBox(); self.anchor_combo.addItems(ANCHOR_OPTIONS)
        self.lat_format_combo = QComboBox(); self.lat_format_combo.addItems(LAT_FORMAT_OPTIONS)
        self.lon_format_combo = QComboBox(); self.lon_format_combo.addItems(LON_FORMAT_OPTIONS)
        self.coord_location_combo = QComboBox(); self.coord_location_combo.addItems(COORD_LOCATION_OPTIONS)
        self.is_archive_combo = QComboBox(); self.is_archive_combo.addItems(["否", "是"]); self.is_archive_combo.currentIndexChanged.connect(self._update_archive_state)
        self.archive_ext_edit = QLineEdit(".zip")
        self.raster_ext_edit = QLineEdit(".tif")
        self.zip_strategy_combo = QComboBox(); self.zip_strategy_combo.addItems(ZIP_STRATEGY_OPTIONS)
        self.resample_method_combo = QComboBox(); self.resample_method_combo.addItems(RESAMPLE_METHOD_OPTIONS)
        self.allow_missing_checkbox = QCheckBox("缺失瓦片时继续处理")

        entries = [
            ("纬度间隔", "单个瓦片在纬度方向的跨度，例如 1 度。", self.lat_interval_spin),
            ("经度间隔", "单个瓦片在经度方向的跨度，例如 1 度。", self.lon_interval_spin),
            ("纬度格式", "纬度在路径中的表达方式，例如 N00 或 N00_00。", self.lat_format_combo),
            ("经度格式", "经度在路径中的表达方式，例如 E000 或 E000_00。", self.lon_format_combo),
            ("命名锚点", "文件名中的经纬度代表哪个角点，常见默认是左下角。", self.anchor_combo),
            ("经纬度位置", "经纬度 token 出现在文件名、文件夹名，或两者同时出现。", self.coord_location_combo),
            ("是否压缩", "瓦片是否以 zip 等压缩包形式保存。", self.is_archive_combo),
            ("压缩包扩展名", "压缩包后缀，常见为 .zip。", self.archive_ext_edit),
            ("主栅格扩展名", "主栅格的扩展名，例如 .tif、.hgt。", self.raster_ext_edit),
            ("压缩包策略", "压缩包内有多个文件时，如何自动选择主栅格。", self.zip_strategy_combo),
        ]
        for idx, (text, tip, widget) in enumerate(entries):
            row = idx // 2
            col = (idx % 2) * 2
            rule_grid.addWidget(self._label_with_tip(text, tip), row, col)
            rule_grid.addWidget(widget, row, col + 1)
            self._builtin_rule_widgets.append(widget)
        rule_grid.addWidget(self._label_with_tip("插值方式", "用于裁剪/重采样输出时的插值算法，默认双线性插值。"), 5, 0)
        rule_grid.addWidget(self.resample_method_combo, 5, 1)
        rule_grid.addWidget(self._label_with_tip("缺失处理", "勾选后，即使部分瓦片缺失，仍继续拼接已有瓦片。"), 5, 2)
        rule_grid.addWidget(self.allow_missing_checkbox, 5, 3)
        right_layout.addWidget(rule_group)

        template_group = QGroupBox("路径规则")
        template_form = QFormLayout(template_group)
        self.path_rule_edit = QLineEdit()
        self.preview_label = QLabel("")
        self.preview_label.setWordWrap(True)
        template_form.addRow(self._label_with_tip("路径规则", "可编辑的内部规则，使用 {lat} / {lon} 占位。"), self.path_rule_edit)
        template_form.addRow(self._label_with_tip("规则预览", "根据当前格式设置生成的实际路径示意。"), self.preview_label)
        right_layout.addWidget(template_group)

        test_group = QGroupBox("配置测试")
        test_form = QFormLayout(test_group)
        point_row = QHBoxLayout()
        self.test_lon_spin = QDoubleSpinBox(); self.test_lon_spin.setRange(-180, 180); self.test_lon_spin.setDecimals(6)
        self.test_lat_spin = QDoubleSpinBox(); self.test_lat_spin.setRange(-90, 90); self.test_lat_spin.setDecimals(6)
        reset_btn = QPushButton("恢复默认坐标")
        reset_btn.clicked.connect(self._restore_default_test_point)
        point_row.addWidget(QLabel("经度")); point_row.addWidget(self.test_lon_spin)
        point_row.addWidget(QLabel("纬度")); point_row.addWidget(self.test_lat_spin)
        point_row.addWidget(reset_btn)
        test_form.addRow(self._label_with_tip("测试点", "默认使用北京坐标。测试会验证找到的瓦片是否真实覆盖该点。"), point_row)
        test_btn = QPushButton("测试当前配置")
        test_btn.clicked.connect(self._test_current_config)
        test_form.addRow("", test_btn)
        self.test_result_edit = QTextEdit()
        self.test_result_edit.setReadOnly(True)
        self.test_result_edit.setMaximumHeight(100)
        test_form.addRow("测试结果", self.test_result_edit)
        right_layout.addWidget(test_group)

        body.addWidget(right, 2)

        bottom = QHBoxLayout()
        layout.addLayout(bottom)
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._save_current)
        bottom.addWidget(save_btn)
        save_as_btn = QPushButton("另存为")
        save_as_btn.clicked.connect(self._save_as)
        bottom.addWidget(save_as_btn)
        bottom.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)

        for widget in [self.name_edit, self.root_dir_edit, self.archive_ext_edit, self.raster_ext_edit, self.path_rule_edit]:
            widget.textChanged.connect(self._on_form_changed)
        self.description_edit.textChanged.connect(self._on_form_changed)
        for combo in [self.anchor_combo, self.lat_format_combo, self.lon_format_combo, self.coord_location_combo, self.is_archive_combo, self.zip_strategy_combo, self.resample_method_combo]:
            combo.currentIndexChanged.connect(self._on_form_changed)
        self.lat_interval_spin.valueChanged.connect(self._on_form_changed)
        self.lon_interval_spin.valueChanged.connect(self._on_form_changed)
        self.allow_missing_checkbox.toggled.connect(self._on_form_changed)

    def _label_with_tip(self, text: str, tip: str) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(text)
        info = QToolButton()
        info.setText("i")
        info.setAutoRaise(True)
        info.setToolTip(tip)
        info.setFixedSize(16, 16)
        info.setStyleSheet(
            "QToolButton {"
            " color: #2d6cdf;"
            " font-weight: bold;"
            " border: 1px solid #2d6cdf;"
            " border-radius: 8px;"
            " background: #eef4ff;"
            " padding: 0px;"
            "}"
            "QToolButton:hover {"
            " background: #dbe8ff;"
            "}"
        )
        layout.addWidget(label)
        layout.addWidget(info)
        layout.addStretch()
        return widget

    def _load_sources(self):
        current_name = None
        current_item = self.source_list.currentItem()
        if current_item:
            current_name = current_item.data(Qt.UserRole)
        self.source_list.clear()
        for source in self.manager.get_local_sources():
            item = QListWidgetItem(f"{source.name} [内置]" if source.builtin else source.name)
            item.setData(Qt.UserRole, source.name)
            self.source_list.addItem(item)
        for draft_key, draft_cfg in self._draft_configs.items():
            item = QListWidgetItem(f"{draft_cfg.name} [未保存]")
            item.setData(Qt.UserRole, draft_key)
            self.source_list.addItem(item)
        if current_name:
            self._select_by_name(current_name)

    def _select_by_name(self, name: str):
        for row in range(self.source_list.count()):
            item = self.source_list.item(row)
            if item.data(Qt.UserRole) == name:
                self.source_list.setCurrentRow(row)
                return

    def _on_item_changed(self, current, previous):
        if self._suspend_item_change:
            return
        if previous and not self._confirm_discard_if_needed():
            self.source_list.blockSignals(True)
            self.source_list.setCurrentItem(previous)
            self.source_list.blockSignals(False)
            return
        if not current:
            return
        key = current.data(Qt.UserRole)
        if isinstance(key, str) and key.startswith("__draft__:"):
            config = self._draft_configs.get(key)
            self.current_original_name = None
            self._current_draft_key = key
        else:
            config = self.manager.get_local_source(key)
            self.current_original_name = config.name if config else None
            self._current_draft_key = None
        if not config:
            return
        self.current_config = config
        self._load_config_to_form(config)

    def _load_config_to_form(self, config: LocalRasterSourceConfig):
        needs_repair = False
        if "{lat}" not in config.relative_path_template and "{lon}" not in config.relative_path_template and "{tile}" not in config.relative_path_template:
            needs_repair = True
        if "{lat}" not in config.tile_token_template and "{lon}" not in config.tile_token_template and "{tile}" not in config.tile_token_template:
            needs_repair = True
        if "{tile}" in config.tile_token_template:
            needs_repair = True

        if config.sample_path and needs_repair:
            try:
                detected = RasterSourceAutoDetector.detect_from_sample(config.sample_path)
                config.relative_path_template = detected.relative_path_template
                config.tile_token_template = detected.tile_token_template
            except Exception:
                pass
        self._restoring_state = True
        self.name_edit.setText(config.name)
        self.root_dir_edit.setText(config.root_dir)
        self.sample_path_edit.setText(config.sample_path)
        self.description_edit.setPlainText(config.description)
        self.lat_interval_spin.setValue(config.latitude_interval)
        self.lon_interval_spin.setValue(config.longitude_interval)
        self.anchor_combo.setCurrentText(config.naming_anchor)
        self.lat_format_combo.setCurrentText(config.latitude_format)
        self.lon_format_combo.setCurrentText(config.longitude_format)
        self.coord_location_combo.setCurrentText(config.coord_location)
        self.is_archive_combo.setCurrentText("是" if config.is_archive else "否")
        self.archive_ext_edit.setText(config.archive_extension)
        self.raster_ext_edit.setText(config.raster_extension)
        self.zip_strategy_combo.setCurrentText(config.zip_raster_strategy)
        self.resample_method_combo.setCurrentText(config.resample_method)
        self.allow_missing_checkbox.setChecked(config.allow_missing_tiles)
        self.path_rule_edit.setText(config.relative_path_template)
        self._tile_token_template = config.tile_token_template or "{lat}{lon}"
        if config.last_test_point:
            self.test_lat_spin.setValue(config.last_test_point[0])
            self.test_lon_spin.setValue(config.last_test_point[1])
        else:
            self._restore_default_test_point()
        self._apply_builtin_state(config.builtin)
        self._update_archive_state()
        self._refresh_preview()
        self._dirty = False
        self._restoring_state = False

    def _apply_builtin_state(self, is_builtin: bool):
        self.name_edit.setEnabled(not is_builtin)
        self.path_rule_edit.setReadOnly(is_builtin)
        for widget in self._builtin_rule_widgets:
            widget.setEnabled(not is_builtin)

    def _collect_form_config(self) -> LocalRasterSourceConfig:
        builtin = bool(self.current_config.builtin) if self.current_config else False
        return LocalRasterSourceConfig(
            name=self.name_edit.text().strip() or "未命名本地数据源",
            root_dir=self.root_dir_edit.text().strip(),
            is_archive=self.is_archive_combo.currentText() == "是",
            archive_extension=self.archive_ext_edit.text().strip() or ".zip",
            raster_extension=self.raster_ext_edit.text().strip() or ".tif",
            longitude_interval=self.lon_interval_spin.value(),
            latitude_interval=self.lat_interval_spin.value(),
            naming_anchor=self.anchor_combo.currentText(),
            relative_path_template=self.path_rule_edit.text().strip() or "{tile}.tif",
            tile_token_template=self._tile_token_template or "{lat}{lon}",
            latitude_format=self.lat_format_combo.currentText(),
            longitude_format=self.lon_format_combo.currentText(),
            coord_location=self.coord_location_combo.currentText(),
            zip_raster_strategy=self.zip_strategy_combo.currentText(),
            resample_method=self.resample_method_combo.currentText(),
            allow_missing_tiles=self.allow_missing_checkbox.isChecked(),
            description=self.description_edit.toPlainText().strip(),
            builtin=builtin,
            last_test_point=(self.test_lat_spin.value(), self.test_lon_spin.value()),
            sample_path=self.sample_path_edit.text().strip(),
        )

    def _refresh_preview(self):
        preview = build_rule_preview(self._collect_form_config())
        self.preview_label.setText(preview)

    def _on_form_changed(self):
        if self._restoring_state:
            return
        self._dirty = True
        self._refresh_preview()

    def _browse_root_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "选择数据根目录", self.root_dir_edit.text().strip())
        if folder:
            self.root_dir_edit.setText(folder)

    def _update_archive_state(self):
        is_archive = self.is_archive_combo.currentText() == "是"
        builtin = bool(self.current_config.builtin) if self.current_config else False
        self.archive_ext_edit.setEnabled(is_archive and not builtin)
        self.zip_strategy_combo.setEnabled(is_archive and not builtin)

    def _restore_default_test_point(self):
        self.test_lon_spin.setValue(BEIJING_LON)
        self.test_lat_spin.setValue(BEIJING_LAT)

    def _new_source(self):
        config = LocalRasterSourceConfig(name=self.manager.generate_unique_name("新的本地数据源", local=True), last_test_point=(BEIJING_LAT, BEIJING_LON))
        self.current_original_name = None
        self.current_config = config
        self._tile_token_template = config.tile_token_template
        self.selected_name = config.name
        self._current_draft_key = f"__draft__:{uuid.uuid4().hex[:8]}"
        self._draft_configs[self._current_draft_key] = config
        item = QListWidgetItem(config.name)
        item.setText(f"{config.name} [未保存]")
        item.setData(Qt.UserRole, self._current_draft_key)
        self._suspend_item_change = True
        self.source_list.addItem(item)
        self.source_list.setCurrentItem(item)
        self._suspend_item_change = False
        self._load_config_to_form(config)
        self._dirty = False

    def _import_sample(self):
        sample_path, _ = QFileDialog.getOpenFileName(self, "选择样例栅格文件或压缩包", "", "栅格或压缩包 (*.tif *.tiff *.hgt *.img *.vrt *.zip);;所有文件 (*.*)")
        if not sample_path:
            return
        try:
            config = RasterSourceAutoDetector.detect_from_sample(sample_path)
            config.name = self.manager.generate_unique_name(config.name, local=True)
            config.last_test_point = (BEIJING_LAT, BEIJING_LON)
            if self.current_original_name is None and self._current_draft_key and not self._dirty:
                self.current_config = config
                self._tile_token_template = config.tile_token_template
                self._draft_configs[self._current_draft_key] = config
                item = self.source_list.currentItem()
                if item:
                    item.setText(f"{config.name} [未保存]")
                self._load_config_to_form(config)
                self.test_result_edit.setPlainText("已覆盖当前未修改的新建配置。")
                self._dirty = True
                return

            if self.current_original_name is None and self._current_draft_key and self._dirty:
                reply = QMessageBox.question(
                    self,
                    "保存提示",
                    f"配置“{self.name_edit.text().strip() or '未命名配置'}”有未保存修改，是否先保存后再智能识别？",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                    QMessageBox.Yes,
                )
                if reply == QMessageBox.Cancel:
                    return
                if reply == QMessageBox.Yes:
                    self._save_current()
                if reply == QMessageBox.No:
                    self._discard_unsaved_draft(self._current_draft_key)

            self.current_original_name = None
            self.current_config = config
            self._tile_token_template = config.tile_token_template
            self.selected_name = config.name
            self._current_draft_key = f"__draft__:{uuid.uuid4().hex[:8]}"
            self._draft_configs[self._current_draft_key] = config
            item = QListWidgetItem(f"{config.name} [未保存]")
            item.setData(Qt.UserRole, self._current_draft_key)
            self._suspend_item_change = True
            self.source_list.addItem(item)
            self.source_list.setCurrentItem(item)
            self._suspend_item_change = False
            self._load_config_to_form(config)
            self.test_result_edit.setPlainText("已智能识别新的数据源配置。")
            self._dirty = True
        except Exception as exc:
            QMessageBox.warning(self, "识别失败", f"自动识别失败，请手动修正。\n\n原因: {exc}")

    def _copy_source(self):
        item = self.source_list.currentItem()
        if not item:
            return
        cloned = self.manager.duplicate_local_source(item.data(Qt.UserRole))
        if not cloned:
            return
        cloned.last_test_point = cloned.last_test_point or (BEIJING_LAT, BEIJING_LON)
        self.current_original_name = None
        self.current_config = cloned
        self._tile_token_template = cloned.tile_token_template
        self.selected_name = cloned.name
        self._current_draft_key = f"__draft__:{uuid.uuid4().hex[:8]}"
        self._draft_configs[self._current_draft_key] = cloned
        item = QListWidgetItem(f"{cloned.name} [未保存]")
        item.setData(Qt.UserRole, self._current_draft_key)
        self._suspend_item_change = True
        self.source_list.addItem(item)
        self.source_list.setCurrentItem(item)
        self._suspend_item_change = False
        self._load_config_to_form(cloned)
        self._dirty = True

    def _delete_source(self):
        item = self.source_list.currentItem()
        if not item:
            return
        name = item.data(Qt.UserRole)
        if isinstance(name, str) and name.startswith("__draft__:"):
            self._discard_unsaved_draft(name)
            return
        source = self.manager.get_local_source(name)
        if source and source.builtin:
            QMessageBox.information(self, "提示", "内置数据源不允许删除，可以复制后修改。")
            return
        if QMessageBox.question(self, "确认删除", f"确定删除本地数据源“{name}”吗？") != QMessageBox.Yes:
            return
        self.manager.delete_local_source(name)
        self._load_sources()
        if self.source_list.count():
            self.source_list.setCurrentRow(0)

    def _save_current(self):
        config = self._collect_form_config()
        if not config.name.strip():
            QMessageBox.warning(self, "警告", "请填写配置名称")
            return
        if self.current_original_name is None and self.manager.get_local_source(config.name):
            config.name = self.manager.generate_unique_name(config.name, local=True)
            self.name_edit.setText(config.name)
        self.manager.save_local_source(config, original_name=self.current_original_name)
        self.current_original_name = config.name
        self.current_config = self.manager.get_local_source(config.name)
        self._tile_token_template = self.current_config.tile_token_template if self.current_config else self._tile_token_template
        self.selected_name = config.name
        if self._current_draft_key and self._current_draft_key in self._draft_configs:
            del self._draft_configs[self._current_draft_key]
            self._current_draft_key = None
        item = self.source_list.currentItem()
        if item:
            item.setData(Qt.UserRole, config.name)
            item.setText(f"{config.name} [内置]" if (self.current_config and self.current_config.builtin) else config.name)
        self.test_result_edit.setPlainText("配置已保存。")
        self._dirty = False

    def _save_as(self):
        config = self._collect_form_config()
        new_name, ok = QInputDialog.getText(self, "另存为", "请输入新的配置名称", text=f"{config.name} - 副本")
        if not ok or not new_name.strip():
            return
        config.name = self.manager.generate_unique_name(new_name.strip(), local=True)
        config.builtin = False
        self.manager.save_local_source(config)
        self.current_original_name = config.name
        self.current_config = config
        self._tile_token_template = config.tile_token_template
        self.selected_name = config.name
        if self._current_draft_key and self._current_draft_key in self._draft_configs:
            del self._draft_configs[self._current_draft_key]
            self._current_draft_key = None
        item = self.source_list.currentItem()
        if item:
            item.setData(Qt.UserRole, config.name)
            item.setText(config.name)
        self.test_result_edit.setPlainText("已另存为新的本地数据源配置。")
        self._dirty = False

    def _test_current_config(self):
        config = self._collect_form_config()
        processor = LocalRasterProcessor()
        result = processor.test_config(config, self.test_lat_spin.value(), self.test_lon_spin.value(), processor.get_raster_extent_wgs84)
        self.test_result_edit.setPlainText("\n".join([result.message] + result.details))
        if result.success:
            self.manager.save_local_source(config, original_name=self.current_original_name)
            self.current_original_name = config.name
            self.selected_name = config.name
            saved = self.manager.get_local_source(config.name)
            if saved:
                self.current_config = saved
                self._tile_token_template = saved.tile_token_template
            self._dirty = False

    def _confirm_discard_if_needed(self) -> bool:
        if not self._dirty:
            return True
        config_name = self.name_edit.text().strip() or "未命名配置"
        reply = QMessageBox.question(
            self,
            "保存提示",
            f"配置“{config_name}”有未保存修改，是否先保存？",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.Cancel:
            return False
        if reply == QMessageBox.Yes:
            before = self.current_original_name
            self._save_current()
            if before is None and self.current_original_name is None:
                return False
        elif reply == QMessageBox.No and self.current_original_name is None and self._current_draft_key:
            self._discard_unsaved_draft(self._current_draft_key)
        return True

    def _discard_unsaved_draft(self, draft_key: str):
        if draft_key in self._draft_configs:
            del self._draft_configs[draft_key]
        for row in range(self.source_list.count()):
            item = self.source_list.item(row)
            if item.data(Qt.UserRole) == draft_key:
                self._suspend_item_change = True
                removed = self.source_list.takeItem(row)
                del removed
                self._suspend_item_change = False
                break
        self.current_original_name = None
        self._current_draft_key = None
        self.current_config = None
        self._dirty = False
        if self.source_list.count():
            self._suspend_item_change = True
            self.source_list.setCurrentRow(0)
            self._suspend_item_change = False
            item = self.source_list.currentItem()
            if item:
                config = self.manager.get_local_source(item.data(Qt.UserRole))
                if config:
                    self._load_config_to_form(config)

    def _save_draft_config(self, draft_key: str, config: LocalRasterSourceConfig):
        if not config.name.strip():
            config.name = self.manager.generate_unique_name("未命名本地数据源", local=True)
        if self.manager.get_local_source(config.name):
            config.name = self.manager.generate_unique_name(config.name, local=True)
        self.manager.save_local_source(config)
        if draft_key in self._draft_configs:
            del self._draft_configs[draft_key]

    def closeEvent(self, event):
        current_is_draft = self.current_original_name is None and self._current_draft_key is not None
        if not current_is_draft:
            if not self._confirm_discard_if_needed():
                event.ignore()
                return
        else:
            # 当前是草稿时，统一进入逐条草稿询问流程，避免重复弹窗
            if self.current_config:
                self._draft_configs[self._current_draft_key] = self._collect_form_config()

        pending_keys = list(self._draft_configs.keys())
        for draft_key in pending_keys:
            draft_cfg = self._draft_configs.get(draft_key)
            if not draft_cfg:
                continue
            reply = QMessageBox.question(
                self,
                "保存提示",
                f"未保存配置“{draft_cfg.name}”是否保存？",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Cancel:
                event.ignore()
                return
            if reply == QMessageBox.Yes:
                self._save_draft_config(draft_key, draft_cfg)
            else:
                del self._draft_configs[draft_key]
        super().closeEvent(event)
