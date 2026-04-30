"""
图层状态管理。
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPainter

from .models import LayerSpec, LayerState, RasterLayer
from .styles import LayerDisplaySettings, default_display_settings


def raster_layer_to_spec(layer: RasterLayer) -> LayerSpec:
    """raster_layer_to_spec。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        layer (RasterLayer): 输入参数。
    返回:
        LayerSpec: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    display = layer.display_settings
    return LayerSpec(
        id=layer.id,
        name=layer.name,
        layer_type="raster",
        visible=bool(layer.visible),
        opacity=float(display.opacity),
        locked=bool(layer.locked),
        selectable=True,
        blend_mode=str(display.blend_mode),
        metadata=dict(layer.custom_properties or {}),
    )


class LayerManager(QObject):
    active_layer_changed = Signal(object)
    layer_style_changed = Signal(str)
    layer_display_changed = Signal(str)
    layer_order_changed = Signal()
    layer_selection_changed = Signal(str, bool)

    def __init__(self):
        """__init__。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        super().__init__()
        self._layers: OrderedDict[str, LayerState] = OrderedDict()
        self._active_layer_id: str | None = None

    def add_layer(self, spec: LayerSpec | RasterLayer, item=None) -> LayerState:
        """add_layer。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            spec (LayerSpec | RasterLayer): 输入参数。
            item (Any): 输入参数。
        返回:
            LayerState: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if isinstance(spec, RasterLayer):
            layer = spec
            state = LayerState(spec=raster_layer_to_spec(layer), z_order=len(self._layers), item=item, layer=layer)
        else:
            state = LayerState(spec=spec, z_order=len(self._layers), item=item, layer=None)
        self._layers[state.spec.id] = state
        self._sync_z_order()
        if self._active_layer_id is None and state.spec.selectable:
            self.set_active_layer(state.spec.id)
        return state

    def add_raster_layer(self, layer: RasterLayer, item=None) -> LayerState:
        """add_raster_layer。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer (RasterLayer): 输入参数。
            item (Any): 输入参数。
        返回:
            LayerState: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        existing = self._layers.get(layer.id)
        if existing is not None:
            existing.layer = layer
            existing.spec = raster_layer_to_spec(layer)
            if item is not None:
                existing.item = item
            self._apply_item_state(existing)
            self.layer_style_changed.emit(layer.id)
            self.layer_display_changed.emit(layer.id)
            return existing
        state = self.add_layer(layer, item=item)
        self.layer_style_changed.emit(layer.id)
        self.layer_display_changed.emit(layer.id)
        return state

    def remove_layer(self, layer_id: str) -> LayerState | None:
        """remove_layer。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str): 输入参数。
        返回:
            LayerState | None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        state = self._layers.pop(layer_id, None)
        if self._active_layer_id == layer_id:
            self._active_layer_id = None
            next_id = next(iter(self._layers.keys()), None)
            if next_id is not None:
                self.set_active_layer(next_id)
            else:
                self.active_layer_changed.emit(None)
        self._sync_z_order()
        return state

    def layer(self, layer_id: str) -> LayerState | None:
        """layer。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str): 输入参数。
        返回:
            LayerState | None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        return self._layers.get(layer_id)

    def layers(self) -> list[LayerState]:
        """layers。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            list[LayerState]: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        return list(self._layers.values())

    def active_layer_id(self) -> str | None:
        """active_layer_id。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            str | None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        return self._active_layer_id

    def active_raster_layer(self) -> RasterLayer | None:
        """active_raster_layer。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            RasterLayer | None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        state = self.layer(self._active_layer_id) if self._active_layer_id else None
        return None if state is None else state.layer

    def set_active_layer(self, layer_id: str | None) -> None:
        """set_active_layer。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str | None): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if layer_id is not None and layer_id not in self._layers:
            raise KeyError(layer_id)
        previous = self._active_layer_id
        if previous == layer_id:
            return
        if previous is not None and previous in self._layers and self._layers[previous].layer is not None:
            self._layers[previous].layer.selected = False
            self.layer_selection_changed.emit(previous, False)
        self._active_layer_id = layer_id
        if layer_id is not None and self._layers[layer_id].layer is not None:
            self._layers[layer_id].layer.selected = True
            self.layer_selection_changed.emit(layer_id, True)
        self.active_layer_changed.emit(layer_id)

    def set_item(self, layer_id: str, item) -> None:
        """set_item。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str): 输入参数。
            item (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        state = self._require(layer_id)
        state.item = item
        self._apply_item_state(state)

    def set_visible(self, layer_id: str, visible: bool) -> None:
        """set_visible。

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
        state = self._require(layer_id)
        state.spec.visible = bool(visible)
        if state.layer is not None:
            state.layer.visible = bool(visible)
            state.layer.display_settings = replace(state.layer.display_settings, visible=bool(visible))
        self._apply_item_state(state)
        self.layer_display_changed.emit(layer_id)

    def set_opacity(self, layer_id: str, opacity: float) -> None:
        """set_opacity。

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
        state = self._require(layer_id)
        opacity = max(0.0, min(float(opacity), 1.0))
        state.spec.opacity = opacity
        if state.layer is not None:
            state.layer.display_settings = replace(state.layer.display_settings, opacity=opacity)
        self._apply_item_state(state)
        self.layer_display_changed.emit(layer_id)

    def move_layer(self, layer_id: str, target_index: int) -> None:
        """move_layer。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str): 输入参数。
            target_index (int): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if layer_id not in self._layers:
            raise KeyError(layer_id)
        items = list(self._layers.items())
        current_index = next(index for index, item in enumerate(items) if item[0] == layer_id)
        entry = items.pop(current_index)
        target_index = max(0, min(int(target_index), len(items)))
        items.insert(target_index, entry)
        self._layers = OrderedDict(items)
        self._sync_z_order()
        self.layer_order_changed.emit()

    def set_blend_mode(self, layer_id: str, blend_mode: str) -> None:
        """set_blend_mode。

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
        state = self._require(layer_id)
        state.spec.blend_mode = str(blend_mode or "source_over")
        if state.layer is not None:
            state.layer.display_settings = replace(state.layer.display_settings, blend_mode=state.spec.blend_mode)
        self._apply_item_state(state)
        self.layer_display_changed.emit(layer_id)

    def set_render_style(self, layer_id: str, render_style) -> None:
        """set_render_style。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str): 输入参数。
            render_style (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        state = self._require(layer_id)
        if state.layer is None:
            return
        state.layer.render_style = render_style
        state.layer.revision += 1
        self.layer_style_changed.emit(layer_id)

    def set_display_settings(self, layer_id: str, display_settings: LayerDisplaySettings) -> None:
        """set_display_settings。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str): 输入参数。
            display_settings (LayerDisplaySettings): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        state = self._require(layer_id)
        if state.layer is None:
            return
        state.layer.display_settings = display_settings
        state.layer.visible = bool(display_settings.visible)
        state.layer.revision += 1
        state.spec = raster_layer_to_spec(state.layer)
        self._apply_item_state(state)
        self.layer_display_changed.emit(layer_id)

    def update_raster_layer(self, layer_id: str, *, source=None, metadata=None, render_style=None, display_settings=None, custom_properties=None) -> None:
        """update_raster_layer。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str): 输入参数。
            source (Any): 输入参数。
            metadata (Any): 输入参数。
            render_style (Any): 输入参数。
            display_settings (Any): 输入参数。
            custom_properties (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        state = self._require(layer_id)
        if state.layer is None:
            display = display_settings or default_display_settings(nodata_value=getattr(metadata, "nodata", None) if metadata is not None else None)
            state.layer = RasterLayer(
                id=state.spec.id,
                name=state.spec.name,
                source=source,
                metadata=metadata,
                render_style=render_style,
                display_settings=display,
                visible=state.spec.visible,
                selected=self._active_layer_id == layer_id,
                locked=state.spec.locked,
                custom_properties=dict(custom_properties or {}),
            )
        else:
            if source is not None:
                state.layer.source = source
            if metadata is not None:
                state.layer.metadata = metadata
            if render_style is not None:
                state.layer.render_style = render_style
            if display_settings is not None:
                state.layer.display_settings = display_settings
            if custom_properties is not None:
                state.layer.custom_properties.update(custom_properties)
            state.layer.revision += 1
        state.spec = raster_layer_to_spec(state.layer)
        self._apply_item_state(state)

    def to_specs(self) -> list[LayerSpec]:
        """to_specs。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            list[LayerSpec]: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        return [state.spec for state in self.layers()]

    def _require(self, layer_id: str) -> LayerState:
        """_require。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            layer_id (str): 输入参数。
        返回:
            LayerState: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        state = self.layer(layer_id)
        if state is None:
            raise KeyError(layer_id)
        return state

    def _sync_z_order(self) -> None:
        """_sync_z_order。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        for index, state in enumerate(self._layers.values()):
            state.z_order = index
            self._apply_item_state(state)

    def _apply_item_state(self, state: LayerState) -> None:
        """_apply_item_state。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            state (LayerState): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        item = state.item
        if item is None:
            return
        if hasattr(item, "setVisible"):
            item.setVisible(state.spec.visible)
        if hasattr(item, "setOpacity"):
            item.setOpacity(state.spec.opacity)
        if hasattr(item, "setZValue"):
            item.setZValue(state.z_order)
        composition_map = {
            "source_over": QPainter.CompositionMode_SourceOver,
            "multiply": QPainter.CompositionMode_Multiply,
            "screen": QPainter.CompositionMode_Screen,
            "overlay": QPainter.CompositionMode_Overlay,
            "plus": QPainter.CompositionMode_Plus,
        }
        if hasattr(item, "setCompositionMode"):
            mode = composition_map.get(state.spec.blend_mode, QPainter.CompositionMode_SourceOver)
            try:
                item.setCompositionMode(mode)
            except Exception:
                pass
