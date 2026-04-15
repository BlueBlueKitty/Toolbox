"""
标签控制面板。
"""

from __future__ import annotations

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
    QColorDialog,
    QInputDialog,
    QLabel,
    QVBoxLayout,
)

from src.segmentation.models import LabelClass


class LabelPanelWidget(QGroupBox):
    active_label_changed = Signal(int)
    labels_changed = Signal(object)

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
        button_row.addWidget(self.add_button)
        button_row.addWidget(self.edit_button)
        layout.addLayout(button_row)
        self.list_widget.currentRowChanged.connect(self._on_current_changed)
        self.add_button.clicked.connect(self._add_label)
        self.edit_button.clicked.connect(self._edit_label)
        self._labels: list[LabelClass] = []

    def set_labels(self, labels: list[LabelClass], active_label_id: int | None = None) -> None:
        self._labels = labels[:]
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        current_row = 0
        for index, label in enumerate(labels):
            item = QListWidgetItem(f"{label.shortcut}  {label.name}")
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
        name, ok = QInputDialog.getText(self, "新增标签", "标签名称")
        if not ok or not name.strip():
            return
        color = QColorDialog.getColor(parent=self)
        if not color.isValid():
            return
        shortcut, ok = QInputDialog.getText(self, "快捷键", "快捷键")
        if not ok or not shortcut.strip():
            return
        next_id = max([label.id for label in self._labels], default=0) + 1
        self._labels.append(LabelClass(next_id, name.strip(), color.name(), shortcut.strip()))
        self.labels_changed.emit(self._labels[:])
        self.set_labels(self._labels, next_id)

    def _edit_label(self) -> None:
        row = self.list_widget.currentRow()
        if not (0 <= row < len(self._labels)):
            return
        label = self._labels[row]
        dialog = LabelEditDialog(label, self)
        if dialog.exec() != QDialog.Accepted:
            return
        edited = dialog.label()
        self._labels[row] = LabelClass(label.id, edited.name, edited.color, edited.shortcut, label.visible, label.locked)
        self.labels_changed.emit(self._labels[:])
        self.set_labels(self._labels, label.id)

    def _make_color_icon(self, color_name: str) -> QIcon:
        pixmap = QPixmap(14, 14)
        pixmap.fill(QColor(color_name))
        return QIcon(pixmap)


class LabelEditDialog(QDialog):
    def __init__(self, label: LabelClass, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑标签")
        self._color = QColor(label.color)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("标签名称"))
        self.name_edit = QLineEdit(label.name)
        layout.addWidget(self.name_edit)
        layout.addWidget(QLabel("快捷键"))
        self.shortcut_edit = QLineEdit(label.shortcut)
        layout.addWidget(self.shortcut_edit)
        self.color_button = QPushButton()
        self.color_button.clicked.connect(self._choose_color)
        layout.addWidget(QLabel("标签颜色"))
        layout.addWidget(self.color_button)
        self._update_color_button()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_color(self):
        color = QColorDialog.getColor(self._color, self, "选择标签颜色")
        if color.isValid():
            self._color = color
            self._update_color_button()

    def _update_color_button(self):
        self.color_button.setText(f"选择颜色: {self._color.name()}")
        self.color_button.setStyleSheet(
            f"background-color: {self._color.name()}; color: {'#000000' if self._color.lightness() > 128 else '#ffffff'};"
        )

    def label(self) -> LabelClass:
        return LabelClass(
            id=0,
            name=self.name_edit.text().strip() or "未命名标签",
            color=self._color.name(),
            shortcut=self.shortcut_edit.text().strip() or "",
        )
