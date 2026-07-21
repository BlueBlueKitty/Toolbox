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
        super().__init__()
        self._layers: OrderedDict[str, LayerState] = OrderedDict()
        self._active_layer_id: str | None = None

    def add_layer(self, spec: LayerSpec | RasterLayer, item=None) -> LayerState:
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
        return self._layers.get(layer_id)

    def layers(self) -> list[LayerState]:
        return list(self._layers.values())

    def active_layer_id(self) -> str | None:
        return self._active_layer_id

    def active_raster_layer(self) -> RasterLayer | None:
        state = self.layer(self._active_layer_id) if self._active_layer_id else None
        return None if state is None else state.layer

    def set_active_layer(self, layer_id: str | None) -> None:
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
        state = self._require(layer_id)
        state.item = item
        self._apply_item_state(state)

    def set_visible(self, layer_id: str, visible: bool) -> None:
        state = self._require(layer_id)
        state.spec.visible = bool(visible)
        if state.layer is not None:
            state.layer.visible = bool(visible)
            state.layer.display_settings = replace(state.layer.display_settings, visible=bool(visible))
        self._apply_item_state(state)
        self.layer_display_changed.emit(layer_id)

    def set_opacity(self, layer_id: str, opacity: float) -> None:
        state = self._require(layer_id)
        opacity = max(0.0, min(float(opacity), 1.0))
        state.spec.opacity = opacity
        if state.layer is not None:
            state.layer.display_settings = replace(state.layer.display_settings, opacity=opacity)
        self._apply_item_state(state)
        self.layer_display_changed.emit(layer_id)

    def move_layer(self, layer_id: str, target_index: int) -> None:
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

    def set_layer_order(self, layer_ids: list[str]) -> None:
        """Apply a complete low-to-high order in one model update.

        Unknown IDs are ignored. Existing IDs that the caller omitted are
        retained after the requested ones in their previous relative order.
        """
        requested = [str(layer_id) for layer_id in layer_ids if str(layer_id) in self._layers]
        requested_set = set(requested)
        ordered = [(layer_id, self._layers[layer_id]) for layer_id in requested]
        ordered.extend((layer_id, state) for layer_id, state in self._layers.items() if layer_id not in requested_set)
        if [layer_id for layer_id, _state in ordered] == list(self._layers):
            return
        self._layers = OrderedDict(ordered)
        self._sync_z_order()
        self.layer_order_changed.emit()

    def set_blend_mode(self, layer_id: str, blend_mode: str) -> None:
        state = self._require(layer_id)
        state.spec.blend_mode = str(blend_mode or "source_over")
        if state.layer is not None:
            state.layer.display_settings = replace(state.layer.display_settings, blend_mode=state.spec.blend_mode)
        self._apply_item_state(state)
        self.layer_display_changed.emit(layer_id)

    def set_render_style(self, layer_id: str, render_style) -> None:
        state = self._require(layer_id)
        if state.layer is None:
            return
        state.layer.render_style = render_style
        state.layer.revision += 1
        self.layer_style_changed.emit(layer_id)

    def set_display_settings(self, layer_id: str, display_settings: LayerDisplaySettings) -> None:
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
        return [state.spec for state in self.layers()]

    def _require(self, layer_id: str) -> LayerState:
        state = self.layer(layer_id)
        if state is None:
            raise KeyError(layer_id)
        return state

    def _sync_z_order(self) -> None:
        for index, state in enumerate(self._layers.values()):
            state.z_order = index
            self._apply_item_state(state)

    def _apply_item_state(self, state: LayerState) -> None:
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
