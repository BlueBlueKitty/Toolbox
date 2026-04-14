"""
魔法棒参数面板。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QGroupBox, QPushButton, QSpinBox

from src.segmentation.models import MagicWandParams


class MagicWandPanel(QGroupBox):
    params_changed = Signal(object)
    confirm_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__("魔法棒", parent)
        layout = QFormLayout(self)
        self.tolerance_spin = QSpinBox()
        self.tolerance_spin.setRange(0, 255)
        self.tolerance_spin.setValue(15)
        self.connectivity_combo = QComboBox()
        self.connectivity_combo.addItems(["4", "8"])
        self.connectivity_combo.setCurrentText("8")
        self.min_area_spin = QSpinBox()
        self.min_area_spin.setRange(1, 100000)
        self.min_area_spin.setValue(16)
        self.smooth_spin = QSpinBox()
        self.smooth_spin.setRange(0, 20)
        self.smooth_spin.setValue(2)
        self.fill_holes_check = QCheckBox("填充孔洞")
        self.fill_holes_check.setChecked(True)
        self.seed_only_check = QCheckBox("仅保留种子连通")
        self.seed_only_check.setChecked(True)
        self.confirm_button = QPushButton("确认预览")
        self.cancel_button = QPushButton("取消预览")
        layout.addRow("阈值", self.tolerance_spin)
        layout.addRow("连通方式", self.connectivity_combo)
        layout.addRow("最小面积", self.min_area_spin)
        layout.addRow("边界平滑", self.smooth_spin)
        layout.addRow(self.fill_holes_check)
        layout.addRow(self.seed_only_check)
        layout.addRow(self.confirm_button)
        layout.addRow(self.cancel_button)
        for widget in [
            self.tolerance_spin,
            self.connectivity_combo,
            self.min_area_spin,
            self.smooth_spin,
            self.fill_holes_check,
            self.seed_only_check,
        ]:
            signal = getattr(widget, "valueChanged", None) or getattr(widget, "currentTextChanged", None) or getattr(widget, "toggled", None)
            signal.connect(self._emit_params)
        self.confirm_button.clicked.connect(self.confirm_requested)
        self.cancel_button.clicked.connect(self.cancel_requested)

    def params(self) -> MagicWandParams:
        return MagicWandParams(
            tolerance=self.tolerance_spin.value(),
            connectivity=int(self.connectivity_combo.currentText()),
            min_area=self.min_area_spin.value(),
            smooth_radius=self.smooth_spin.value(),
            fill_holes=self.fill_holes_check.isChecked(),
            seed_only=self.seed_only_check.isChecked(),
        )

    def _emit_params(self, *_args) -> None:
        self.params_changed.emit(self.params())
