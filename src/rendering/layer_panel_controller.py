"""
通用图层面板控制器（可复用于不同工具窗口）。
"""

from __future__ import annotations

from typing import Callable

from src.rendering.layer_operations import is_layer_removable, ui_index_to_z_index


class LayerPanelController:
    def __init__(
        self,
        layer_panel,
        canvas,
        *,
        exclude_layer_ids: set[str] | None = None,
    ):
        """__init__。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_panel (Any): 输入参数。
            canvas (Any): 输入参数。
            exclude_layer_ids (set[str] | None): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self.layer_panel = layer_panel
        self.canvas = canvas
        self.exclude_layer_ids = set(exclude_layer_ids or set())
        self.on_layer_visibility: Callable[[str, bool], None] | None = None
        self.on_layer_order: Callable[[str, int], None] | None = None
        self.on_layer_opacity: Callable[[str, float], None] | None = None
        self.on_layer_blend_mode: Callable[[str, str], None] | None = None
        self.on_layer_selected: Callable[[str | None], None] | None = None
        self.on_layer_remove: Callable[[str], bool] | None = None
        self.on_layer_nodata: Callable[[str, object], None] | None = None
        self.on_layer_style: Callable[[str], None] | None = None
        self.on_layer_property: Callable[[str], None] | None = None
        self.on_zoom_bbox: Callable[[str], tuple[float, float, float, float] | None] | None = None
        self.after_change: Callable[[], None] | None = None
        self._active_layer_slot_connected = False

    def bind(self) -> None:
        """bind。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self.layer_panel.visibility_changed.connect(self._handle_visibility_changed)
        self.layer_panel.order_changed.connect(self._handle_order_changed)
        self.layer_panel.zoom_to_layer_requested.connect(self._handle_zoom_to_layer)
        self.layer_panel.opacity_changed.connect(self._handle_opacity_changed)
        self.layer_panel.blend_mode_changed.connect(self._handle_blend_mode_changed)
        self.layer_panel.layer_selected.connect(self._handle_layer_selected)
        self.layer_panel.remove_layer_requested.connect(self._handle_remove_layer)
        self.layer_panel.move_layer_top_requested.connect(self._handle_move_layer_top)
        self.layer_panel.move_layer_bottom_requested.connect(self._handle_move_layer_bottom)
        self.layer_panel.nodata_alpha_changed.connect(self._handle_nodata_changed)
        self.layer_panel.style_edit_requested.connect(self._handle_style_requested)
        self.layer_panel.property_requested.connect(self._handle_property_requested)
        self._connect_canvas_layer_signal()

    def _connect_canvas_layer_signal(self) -> None:
        """_connect_canvas_layer_signal。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if not hasattr(self.canvas, "layer_manager"):
            return
        try:
            self.canvas.layer_manager.active_layer_changed.connect(self.layer_panel.set_current_layer)
            self._active_layer_slot_connected = True
        except Exception:
            self._active_layer_slot_connected = False

    def _disconnect_canvas_layer_signal(self, canvas) -> None:
        """_disconnect_canvas_layer_signal。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            canvas (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if not self._active_layer_slot_connected or not hasattr(canvas, "layer_manager"):
            return
        try:
            canvas.layer_manager.active_layer_changed.disconnect(self.layer_panel.set_current_layer)
        except Exception:
            pass
        self._active_layer_slot_connected = False

    def set_canvas(self, canvas) -> None:
        """set_canvas。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            canvas (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if canvas is self.canvas:
            return
        old_canvas = self.canvas
        self._disconnect_canvas_layer_signal(old_canvas)
        self.canvas = canvas
        self._connect_canvas_layer_signal()

    def rebuild_panel_items(self) -> None:
        """rebuild_panel_items。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        states = [
            state
            for state in self.canvas.layer_manager.layers()
            if state.spec.id not in self.exclude_layer_ids
        ]
        self.layer_panel.set_layers([state.spec for state in reversed(states)])

    def apply_visibility_map(self, visibility_map: dict[str, bool]) -> None:
        """apply_visibility_map。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            visibility_map (dict[str, bool]): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        for layer_id, visible in (visibility_map or {}).items():
            self.layer_panel.set_layer_checked(layer_id, bool(visible))
            if self.canvas.layer_manager.layer(layer_id):
                self.canvas.set_layer_visible(layer_id, bool(visible))

    def _invoke_after_change(self) -> None:
        """_invoke_after_change。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if self.after_change is not None:
            self.after_change()

    def _handle_visibility_changed(self, layer_id: str, visible: bool) -> None:
        """_handle_visibility_changed。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str): 输入参数。
            visible (bool): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if self.canvas.layer_manager.layer(layer_id):
            self.canvas.set_layer_visible(layer_id, visible)
        if self.on_layer_visibility is not None:
            self.on_layer_visibility(layer_id, visible)
        self._invoke_after_change()

    def _handle_order_changed(self, layer_id: str, ui_index: int) -> None:
        """_handle_order_changed。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str): 输入参数。
            ui_index (int): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        states = [
            state
            for state in self.canvas.layer_manager.layers()
            if state.spec.id not in self.exclude_layer_ids
        ]
        if self.canvas.layer_manager.layer(layer_id):
            # 映射到全量图层序号，避免被未显示在面板中的内部图层“卡住”
            total_all = len(self.canvas.layer_manager.layers())
            z_index = ui_index_to_z_index(ui_index, len(states))
            base_index = max(0, total_all - len(states))
            self.canvas.move_layer(layer_id, base_index + z_index)
        if self.on_layer_order is not None:
            self.on_layer_order(layer_id, ui_index)
        self._invoke_after_change()

    def _handle_zoom_to_layer(self, layer_id: str) -> None:
        """_handle_zoom_to_layer。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if self.on_zoom_bbox is None:
            return
        bbox = self.on_zoom_bbox(layer_id)
        if bbox is None:
            self.canvas.fit_in_view()
            return
        x, y, width, height = bbox
        self.canvas.view_box.setRange(
            xRange=(x, x + max(width, 1.0)),
            yRange=(y, y + max(height, 1.0)),
            padding=0.05,
        )
        self.canvas.refresh_view()

    def _handle_opacity_changed(self, layer_id: str, opacity: float) -> None:
        """_handle_opacity_changed。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str): 输入参数。
            opacity (float): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if self.canvas.layer_manager.layer(layer_id):
            self.canvas.set_layer_opacity(layer_id, opacity)
        if self.on_layer_opacity is not None:
            self.on_layer_opacity(layer_id, opacity)
        self._invoke_after_change()

    def _handle_blend_mode_changed(self, layer_id: str, blend_mode: str) -> None:
        """_handle_blend_mode_changed。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str): 输入参数。
            blend_mode (str): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if self.canvas.layer_manager.layer(layer_id):
            self.canvas.set_layer_blend_mode(layer_id, blend_mode)
        if self.on_layer_blend_mode is not None:
            self.on_layer_blend_mode(layer_id, blend_mode)
        self._invoke_after_change()

    def _handle_layer_selected(self, layer_id: str | None) -> None:
        """_handle_layer_selected。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str | None): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if hasattr(self.canvas, "layer_manager"):
            try:
                self.canvas.layer_manager.set_active_layer(layer_id)
            except Exception:
                pass
        if self.on_layer_selected is not None:
            self.on_layer_selected(layer_id)

    def _handle_remove_layer(self, layer_id: str) -> None:
        """_handle_remove_layer。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if not is_layer_removable(layer_id):
            return
        if self.on_layer_remove is not None and self.on_layer_remove(layer_id):
            self._invoke_after_change()
            return
        if self.canvas.layer_manager.layer(layer_id):
            try:
                self.canvas.remove_layer(layer_id)
            except Exception:
                pass
        self.rebuild_panel_items()
        self._invoke_after_change()

    def _handle_move_layer_top(self, layer_id: str) -> None:
        """_handle_move_layer_top。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if self.canvas.layer_manager.layer(layer_id):
            self.canvas.move_layer(layer_id, len(self.canvas.layer_manager.layers()) - 1)
            self.rebuild_panel_items()
            self._invoke_after_change()

    def _handle_move_layer_bottom(self, layer_id: str) -> None:
        """_handle_move_layer_bottom。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if self.canvas.layer_manager.layer(layer_id):
            self.canvas.move_layer(layer_id, 0)
            self.rebuild_panel_items()
            self._invoke_after_change()

    def _handle_nodata_changed(self, layer_id: str, value) -> None:
        """_handle_nodata_changed。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str): 输入参数。
            value (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if self.on_layer_nodata is not None:
            self.on_layer_nodata(layer_id, value)

    def _handle_style_requested(self, layer_id: str) -> None:
        """_handle_style_requested。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if self.on_layer_style is not None:
            self.on_layer_style(layer_id)

    def _handle_property_requested(self, layer_id: str) -> None:
        """_handle_property_requested。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if self.on_layer_property is not None:
            self.on_layer_property(layer_id)
