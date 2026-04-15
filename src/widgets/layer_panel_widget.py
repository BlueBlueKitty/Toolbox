"""
图层控制面板。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QFormLayout, QGroupBox, QWidget


class LayerPanelWidget(QGroupBox):
    visibility_changed = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__("图层", parent)
        layout = QFormLayout(self)
        self.image_check = QCheckBox("显示图像")
        self.image_check.setChecked(True)
        self.annotation_check = QCheckBox("显示矢量")
        self.annotation_check.setChecked(True)
        self.raster_check = QCheckBox("显示Mask")
        self.raster_check.setChecked(True)
        self.preview_vector_check = QCheckBox("显示矢量预览")
        self.preview_vector_check.setChecked(True)
        self.preview_mask_check = QCheckBox("显示Mask预览")
        self.preview_mask_check.setChecked(True)
        layout.addRow(self.image_check)
        layout.addRow(self.annotation_check)
        layout.addRow(self.raster_check)
        layout.addRow(self.preview_vector_check)
        layout.addRow(self.preview_mask_check)
        self.image_check.toggled.connect(lambda checked: self.visibility_changed.emit("image", checked))
        self.annotation_check.toggled.connect(lambda checked: self.visibility_changed.emit("annotations", checked))
        self.raster_check.toggled.connect(lambda checked: self.visibility_changed.emit("raster", checked))
        self.preview_vector_check.toggled.connect(lambda checked: self.visibility_changed.emit("preview_vector", checked))
        self.preview_mask_check.toggled.connect(lambda checked: self.visibility_changed.emit("preview_mask", checked))
