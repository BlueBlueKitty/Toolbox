"""
图像分割导出设置对话框。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class SegmentationExportDialog(QDialog):
    accepted_settings = Signal(dict)

    VECTOR_FORMATS = {
        "GeoJSON": ".geojson",
        "Shapefile": ".shp",
        "GPKG": ".gpkg",
    }

    MASK_FORMATS = {
        "PNG Mask": ".png",
        "GeoTIFF": ".tif",
    }

    def __init__(self, default_name: str, default_dir: str, has_geo: bool, prefer_tif_mask: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导出设置")
        self.resize(520, 320)
        self._has_geo = has_geo

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFormAlignment(form.formAlignment())

        self.dir_edit = QLineEdit(default_dir)
        browse_button = QPushButton("浏览...")
        browse_button.clicked.connect(self._browse_dir)
        dir_row = QWidget()
        dir_layout = QHBoxLayout(dir_row)
        dir_layout.setContentsMargins(0, 0, 0, 0)
        dir_layout.addWidget(self.dir_edit)
        dir_layout.addWidget(browse_button)
        form.addRow("导出目录", dir_row)

        self.base_name_edit = QLineEdit(default_name)
        form.addRow("文件名前缀", self.base_name_edit)

        self.export_vector_check = QCheckBox("导出矢量")
        self.export_vector_check.setChecked(True)
        self.export_mask_check = QCheckBox("导出 Mask")
        self.export_mask_check.setChecked(True)
        export_row = QWidget()
        export_layout = QHBoxLayout(export_row)
        export_layout.setContentsMargins(0, 0, 0, 0)
        export_layout.addWidget(self.export_vector_check)
        export_layout.addWidget(self.export_mask_check)
        export_layout.addStretch(1)
        form.addRow("导出内容", export_row)

        self.vector_format_combo = QComboBox()
        self.vector_format_combo.addItems(self.VECTOR_FORMATS.keys())
        form.addRow("矢量格式", self.vector_format_combo)

        self.vector_coord_combo = QComboBox()
        self.vector_coord_combo.addItem("图像坐标", "image")
        self.vector_coord_combo.addItem("地理坐标", "geo")
        if not has_geo:
            self.vector_coord_combo.model().item(1).setEnabled(False)
            self.vector_coord_combo.setCurrentIndex(0)
        else:
            self.vector_coord_combo.setCurrentIndex(1)
        form.addRow("矢量坐标", self.vector_coord_combo)

        self.mask_format_combo = QComboBox()
        self.mask_format_combo.addItems(self.MASK_FORMATS.keys())
        self.mask_format_combo.setCurrentText("GeoTIFF" if prefer_tif_mask else "PNG Mask")
        form.addRow("Mask 格式", self.mask_format_combo)

        self.mask_colored_check = QCheckBox("写入标签着色表")
        self.mask_colored_check.setChecked(True)
        form.addRow("Mask 选项", self.mask_colored_check)

        self.hint_label = QLabel("项目中的 Mask 将按当前 Mask 显示层导出；若无独立 Mask，则会按当前矢量生成。")
        self.hint_label.setWordWrap(True)
        layout.addLayout(form)
        layout.addWidget(self.hint_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.export_vector_check.toggled.connect(self._update_enabled_state)
        self.export_mask_check.toggled.connect(self._update_enabled_state)
        self.mask_format_combo.currentTextChanged.connect(self._update_enabled_state)
        self._update_enabled_state()

    def _browse_dir(self) -> None:
        current = self.dir_edit.text().strip()
        selected = QFileDialog.getExistingDirectory(self, "选择导出目录", current or str(Path.home()))
        if selected:
            self.dir_edit.setText(selected)

    def _update_enabled_state(self) -> None:
        vector_enabled = self.export_vector_check.isChecked()
        mask_enabled = self.export_mask_check.isChecked()
        self.vector_format_combo.setEnabled(vector_enabled)
        self.vector_coord_combo.setEnabled(vector_enabled)
        self.mask_format_combo.setEnabled(mask_enabled)
        self.mask_colored_check.setEnabled(mask_enabled and self.mask_format_combo.currentText() == "GeoTIFF")

    def _accept(self) -> None:
        output_dir = self.dir_edit.text().strip()
        base_name = self.base_name_edit.text().strip()
        if not output_dir:
            QMessageBox.warning(self, "提示", "请选择导出目录。")
            return
        if not base_name:
            QMessageBox.warning(self, "提示", "请输入文件名前缀。")
            return
        if not self.export_vector_check.isChecked() and not self.export_mask_check.isChecked():
            QMessageBox.warning(self, "提示", "请至少选择一种导出内容。")
            return
        settings = {
            "output_dir": output_dir,
            "base_name": base_name,
            "export_vector": self.export_vector_check.isChecked(),
            "export_mask": self.export_mask_check.isChecked(),
            "vector_format": self.vector_format_combo.currentText(),
            "vector_extension": self.VECTOR_FORMATS[self.vector_format_combo.currentText()],
            "vector_coord_mode": self.vector_coord_combo.currentData(),
            "mask_format": self.mask_format_combo.currentText(),
            "mask_extension": self.MASK_FORMATS[self.mask_format_combo.currentText()],
            "mask_colored": self.mask_colored_check.isChecked(),
        }
        self.accepted_settings.emit(settings)
        self.accept()

    @classmethod
    def get_settings(cls, default_name: str, default_dir: str, has_geo: bool, prefer_tif_mask: bool, parent=None):
        dialog = cls(default_name, default_dir, has_geo, prefer_tif_mask, parent)
        if dialog.exec() != QDialog.Accepted:
            return None
        return {
            "output_dir": dialog.dir_edit.text().strip(),
            "base_name": dialog.base_name_edit.text().strip(),
            "export_vector": dialog.export_vector_check.isChecked(),
            "export_mask": dialog.export_mask_check.isChecked(),
            "vector_format": dialog.vector_format_combo.currentText(),
            "vector_extension": dialog.VECTOR_FORMATS[dialog.vector_format_combo.currentText()],
            "vector_coord_mode": dialog.vector_coord_combo.currentData(),
            "mask_format": dialog.mask_format_combo.currentText(),
            "mask_extension": dialog.MASK_FORMATS[dialog.mask_format_combo.currentText()],
            "mask_colored": dialog.mask_colored_check.isChecked(),
        }
