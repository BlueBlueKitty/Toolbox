"""
标签控制面板。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QColorDialog,
    QInputDialog,
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
        self.list_widget.clear()
        current_row = 0
        for index, label in enumerate(labels):
            item = QListWidgetItem(f"{label.shortcut}  {label.name}  {label.color}")
            self.list_widget.addItem(item)
            if active_label_id == label.id:
                current_row = index
        if labels:
            self.list_widget.setCurrentRow(current_row)

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
        name, ok = QInputDialog.getText(self, "编辑标签", "标签名称", text=label.name)
        if not ok or not name.strip():
            return
        color = QColorDialog.getColor(parent=self)
        if not color.isValid():
            color_name = label.color
        else:
            color_name = color.name()
        shortcut, ok = QInputDialog.getText(self, "快捷键", "快捷键", text=label.shortcut)
        if not ok or not shortcut.strip():
            shortcut = label.shortcut
        self._labels[row] = LabelClass(label.id, name.strip(), color_name, shortcut.strip(), label.visible, label.locked)
        self.labels_changed.emit(self._labels[:])
        self.set_labels(self._labels, label.id)
