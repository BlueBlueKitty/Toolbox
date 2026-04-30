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
        "COCO": "_coco.json",
        "YOLO": "_yolo.txt",
        "VOC": "_voc.xml",
    }

    DL_VECTOR_FORMATS = {"COCO", "YOLO", "VOC"}

    MASK_FORMATS = {
        "PNG Mask": ".png",
        "GeoTIFF": ".tif",
    }

    @classmethod
    def available_vector_formats(cls, has_geo: bool) -> dict[str, str]:
        """available_vector_formats。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            has_geo (bool): 输入参数。
        返回:
            dict[str, str]: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if has_geo:
            return dict(cls.VECTOR_FORMATS)
        return {
            name: extension
            for name, extension in cls.VECTOR_FORMATS.items()
            if name in cls.DL_VECTOR_FORMATS
        }

    @classmethod
    def coordinate_mode_for_format(cls, vector_format: str, has_geo: bool) -> str:
        """coordinate_mode_for_format。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            vector_format (str): 输入参数。
            has_geo (bool): 输入参数。
        返回:
            str: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if has_geo and vector_format not in cls.DL_VECTOR_FORMATS:
            return "geo"
        return "image"

    def __init__(self, default_name: str, default_dir: str, has_geo: bool, prefer_tif_mask: bool, parent=None):
        """__init__。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            default_name (str): 输入参数。
            default_dir (str): 输入参数。
            has_geo (bool): 输入参数。
            prefer_tif_mask (bool): 输入参数。
            parent (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        super().__init__(parent)
        self.setWindowTitle("导出设置")
        self.resize(520, 320)
        self._has_geo = has_geo
        self._vector_formats = self.available_vector_formats(has_geo)

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
        self.mask_format_combo.setCurrentText("GeoTIFF" if prefer_tif_mask else "PNG Mask")
        form.addRow("Mask 格式", self.mask_format_combo)

        self.mask_colored_check = QCheckBox("GeoTIFF 写入标签着色表")
        self.mask_colored_check.setChecked(True)
        form.addRow("Mask 选项", self.mask_colored_check)

        self.hint_label = QLabel(
            "PNG Mask 将按标签颜色导出为 RGB 图像；GeoTIFF Mask 保持单波段标签值。"
        )
        self.hint_label.setWordWrap(True)
        layout.addLayout(form)
        layout.addWidget(self.hint_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.export_vector_check.toggled.connect(self._update_enabled_state)
        self.export_mask_check.toggled.connect(self._update_enabled_state)
        self.vector_format_combo.currentTextChanged.connect(self._update_enabled_state)
        self.mask_format_combo.currentTextChanged.connect(self._update_enabled_state)
        self._update_enabled_state()

    def _browse_dir(self) -> None:
        """_browse_dir。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        current = self.dir_edit.text().strip()
        selected = QFileDialog.getExistingDirectory(self, "选择导出目录", current or str(Path.home()))
        if selected:
            self.dir_edit.setText(selected)

    def _update_enabled_state(self) -> None:
        """_update_enabled_state。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        vector_enabled = self.export_vector_check.isChecked()
        mask_enabled = self.export_mask_check.isChecked()
        self.vector_format_combo.setEnabled(vector_enabled)
        self.mask_format_combo.setEnabled(mask_enabled)
        self.mask_colored_check.setEnabled(mask_enabled and self.mask_format_combo.currentText() == "GeoTIFF")

    def _accept(self) -> None:
        """_accept。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
            "vector_extension": self._vector_formats[self.vector_format_combo.currentText()],
            "mask_format": self.mask_format_combo.currentText(),
            "mask_extension": self.MASK_FORMATS[self.mask_format_combo.currentText()],
            "mask_colored": self.mask_colored_check.isChecked(),
        }
        self.accepted_settings.emit(settings)
        self.accept()

    @classmethod
    def get_settings(cls, default_name: str, default_dir: str, has_geo: bool, prefer_tif_mask: bool, parent=None):
        """get_settings。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            default_name (str): 输入参数。
            default_dir (str): 输入参数。
            has_geo (bool): 输入参数。
            prefer_tif_mask (bool): 输入参数。
            parent (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        dialog = cls(default_name, default_dir, has_geo, prefer_tif_mask, parent)
        if dialog.exec() != QDialog.Accepted:
            return None
        vector_format = dialog.vector_format_combo.currentText()
        return {
            "output_dir": dialog.dir_edit.text().strip(),
            "base_name": dialog.base_name_edit.text().strip(),
            "export_vector": dialog.export_vector_check.isChecked(),
            "export_mask": dialog.export_mask_check.isChecked(),
            "vector_format": vector_format,
            "vector_extension": dialog._vector_formats[vector_format],
            "mask_format": dialog.mask_format_combo.currentText(),
            "mask_extension": dialog.MASK_FORMATS[dialog.mask_format_combo.currentText()],
            "mask_colored": dialog.mask_colored_check.isChecked(),
        }
