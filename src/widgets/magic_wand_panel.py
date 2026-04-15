"""
魔法棒参数面板。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.segmentation.models import MagicWandParams


class MagicWandPanel(QGroupBox):
    params_changed = Signal(object)
    merge_preview_changed = Signal(bool)
    confirm_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__("魔法棒", parent)
        layout = QVBoxLayout(self)

        top_row = QHBoxLayout()
        self.single_preview_button = QToolButton()
        self.single_preview_button.setText("单次预览")
        self.single_preview_button.setCheckable(True)
        self.single_preview_button.setChecked(True)
        self.merge_preview_button = QToolButton()
        self.merge_preview_button.setText("合并预览")
        self.merge_preview_button.setCheckable(True)
        top_row.addWidget(self.single_preview_button)
        top_row.addWidget(self.merge_preview_button)
        layout.addLayout(top_row)

        form = QFormLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("RGB", "rgb")
        self.mode_combo.addItem("R", "r")
        self.mode_combo.addItem("G", "g")
        self.mode_combo.addItem("B", "b")
        self.mode_combo.addItem("H", "h")
        self.mode_combo.addItem("S", "s")
        self.mode_combo.addItem("V", "v")
        form.addRow("比较模式", self.mode_combo)

        self.mask_header = QLabel("Mask 控制")
        self.mask_header.setStyleSheet("font-weight: 700; color: #475569; padding-top: 6px;")
        form.addRow(self.mask_header)

        self.connectivity_combo = QComboBox()
        self.connectivity_combo.addItems(["4", "8"])
        self.connectivity_combo.setCurrentText("8")
        form.addRow("连通方式", self.connectivity_combo)

        self.tolerance_slider, self.tolerance_value = self._make_slider(0, 80, 15)
        form.addRow("阈值", self._slider_row(self.tolerance_slider, self.tolerance_value))

        self.min_area_edit = QLineEdit("16")
        form.addRow("最小面积", self.min_area_edit)

        self.fill_holes_check = QCheckBox("填充孔洞")
        self.fill_holes_check.setChecked(False)
        form.addRow(self.fill_holes_check)

        self.vector_header = QLabel("矢量控制")
        self.vector_header.setStyleSheet("font-weight: 700; color: #475569; padding-top: 6px;")
        form.addRow(self.vector_header)

        self.simplify_polygon_check = QCheckBox("简化矢量边界")
        self.simplify_polygon_check.setChecked(True)
        form.addRow(self.simplify_polygon_check)

        self.vector_smooth_slider, self.vector_smooth_value = self._make_slider(0, 12, 2)
        self.vector_smooth_slider.setSingleStep(1)
        self.vector_smooth_slider.setPageStep(1)
        form.addRow("平滑矢量边界", self._slider_row(self.vector_smooth_slider, self.vector_smooth_value))

        layout.addLayout(form)

        self.confirm_button = QPushButton("确认/应用")
        self.cancel_button = QPushButton("取消")
        layout.addWidget(self.confirm_button)
        layout.addWidget(self.cancel_button)

        self.single_preview_button.clicked.connect(lambda checked: self._set_merge_preview(not checked))
        self.merge_preview_button.clicked.connect(lambda checked: self._set_merge_preview(checked))
        for widget in [
            self.mode_combo,
            self.connectivity_combo,
            self.tolerance_slider,
            self.min_area_edit,
            self.fill_holes_check,
            self.simplify_polygon_check,
            self.vector_smooth_slider,
        ]:
            signal = getattr(widget, "valueChanged", None) or getattr(widget, "currentTextChanged", None) or getattr(widget, "toggled", None) or getattr(widget, "textChanged", None)
            signal.connect(self._emit_params)
        self.confirm_button.clicked.connect(self.confirm_requested)
        self.cancel_button.clicked.connect(self.cancel_requested)

    def params(self) -> MagicWandParams:
        try:
            min_area = max(1, int(self.min_area_edit.text().strip() or "1"))
        except ValueError:
            min_area = 16
        return MagicWandParams(
            tolerance=self.tolerance_slider.value(),
            connectivity=int(self.connectivity_combo.currentText()),
            min_area=min_area,
            similarity_mode=str(self.mode_combo.currentData()),
            fill_holes=self.fill_holes_check.isChecked(),
            simplify_polygon=self.simplify_polygon_check.isChecked(),
            vector_smoothness=self.vector_smooth_slider.value(),
        )

    def merge_preview_enabled(self) -> bool:
        return self.merge_preview_button.isChecked()

    def _set_merge_preview(self, enabled: bool) -> None:
        self.merge_preview_button.blockSignals(True)
        self.single_preview_button.blockSignals(True)
        self.merge_preview_button.setChecked(enabled)
        self.single_preview_button.setChecked(not enabled)
        self.merge_preview_button.blockSignals(False)
        self.single_preview_button.blockSignals(False)
        self.merge_preview_changed.emit(enabled)

    def _emit_params(self, *_args) -> None:
        self.params_changed.emit(self.params())

    def _make_slider(self, minimum: int, maximum: int, value: int) -> tuple[QSlider, QLabel]:
        slider = QSlider(Qt.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        label = QLabel(str(value))
        label.setMinimumWidth(36)
        slider.valueChanged.connect(lambda current, target=label: target.setText(str(current)))
        return slider, label

    def _slider_row(self, slider: QSlider, label: QLabel) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(slider, 1)
        layout.addWidget(label)
        return widget
