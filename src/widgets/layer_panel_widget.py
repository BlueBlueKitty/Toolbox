"""
通用图层控制面板。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
)

from src.rendering.models import LayerSpec


class LayerPanelWidget(QGroupBox):
    visibility_changed = Signal(str, bool)
    order_changed = Signal(str, int)
    zoom_to_layer_requested = Signal(str)
    opacity_changed = Signal(str, float)
    blend_mode_changed = Signal(str, str)
    layer_selected = Signal(object)
    remove_layer_requested = Signal(str)
    move_layer_top_requested = Signal(str)
    move_layer_bottom_requested = Signal(str)
    nodata_alpha_changed = Signal(str, object)
    style_edit_requested = Signal(str)
    property_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__("图层", parent)
        layout = QVBoxLayout(self)
        self.layer_list = QListWidget()
        self.layer_list.setDragDropMode(QListWidget.InternalMove)
        self.layer_list.setDefaultDropAction(Qt.MoveAction)
        self.layer_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.layer_list.itemChanged.connect(self._on_item_changed)
        self.layer_list.itemClicked.connect(self._on_item_clicked)
        self.layer_list.currentItemChanged.connect(self._on_current_item_changed)
        self.layer_list.customContextMenuRequested.connect(self._open_context_menu)
        self.layer_list.model().rowsMoved.connect(self._on_rows_moved)
        layout.addWidget(self.layer_list)
        self._updating = False
        self._last_changed_item = None

    def set_layers(self, layers: list[LayerSpec]) -> None:
        self._updating = True
        self.layer_list.clear()
        for layer in layers:
            item = QListWidgetItem(layer.name)
            item.setData(Qt.UserRole, layer.id)
            item.setData(Qt.UserRole + 1, bool(getattr(layer, "locked", False)))
            item.setData(Qt.UserRole + 2, str(getattr(layer, "layer_type", "")))
            item.setData(Qt.UserRole + 3, float(getattr(layer, "opacity", 1.0)))
            item.setData(Qt.UserRole + 4, str(getattr(layer, "blend_mode", "source_over")))
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
        self._last_changed_item = item
        self.visibility_changed.emit(item.data(Qt.UserRole), item.checkState() == Qt.Checked)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        if self._updating:
            return
        self._last_changed_item = None

    def _on_rows_moved(self, *_args) -> None:
        if self._updating:
            return
        for index, layer_id in enumerate(self.layer_order()):
            self.order_changed.emit(layer_id, index)

    def _on_current_item_changed(self, current: QListWidgetItem, _previous: QListWidgetItem) -> None:
        layer_id = None if current is None else current.data(Qt.UserRole)
        self.layer_selected.emit(layer_id)

    def _open_context_menu(self, pos) -> None:
        item = self.layer_list.itemAt(pos)
        if item is None:
            return
        layer_id = item.data(Qt.UserRole)
        locked = bool(item.data(Qt.UserRole + 1))
        layer_type = str(item.data(Qt.UserRole + 2) or "")
        menu = QMenu(self.layer_list)
        toggle_action = menu.addAction("隐藏图层" if item.checkState() == Qt.Checked else "显示图层")
        zoom_action = menu.addAction("缩放到图层")
        move_top_action = menu.addAction("移到顶层")
        move_bottom_action = menu.addAction("移到底层")
        opacity_action = menu.addAction("调节透明度...")
        nodata_action = menu.addAction("调整Nodata值...") if layer_type in {"raster", "raster_overlay"} else None
        style_action = menu.addAction("调整样式...") if layer_type == "vector" else None
        blend_menu = menu.addMenu("叠加方式")
        blend_actions = {}
        for text, mode in [
            ("正常", "source_over"),
            ("正片叠底", "multiply"),
            ("滤色", "screen"),
            ("叠加", "overlay"),
            ("线性加亮", "plus"),
        ]:
            blend_actions[blend_menu.addAction(text)] = mode
        remove_action = None
        if not locked:
            remove_action = menu.addAction("移除图层")
        property_action = menu.addAction("属性")
        action = menu.exec(self.layer_list.viewport().mapToGlobal(pos))
        if action is None:
            return
        if action == toggle_action:
            self._updating = True
            item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
            self._updating = False
            self.visibility_changed.emit(layer_id, item.checkState() == Qt.Checked)
            return
        if action == zoom_action:
            self.zoom_to_layer_requested.emit(layer_id)
            return
        if action == move_top_action:
            self.move_layer_top_requested.emit(layer_id)
            return
        if action == move_bottom_action:
            self.move_layer_bottom_requested.emit(layer_id)
            return
        if action == opacity_action:
            current_opacity = item.data(Qt.UserRole + 3)
            try:
                opacity_percent = int(round(float(current_opacity) * 100.0))
            except Exception:
                opacity_percent = 100
            value, ok = QInputDialog.getInt(
                self.layer_list,
                "设置透明度",
                "透明度(0-100):",
                max(0, min(100, opacity_percent)),
                0,
                100,
                1,
            )
            if ok:
                item.setData(Qt.UserRole + 3, max(0.0, min(1.0, float(value) / 100.0)))
                self.opacity_changed.emit(layer_id, max(0.0, min(1.0, float(value) / 100.0)))
            return
        if nodata_action is not None and action == nodata_action:
            value_text, ok = QInputDialog.getText(
                self.layer_list,
                "调整Nodata值",
                "输入Nodata值（留空清除）:",
                text="",
            )
            if ok:
                text = (value_text or "").strip()
                if not text:
                    self.nodata_alpha_changed.emit(layer_id, None)
                else:
                    try:
                        self.nodata_alpha_changed.emit(layer_id, float(text))
                    except ValueError:
                        self.nodata_alpha_changed.emit(layer_id, text)
            return
        if style_action is not None and action == style_action:
            self.style_edit_requested.emit(layer_id)
            return
        if remove_action is not None and action == remove_action:
            self.remove_layer_requested.emit(layer_id)
            return
        if action == property_action:
            self.property_requested.emit(layer_id)
            return
        mode = blend_actions.get(action)
        if mode:
            item.setData(Qt.UserRole + 4, mode)
            self.blend_mode_changed.emit(layer_id, mode)
