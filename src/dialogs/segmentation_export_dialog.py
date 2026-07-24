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
        "COCO": "_coco.json",
        "YOLO": "_yolo.txt",
        "VOC": "_voc.xml",
    }

    DL_VECTOR_FORMATS = {"COCO", "YOLO", "VOC"}

    MASK_FORMATS = {
        "PNG": ".png",
        "BMP": ".bmp",
        "GeoTIFF": ".tif",
    }

    @classmethod
    def available_vector_formats(cls, has_geo: bool) -> dict[str, str]:
        if has_geo:
            return dict(cls.VECTOR_FORMATS)
        return {
            name: extension
            for name, extension in cls.VECTOR_FORMATS.items()
            if name in cls.DL_VECTOR_FORMATS
        }

    @classmethod
    def coordinate_mode_for_format(cls, vector_format: str, has_geo: bool) -> str:
        if has_geo and vector_format not in cls.DL_VECTOR_FORMATS:
            return "geo"
        return "image"

    def __init__(
        self,
        default_name: str,
        default_dir: str,
        has_geo: bool,
        prefer_tif_mask: bool,
        parent=None,
        initial_settings: dict | None = None,
        browse_dir: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle("导出设置")
        self.resize(520, 1)
        self._has_geo = has_geo
        self._vector_formats = self.available_vector_formats(has_geo)
        self._browse_start_dir = str(browse_dir).strip()

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
        self.vector_format_combo.addItems(self._vector_formats.keys())
        form.addRow("矢量格式", self.vector_format_combo)

        self.mask_format_combo = QComboBox()
        self.mask_format_combo.addItems(self.MASK_FORMATS.keys())
        self.mask_format_combo.setCurrentText("GeoTIFF" if prefer_tif_mask else "PNG")
        form.addRow("Mask 格式", self.mask_format_combo)

        self.export_split_masks_check = QCheckBox("另按标签分别导出 Mask（文件名前缀1、2、3…）")
        form.addRow("Mask 选项", self.export_split_masks_check)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.export_vector_check.toggled.connect(self._update_enabled_state)
        self.export_mask_check.toggled.connect(self._update_enabled_state)
        self.vector_format_combo.currentTextChanged.connect(self._update_enabled_state)
        self.mask_format_combo.currentTextChanged.connect(self._update_enabled_state)
        self._apply_initial_settings(initial_settings or {})
        self._update_enabled_state()
        # Let Qt derive a compact height from the visible rows.  A hard-coded
        # height leaves a conspicuous blank area after optional rows change.
        self.resize(520, self.sizeHint().height())

    def _apply_initial_settings(self, settings: dict) -> None:
        """仅恢复有效的项目导出设置，旧项目则保留当前默认值。"""
        if not isinstance(settings, dict):
            return
        if isinstance(settings.get("base_name"), str) and settings["base_name"].strip():
            self.base_name_edit.setText(settings["base_name"].strip())
        self.export_vector_check.setChecked(bool(settings.get("export_vector", self.export_vector_check.isChecked())))
        self.export_mask_check.setChecked(bool(settings.get("export_mask", self.export_mask_check.isChecked())))
        self.export_split_masks_check.setChecked(bool(settings.get("export_split_masks", self.export_split_masks_check.isChecked())))
        self._set_combo_value(self.vector_format_combo, settings.get("vector_format"))
        self._set_combo_value(self.mask_format_combo, settings.get("mask_format"))

    @staticmethod
    def _set_combo_value(combo: QComboBox, value) -> None:
        if isinstance(value, str) and combo.findText(value) >= 0:
            combo.setCurrentText(value)

    def _browse_dir(self) -> None:
        current = self.dir_edit.text().strip()
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择导出目录",
            current or self._browse_start_dir or str(Path.home()),
        )
        if selected:
            self.dir_edit.setText(selected)

    def _update_enabled_state(self) -> None:
        vector_enabled = self.export_vector_check.isChecked()
        mask_enabled = self.export_mask_check.isChecked()
        self.vector_format_combo.setEnabled(vector_enabled)
        self.mask_format_combo.setEnabled(mask_enabled)
        self.export_split_masks_check.setEnabled(mask_enabled)

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
        settings = self._current_settings()
        self.accepted_settings.emit(settings)
        self.accept()

    def _current_settings(self) -> dict:
        vector_format = self.vector_format_combo.currentText()
        mask_format = self.mask_format_combo.currentText()
        return {
            "output_dir": self.dir_edit.text().strip(),
            "base_name": self.base_name_edit.text().strip(),
            "export_vector": self.export_vector_check.isChecked(),
            "export_mask": self.export_mask_check.isChecked(),
            "vector_format": vector_format,
            "vector_extension": self._vector_formats[vector_format],
            "mask_format": mask_format,
            "mask_extension": self.MASK_FORMATS[mask_format],
            "export_split_masks": self.export_split_masks_check.isChecked(),
        }

    @classmethod
    def get_settings(
        cls,
        default_name: str,
        default_dir: str,
        has_geo: bool,
        prefer_tif_mask: bool,
        parent=None,
        initial_settings: dict | None = None,
        browse_dir: str = "",
    ):
        dialog = cls(
            default_name,
            default_dir,
            has_geo,
            prefer_tif_mask,
            parent=parent,
            initial_settings=initial_settings,
            browse_dir=browse_dir,
        )
        if dialog.exec() != QDialog.Accepted:
            return None
        return dialog._current_settings()
