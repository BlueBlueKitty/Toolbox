"""
在线栅格数据源配置管理窗口。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTextEdit,
    QVBoxLayout, QWidget,
)

from src.utils import DATASETS_CONFIG, OnlineRasterSourceConfig, OpenTopographyClient, RasterSourceConfigManager


class OnlineRasterSourceConfigDialog(QDialog):
    def __init__(self, parent=None, manager: RasterSourceConfigManager | None = None, selected_name: str | None = None):
        super().__init__(parent)
        self.manager = manager or RasterSourceConfigManager()
        self.selected_name = selected_name
        self.current_original_name = None
        self.current_config = None
        self._dirty = False

        self.setWindowTitle("在线数据源配置管理")
        self.resize(760, 480)
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
        left_layout.addWidget(QLabel("在线数据源列表"))
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
        basic_group = QGroupBox("基础配置")
        basic_form = QFormLayout(basic_group)
        self.name_edit = QLineEdit()
        basic_form.addRow("数据源名称", self.name_edit)
        self.platform_combo = QComboBox()
        self.platform_combo.addItems(["OpenTopography"])
        basic_form.addRow("平台类型", self.platform_combo)
        self.default_product_combo = QComboBox()
        self.default_product_combo.setEditable(True)
        self.default_product_combo.addItem("")
        for key, info in DATASETS_CONFIG.items():
            self.default_product_combo.addItem(f"{key} ({info['resolution']})", key)
        basic_form.addRow("默认产品/数据集", self.default_product_combo)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        basic_form.addRow("API key", self.api_key_edit)
        self.description_edit = QTextEdit()
        self.description_edit.setMaximumHeight(90)
        basic_form.addRow("描述信息", self.description_edit)
        right_layout.addWidget(basic_group)

        test_group = QGroupBox("连接测试")
        test_layout = QVBoxLayout(test_group)
        test_btn = QPushButton("测试当前在线数据源")
        test_btn.clicked.connect(self._test_current)
        test_layout.addWidget(test_btn)
        self.test_result_edit = QTextEdit()
        self.test_result_edit.setReadOnly(True)
        self.test_result_edit.setMaximumHeight(130)
        test_layout.addWidget(self.test_result_edit)
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

        self.name_edit.textChanged.connect(self._mark_dirty)
        self.platform_combo.currentIndexChanged.connect(self._mark_dirty)
        self.default_product_combo.currentIndexChanged.connect(self._mark_dirty)
        if self.default_product_combo.lineEdit():
            self.default_product_combo.lineEdit().textChanged.connect(self._mark_dirty)
        self.api_key_edit.textChanged.connect(self._mark_dirty)
        self.description_edit.textChanged.connect(self._mark_dirty)

    def _load_sources(self):
        self.source_list.clear()
        for source in self.manager.get_online_sources():
            item = QListWidgetItem(f"{source.name} [内置]" if source.builtin else source.name)
            item.setData(Qt.UserRole, source.name)
            self.source_list.addItem(item)

    def _select_by_name(self, name: str):
        for row in range(self.source_list.count()):
            item = self.source_list.item(row)
            if item.data(Qt.UserRole) == name:
                self.source_list.setCurrentRow(row)
                break

    def _on_item_changed(self, current, previous):
        if previous and not self._confirm_discard_if_needed():
            self.source_list.blockSignals(True)
            self.source_list.setCurrentItem(previous)
            self.source_list.blockSignals(False)
            return
        if not current:
            return
        config = self.manager.get_online_source(current.data(Qt.UserRole))
        if not config:
            return
        self.current_original_name = config.name
        self.current_config = config
        self.name_edit.setText(config.name)
        self.name_edit.setEnabled(not config.builtin)
        self.platform_combo.setCurrentText(config.platform_type)
        idx = self.default_product_combo.findData(config.default_dataset)
        if idx >= 0:
            self.default_product_combo.setCurrentIndex(idx)
        else:
            self.default_product_combo.setEditText(config.default_dataset or "")
        self.api_key_edit.setText(config.api_key)
        self.description_edit.setPlainText(config.description)
        self.test_result_edit.clear()
        self._dirty = False

    def _collect_config(self) -> OnlineRasterSourceConfig:
        builtin = bool(self.current_config.builtin) if self.current_config else False
        product = self.default_product_combo.currentData()
        if product is None:
            product = self.default_product_combo.currentText().strip()
        return OnlineRasterSourceConfig(
            name=self.name_edit.text().strip() or "未命名在线数据源",
            platform_type=self.platform_combo.currentText(),
            api_key=self.api_key_edit.text().strip(),
            default_dataset=product or "",
            description=self.description_edit.toPlainText().strip(),
            builtin=builtin,
        )

    def _copy_source(self):
        item = self.source_list.currentItem()
        if not item:
            return
        config = self.manager.duplicate_online_source(item.data(Qt.UserRole))
        if not config:
            return
        self.current_original_name = None
        self.current_config = config
        self.name_edit.setEnabled(True)
        self.name_edit.setText(config.name)
        self.platform_combo.setCurrentText(config.platform_type)
        idx = self.default_product_combo.findData(config.default_dataset)
        if idx >= 0:
            self.default_product_combo.setCurrentIndex(idx)
        else:
            self.default_product_combo.setEditText(config.default_dataset or "")
        self.api_key_edit.setText(config.api_key)
        self.description_edit.setPlainText(config.description)
        self.test_result_edit.clear()
        self._dirty = True

    def _new_source(self):
        config = OnlineRasterSourceConfig(name=self.manager.generate_unique_name("新的在线数据源", local=False), builtin=False)
        self.current_original_name = None
        self.current_config = config
        self.selected_name = config.name
        item = QListWidgetItem(config.name)
        item.setData(Qt.UserRole, config.name)
        self.source_list.addItem(item)
        self.source_list.setCurrentItem(item)
        self.name_edit.setEnabled(True)
        self.name_edit.setText(config.name)
        self.platform_combo.setCurrentText(config.platform_type)
        self.default_product_combo.setCurrentIndex(0)
        self.api_key_edit.clear()
        self.description_edit.clear()
        self.test_result_edit.clear()
        self._dirty = True

    def _delete_source(self):
        item = self.source_list.currentItem()
        if not item:
            return
        name = item.data(Qt.UserRole)
        source = self.manager.get_online_source(name)
        if source and source.builtin:
            QMessageBox.information(self, "提示", "内置在线数据源不允许删除，可以复制后修改。")
            return
        if QMessageBox.question(self, "确认删除", f"确定删除在线数据源“{name}”吗？") != QMessageBox.Yes:
            return
        self.manager.delete_online_source(name)
        self._load_sources()
        if self.source_list.count():
            self.source_list.setCurrentRow(0)

    def _save_current(self):
        config = self._collect_config()
        self.manager.save_online_source(config, original_name=self.current_original_name)
        self.current_original_name = config.name
        self.current_config = self.manager.get_online_source(config.name)
        self.selected_name = config.name
        self._load_sources()
        self._select_by_name(config.name)
        self.test_result_edit.setPlainText("配置已保存。")
        self._dirty = False

    def _save_as(self):
        config = self._collect_config()
        new_name, ok = QInputDialog.getText(self, "另存为", "请输入新的配置名称", text=f"{config.name} - 副本")
        if not ok or not new_name.strip():
            return
        config.name = self.manager.generate_unique_name(new_name.strip(), local=False)
        config.builtin = False
        self.manager.save_online_source(config)
        self.current_original_name = config.name
        self.current_config = config
        self.selected_name = config.name
        self._load_sources()
        self._select_by_name(config.name)
        self.test_result_edit.setPlainText("已另存为新的在线数据源配置。")
        self._dirty = False

    def _test_current(self):
        config = self._collect_config()
        if config.platform_type != "OpenTopography":
            self.test_result_edit.setPlainText("暂不支持该平台测试。")
            return
        if not config.api_key:
            self.test_result_edit.setPlainText("请先填写 API key。")
            return
        try:
            ok = OpenTopographyClient(config.api_key).validate_api_key()
            self.test_result_edit.setPlainText("测试通过：API key 可用。" if ok else "测试失败：API key 可能无效。")
        except Exception as exc:
            self.test_result_edit.setPlainText(f"测试失败：{exc}")

    def _mark_dirty(self):
        self._dirty = True

    def _confirm_discard_if_needed(self) -> bool:
        if not self._dirty:
            return True
        reply = QMessageBox.question(
            self,
            "保存提示",
            "当前配置有未保存修改，是否先保存？",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.Cancel:
            return False
        if reply == QMessageBox.Yes:
            self._save_current()
        return True

    def closeEvent(self, event):
        if not self._confirm_discard_if_needed():
            event.ignore()
            return
        super().closeEvent(event)
