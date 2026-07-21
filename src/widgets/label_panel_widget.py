"""
标签控制面板。
"""

from __future__ import annotations

import colorsys
import math

from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QMessageBox,
    QColorDialog,
    QLabel,
    QVBoxLayout,
)

from src.segmentation.models import LabelClass


class LabelPanelWidget(QGroupBox):
    active_label_changed = Signal(int)
    labels_changed = Signal(object)
    label_value_changed = Signal(int, int)
    label_deleted = Signal(int)

    def __init__(self, parent=None):
        super().__init__("标签", parent)
        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("""
            QListWidget::item {
                padding: 6px 8px;
                border-radius: 4px;
            }
            QListWidget::item:selected {
                background: #dbeafe;
                color: #1d4ed8;
                font-weight: 700;
            }
        """)
        layout.addWidget(self.list_widget)
        button_row = QHBoxLayout()
        self.add_button = QPushButton("新增")
        self.edit_button = QPushButton("编辑")
        self.delete_button = QPushButton("删除")
        button_row.addWidget(self.add_button)
        button_row.addWidget(self.edit_button)
        button_row.addWidget(self.delete_button)
        layout.addLayout(button_row)
        self.list_widget.currentRowChanged.connect(self._on_current_changed)
        self.add_button.clicked.connect(self._add_label)
        self.edit_button.clicked.connect(self._edit_label)
        self.delete_button.clicked.connect(self._delete_label)
        self._labels: list[LabelClass] = []
        self._reserved_values: set[int] = set()

    def set_reserved_values(self, values) -> None:
        """保留仍存在于 Mask、但可能尚未定义标签的类别值。"""
        self._reserved_values = {int(value) for value in values if 0 < int(value) <= 65535}

    def set_labels(self, labels: list[LabelClass], active_label_id: int | None = None) -> None:
        self._labels = labels[:]
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        current_row = 0
        for index, label in enumerate(labels):
            item = QListWidgetItem(f"{label.name} · 值 {label.id} · 快捷键 {label.shortcut}")
            item.setIcon(self._make_color_icon(label.color))
            item.setToolTip(f"颜色: {label.color}")
            self.list_widget.addItem(item)
            if active_label_id == label.id:
                current_row = index
        if labels:
            self.list_widget.setCurrentRow(current_row)
        self.list_widget.blockSignals(False)

    def _on_current_changed(self, row: int) -> None:
        if 0 <= row < len(self._labels):
            self.active_label_changed.emit(self._labels[row].id)

    def _add_label(self) -> None:
        next_id = self._next_available_value()
        suggested_color = self._generate_next_label_color()
        dialog = LabelEditDialog(
            LabelClass(next_id, f"类别 {next_id}", suggested_color, str(next_id)),
            existing_labels=self._labels,
            parent=self,
        )
        dialog.setWindowTitle("新增标签")
        if dialog.exec() != QDialog.Accepted:
            return
        created = dialog.label()
        self._labels.append(LabelClass(created.id, created.name, created.color, created.shortcut))
        self.labels_changed.emit(self._labels[:])
        self.set_labels(self._labels, created.id)
        self.active_label_changed.emit(created.id)

    def _edit_label(self) -> None:
        row = self.list_widget.currentRow()
        if not (0 <= row < len(self._labels)):
            return
        label = self._labels[row]
        dialog = LabelEditDialog(label, existing_labels=self._labels, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        edited = dialog.label()
        if edited.id != label.id:
            self.label_value_changed.emit(label.id, edited.id)
        self._labels[row] = LabelClass(edited.id, edited.name, edited.color, edited.shortcut, label.visible, label.locked)
        self.labels_changed.emit(self._labels[:])
        self.set_labels(self._labels, edited.id)

    def _delete_label(self) -> None:
        row = self.list_widget.currentRow()
        if not (0 <= row < len(self._labels)):
            return
        label = self._labels[row]
        confirmed = QMessageBox.question(
            self,
            "删除标签",
            f"确定删除标签“{label.name}”（值 {label.id}）吗？\nMask 中值为 {label.id} 的像素会保留。",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if confirmed != QMessageBox.Yes:
            return
        self.label_deleted.emit(label.id)
        del self._labels[row]
        self.labels_changed.emit(self._labels[:])

    def _next_available_value(self) -> int:
        used = {int(label.id) for label in self._labels} | self._reserved_values
        for value in range(1, 65536):
            if value not in used:
                return value
        raise ValueError("标签值已用尽")

    def _make_color_icon(self, color_name: str) -> QIcon:
        pixmap = QPixmap(14, 14)
        pixmap.fill(QColor(color_name))
        return QIcon(pixmap)

    def _generate_next_label_color(self) -> str:
        return generate_distinct_label_color([label.color for label in self._labels])


def generate_distinct_label_color(existing_colors: list[str]) -> str:
    used_colors = {QColor(color).name().lower() for color in existing_colors if QColor(color).isValid()}
    used_lab = [
        _rgb_to_lab(*_qcolor_to_rgb(QColor(color)))
        for color in existing_colors
        if QColor(color).isValid()
    ]
    if not used_lab:
        return DEFAULT_LABEL_COLOR

    best_color = DEFAULT_LABEL_COLOR
    best_score = -1.0
    for color_name in _candidate_label_colors():
        if color_name in used_colors:
            continue
        candidate_lab = _rgb_to_lab(*_qcolor_to_rgb(QColor(color_name)))
        score = min(_lab_distance(candidate_lab, used) for used in used_lab)
        if score > best_score:
            best_color = color_name
            best_score = score
    return best_color


DEFAULT_LABEL_COLOR = "#1d4ed8"


def _candidate_label_colors() -> list[str]:
    colors: list[str] = []
    seen: set[str] = set()
    golden_ratio = 0.618033988749895
    for index in range(72):
        hue = (index * golden_ratio) % 1.0
        for saturation, value in ((0.72, 0.92), (0.58, 0.88), (0.82, 0.74)):
            red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
            color_name = QColor(int(red * 255), int(green * 255), int(blue * 255)).name().lower()
            if color_name not in seen:
                colors.append(color_name)
                seen.add(color_name)
    return colors


def _qcolor_to_rgb(color: QColor) -> tuple[int, int, int]:
    return color.red(), color.green(), color.blue()


def _rgb_to_lab(red: int, green: int, blue: int) -> tuple[float, float, float]:
    x, y, z = _rgb_to_xyz(red, green, blue)
    white_x, white_y, white_z = 0.95047, 1.0, 1.08883
    fx = _lab_f(x / white_x)
    fy = _lab_f(y / white_y)
    fz = _lab_f(z / white_z)
    return 116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)


def _rgb_to_xyz(red: int, green: int, blue: int) -> tuple[float, float, float]:
    r = _srgb_to_linear(red / 255.0)
    g = _srgb_to_linear(green / 255.0)
    b = _srgb_to_linear(blue / 255.0)
    return (
        r * 0.4124564 + g * 0.3575761 + b * 0.1804375,
        r * 0.2126729 + g * 0.7151522 + b * 0.0721750,
        r * 0.0193339 + g * 0.1191920 + b * 0.9503041,
    )


def _srgb_to_linear(value: float) -> float:
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def _lab_f(value: float) -> float:
    epsilon = 216.0 / 24389.0
    kappa = 24389.0 / 27.0
    if value > epsilon:
        return value ** (1.0 / 3.0)
    return (kappa * value + 16.0) / 116.0


def _lab_distance(first: tuple[float, float, float], second: tuple[float, float, float]) -> float:
    return math.sqrt(sum((left - right) ** 2 for left, right in zip(first, second)))


class LabelEditDialog(QDialog):
    def __init__(self, label: LabelClass, existing_labels: list[LabelClass] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑标签")
        self._color = QColor(label.color)
        self._label_id = label.id
        self._existing_labels = existing_labels[:] if existing_labels else []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("标签名称"))
        self.name_edit = QLineEdit(label.name)
        layout.addWidget(self.name_edit)
        layout.addWidget(QLabel("快捷键"))
        self.shortcut_edit = QLineEdit(label.shortcut)
        layout.addWidget(self.shortcut_edit)
        layout.addWidget(QLabel("标签值"))
        self.value_spin = QSpinBox()
        self.value_spin.setRange(1, 65535)
        self.value_spin.setValue(int(label.id))
        layout.addWidget(self.value_spin)
        self.color_button = QPushButton()
        self.color_button.clicked.connect(self._choose_color)
        layout.addWidget(QLabel("标签颜色"))
        layout.addWidget(self.color_button)
        self._update_color_button()
        self.validation_label = QLabel("")
        self.validation_label.setStyleSheet("color: #dc2626;")
        layout.addWidget(self.validation_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.ok_button = buttons.button(QDialogButtonBox.Ok)
        self.name_edit.textChanged.connect(self._validate)
        self.shortcut_edit.textChanged.connect(self._validate)
        self.value_spin.valueChanged.connect(self._validate)
        self._validate()

    def _choose_color(self):
        color = QColorDialog.getColor(self._color, self, "选择标签颜色")
        if color.isValid():
            self._color = color
            self._update_color_button()
            self._validate()

    def _update_color_button(self):
        self.color_button.setText(f"选择颜色: {self._color.name()}")
        self.color_button.setStyleSheet(
            f"background-color: {self._color.name()}; color: {'#000000' if self._color.lightness() > 128 else '#ffffff'};"
        )

    def _validate(self):
        name = self.name_edit.text().strip()
        shortcut = self.shortcut_edit.text().strip()
        value = self.value_spin.value()
        color_name = self._color.name().lower()
        error = ""
        if not name:
            error = "标签名称不能为空。"
        elif any(item.name == name and item.id != self._label_id for item in self._existing_labels):
            error = "标签名称已存在，请立即修改。"
        elif any(item.color.lower() == color_name and item.id != self._label_id for item in self._existing_labels):
            error = "标签颜色已存在，请立即修改。"
        elif any(item.id == value and item.id != self._label_id for item in self._existing_labels):
            error = "标签值已存在，请立即修改。"
        elif not shortcut:
            error = "快捷键不能为空。"
        self.validation_label.setText(error)
        if hasattr(self, "ok_button"):
            self.ok_button.setEnabled(not error)

    def label(self) -> LabelClass:
        return LabelClass(
            id=self.value_spin.value(),
            name=self.name_edit.text().strip() or "未命名标签",
            color=self._color.name(),
            shortcut=self.shortcut_edit.text().strip() or "",
        )
