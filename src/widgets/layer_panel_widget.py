"""
通用图层控制面板。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGroupBox, QListWidget, QListWidgetItem, QVBoxLayout

from src.rendering.models import LayerSpec


class LayerPanelWidget(QGroupBox):
    visibility_changed = Signal(str, bool)
    order_changed = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__("图层", parent)
        layout = QVBoxLayout(self)
        self.layer_list = QListWidget()
        self.layer_list.setDragDropMode(QListWidget.InternalMove)
        self.layer_list.setDefaultDropAction(Qt.MoveAction)
        self.layer_list.itemChanged.connect(self._on_item_changed)
        self.layer_list.model().rowsMoved.connect(self._on_rows_moved)
        layout.addWidget(self.layer_list)
        self._updating = False

    def set_layers(self, layers: list[LayerSpec]) -> None:
        self._updating = True
        self.layer_list.clear()
        for layer in layers:
            item = QListWidgetItem(layer.name)
            item.setData(Qt.UserRole, layer.id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
            item.setCheckState(Qt.Checked if layer.visible else Qt.Unchecked)
            self.layer_list.addItem(item)
        self._updating = False

    def set_layer_checked(self, layer_id: str, visible: bool) -> None:
        item = self._item_for_layer(layer_id)
        if item is None:
            return
        self._updating = True
        item.setCheckState(Qt.Checked if visible else Qt.Unchecked)
        self._updating = False

    def layer_order(self) -> list[str]:
        return [
            self.layer_list.item(index).data(Qt.UserRole)
            for index in range(self.layer_list.count())
        ]

    def _item_for_layer(self, layer_id: str):
        for index in range(self.layer_list.count()):
            item = self.layer_list.item(index)
            if item.data(Qt.UserRole) == layer_id:
                return item
        return None

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._updating:
            return
        self.visibility_changed.emit(item.data(Qt.UserRole), item.checkState() == Qt.Checked)

    def _on_rows_moved(self, *_args) -> None:
        if self._updating:
            return
        for index, layer_id in enumerate(self.layer_order()):
            self.order_changed.emit(layer_id, index)
