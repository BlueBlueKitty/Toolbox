"""
通用图层控制面板。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.rendering.models import LayerSpec


class _FlatLayerTreeWidget(QTreeWidget):
    """Flat, deterministic layer-stack drag handling plus external file drops."""

    files_dropped = Signal(list)
    layer_order_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dragged_item: QTreeWidgetItem | None = None
        self._drag_start_pos = None
        self._dragging_layer = False
        self._drop_indicator = QFrame(self.viewport())
        self._drop_indicator.setFrameShape(QFrame.HLine)
        self._drop_indicator.setFrameShadow(QFrame.Plain)
        self._drop_indicator.setStyleSheet("color: #3b82f6; background: #3b82f6;")
        self._drop_indicator.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._drop_indicator.hide()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if paths:
                self.files_dropped.emit(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._dragged_item = self.itemAt(event.position().toPoint())
            self._drag_start_pos = event.position().toPoint()
            self._dragging_layer = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            self._dragged_item is not None
            and self._drag_start_pos is not None
            and event.buttons() & Qt.LeftButton
            and (event.position().toPoint() - self._drag_start_pos).manhattanLength() >= QApplication.startDragDistance()
        ):
            self._dragging_layer = True
            self.viewport().setCursor(Qt.ClosedHandCursor)
            self._show_drop_indicator(self._drop_index(event.position().toPoint()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        item = self._dragged_item
        was_dragging = self._dragging_layer
        self._dragged_item = None
        self._drag_start_pos = None
        self._dragging_layer = False
        self.viewport().unsetCursor()
        self._drop_indicator.hide()
        if event.button() == Qt.LeftButton and was_dragging and item is not None:
            self._move_top_level_item(item, self._drop_index(event.position().toPoint()))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        self._drop_indicator.hide()
        super().leaveEvent(event)

    def _drop_index(self, position) -> int:
        """Return a top-level insertion index; no drop can create children."""
        for index in range(self.topLevelItemCount()):
            rect = self.visualItemRect(self.topLevelItem(index))
            if position.y() < rect.center().y():
                return index
        return self.topLevelItemCount()

    def _show_drop_indicator(self, target_index: int) -> None:
        """Draw the insertion line that native tree dragging would provide."""
        count = self.topLevelItemCount()
        if count <= 0:
            self._drop_indicator.hide()
            return
        target_index = max(0, min(int(target_index), count))
        if target_index < count:
            y = self.visualItemRect(self.topLevelItem(target_index)).top()
        else:
            y = self.visualItemRect(self.topLevelItem(count - 1)).bottom() + 1
        self._drop_indicator.setGeometry(0, y - 1, self.viewport().width(), 2)
        self._drop_indicator.show()
        self._drop_indicator.raise_()

    def _move_top_level_item(self, item: QTreeWidgetItem, target_index: int) -> None:
        source_index = self.indexOfTopLevelItem(item)
        if source_index < 0:
            return
        target_index = max(0, min(int(target_index), self.topLevelItemCount()))
        if source_index < target_index:
            target_index -= 1
        if source_index == target_index:
            return
        moved_item = self.takeTopLevelItem(source_index)
        self.insertTopLevelItem(target_index, moved_item)
        self.setCurrentItem(moved_item)
        self.layer_order_dropped.emit([
            self.topLevelItem(index).data(0, Qt.UserRole)
            for index in range(self.topLevelItemCount())
        ])


class LayerPanelWidget(QGroupBox):
    visibility_changed = Signal(str, bool)
    window_visibility_changed = Signal(str, str, bool)
    # The full UI order (top -> bottom) is emitted once an internal drag ends.
    order_changed = Signal(list)
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
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__("图层", parent)
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        self.layer_tree = _FlatLayerTreeWidget()
        self.layer_tree.setAcceptDrops(True)
        self.layer_tree.setColumnCount(3)
        self.layer_tree.setHeaderLabels(["图层", "窗口1", "窗口2"])
        self.layer_tree.setRootIsDecorated(False)
        self.layer_tree.setItemsExpandable(False)
        # Internal movement is handled above so QTreeWidget can never create
        # child rows; DropOnly keeps external file dragging available.
        self.layer_tree.setDragDropMode(QAbstractItemView.DropOnly)
        self.layer_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.layer_tree.itemChanged.connect(self._on_item_changed)
        self.layer_tree.itemClicked.connect(self._on_item_clicked)
        self.layer_tree.currentItemChanged.connect(self._on_current_item_changed)
        self.layer_tree.customContextMenuRequested.connect(self._open_context_menu)
        self.layer_tree.files_dropped.connect(self.files_dropped)
        self.layer_tree.layer_order_dropped.connect(self._on_layer_order_dropped)
        self.layer_tree.setColumnWidth(0, 220)
        self.layer_tree.setColumnWidth(1, 72)
        self.layer_tree.setColumnWidth(2, 72)
        layout.addWidget(self.layer_tree)
        self._updating = False
        self._last_changed_item = None
        self._window_checkboxes: dict[tuple[str, str], QCheckBox] = {}

    def set_layers(self, layers: list[LayerSpec]) -> None:
        self._updating = True
        self.layer_tree.clear()
        self._window_checkboxes.clear()
        for layer in layers:
            item = QTreeWidgetItem([layer.name, "", ""])
            item.setData(0, Qt.UserRole, layer.id)
            item.setData(0, Qt.UserRole + 1, bool(getattr(layer, "locked", False)))
            item.setData(0, Qt.UserRole + 2, str(getattr(layer, "layer_type", "")))
            item.setData(0, Qt.UserRole + 3, float(getattr(layer, "opacity", 1.0)))
            item.setData(0, Qt.UserRole + 4, str(getattr(layer, "blend_mode", "source_over")))
            # The panel is a flat stack.  Allowing ``OnItem`` drops makes
            # QTreeWidget turn a layer into an invisible child item.
            flags = item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled | Qt.ItemNeverHasChildren
            item.setFlags(flags & ~Qt.ItemIsDropEnabled)
            item.setCheckState(0, Qt.Checked if layer.visible else Qt.Unchecked)
            self.layer_tree.addTopLevelItem(item)
            self._attach_window_checkbox(item, "viewer_1", 1, True)
            self._attach_window_checkbox(item, "viewer_2", 2, True)
        self._updating = False

    def _attach_window_checkbox(self, item: QTreeWidgetItem, window_id: str, column: int, visible: bool) -> None:
        layer_id = item.data(0, Qt.UserRole)
        wrapper = QWidget(self.layer_tree)
        inner = QHBoxLayout(wrapper)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setAlignment(Qt.AlignCenter)
        check = QCheckBox(wrapper)
        check.setChecked(bool(visible))
        check.toggled.connect(lambda checked, lid=layer_id, wid=window_id: self._on_window_toggled(lid, wid, checked))
        inner.addWidget(check)
        self.layer_tree.setItemWidget(item, column, wrapper)
        self._window_checkboxes[(str(layer_id), window_id)] = check

    def _on_window_toggled(self, layer_id: str, window_id: str, visible: bool) -> None:
        if self._updating:
            return
        self.window_visibility_changed.emit(str(layer_id), window_id, bool(visible))

    def set_window_visibility(self, layer_id: str, window_id: str, visible: bool) -> None:
        check = self._window_checkboxes.get((str(layer_id), window_id))
        if check is None:
            return
        self._updating = True
        check.setChecked(bool(visible))
        self._updating = False

    def set_window_controls_enabled(self, layer_id: str, enabled: bool) -> None:
        """Enable or disable the two per-window controls for a layer."""
        for window_id in ("viewer_1", "viewer_2"):
            check = self._window_checkboxes.get((str(layer_id), window_id))
            if check is not None:
                check.setEnabled(bool(enabled))

    def set_layer_checked(self, layer_id: str, visible: bool) -> None:
        item = self._item_for_layer(layer_id)
        if item is None:
            return
        self._updating = True
        item.setCheckState(0, Qt.Checked if visible else Qt.Unchecked)
        self._updating = False

    def layer_order(self) -> list[str]:
        return [
            self.layer_tree.topLevelItem(index).data(0, Qt.UserRole)
            for index in range(self.layer_tree.topLevelItemCount())
        ]

    def set_current_layer(self, layer_id: str | None) -> None:
        self._updating = True
        try:
            if layer_id is None:
                self.layer_tree.setCurrentItem(None)
                return
            item = self._item_for_layer(layer_id)
            if item is not None:
                self.layer_tree.setCurrentItem(item)
        finally:
            self._updating = False

    def _item_for_layer(self, layer_id: str):
        for index in range(self.layer_tree.topLevelItemCount()):
            item = self.layer_tree.topLevelItem(index)
            if item.data(0, Qt.UserRole) == layer_id:
                return item
        return None

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0:
            return
        self._last_changed_item = item
        self.visibility_changed.emit(item.data(0, Qt.UserRole), item.checkState(0) == Qt.Checked)

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._updating:
            return
        self._last_changed_item = None

    def _on_layer_order_dropped(self, order: list[str]) -> None:
        if not self._updating:
            self.order_changed.emit(order)

    def _on_current_item_changed(self, current: QTreeWidgetItem, _previous: QTreeWidgetItem) -> None:
        if self._updating:
            return
        layer_id = None if current is None else current.data(0, Qt.UserRole)
        self.layer_selected.emit(layer_id)

    def _open_context_menu(self, pos) -> None:
        item = self.layer_tree.itemAt(pos)
        if item is None:
            return
        layer_id = item.data(0, Qt.UserRole)
        locked = bool(item.data(0, Qt.UserRole + 1))
        layer_type = str(item.data(0, Qt.UserRole + 2) or "")
        menu = QMenu(self.layer_tree)
        toggle_action = menu.addAction("隐藏图层" if item.checkState(0) == Qt.Checked else "显示图层")
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
        action = menu.exec(self.layer_tree.viewport().mapToGlobal(pos))
        if action is None:
            return
        if action == toggle_action:
            self._updating = True
            item.setCheckState(0, Qt.Unchecked if item.checkState(0) == Qt.Checked else Qt.Checked)
            self._updating = False
            self.visibility_changed.emit(layer_id, item.checkState(0) == Qt.Checked)
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
            current_opacity = item.data(0, Qt.UserRole + 3)
            try:
                opacity_percent = int(round(float(current_opacity) * 100.0))
            except Exception:
                opacity_percent = 100
            value, ok = QInputDialog.getInt(
                self.layer_tree,
                "设置透明度",
                "透明度(0-100):",
                max(0, min(100, opacity_percent)),
                0,
                100,
                1,
            )
            if ok:
                item.setData(0, Qt.UserRole + 3, max(0.0, min(1.0, float(value) / 100.0)))
                self.opacity_changed.emit(layer_id, max(0.0, min(1.0, float(value) / 100.0)))
            return
        if nodata_action is not None and action == nodata_action:
            value_text, ok = QInputDialog.getText(
                self.layer_tree,
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
            item.setData(0, Qt.UserRole + 4, mode)
            self.blend_mode_changed.emit(layer_id, mode)

    def dragEnterEvent(self, event) -> None:
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        mime = event.mimeData()
        if mime is None or not mime.hasUrls():
            super().dropEvent(event)
            return
        local_paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
        if local_paths:
            self.files_dropped.emit(local_paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)
