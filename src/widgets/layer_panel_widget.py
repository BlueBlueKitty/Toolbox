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
        self.annotation_check = QCheckBox("显示标注")
        self.annotation_check.setChecked(True)
        self.raster_check = QCheckBox("显示栅格")
        self.raster_check.setChecked(True)
        self.preview_check = QCheckBox("显示预览")
        self.preview_check.setChecked(True)
        layout.addRow(self.image_check)
        layout.addRow(self.annotation_check)
        layout.addRow(self.raster_check)
        layout.addRow(self.preview_check)
        self.image_check.toggled.connect(lambda checked: self.visibility_changed.emit("image", checked))
        self.annotation_check.toggled.connect(lambda checked: self.visibility_changed.emit("annotations", checked))
        self.raster_check.toggled.connect(lambda checked: self.visibility_changed.emit("raster", checked))
        self.preview_check.toggled.connect(lambda checked: self.visibility_changed.emit("preview", checked))
