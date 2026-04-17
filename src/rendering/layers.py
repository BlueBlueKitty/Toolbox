"""
图层状态管理。
"""

from __future__ import annotations

from collections import OrderedDict

from .models import LayerSpec, LayerState


class LayerManager:
    def __init__(self):
        self._layers: OrderedDict[str, LayerState] = OrderedDict()

    def add_layer(self, spec: LayerSpec, item=None) -> LayerState:
        state = LayerState(spec=spec, z_order=len(self._layers), item=item)
        self._layers[spec.id] = state
        self._sync_z_order()
        return state

    def remove_layer(self, layer_id: str) -> LayerState | None:
        state = self._layers.pop(layer_id, None)
        self._sync_z_order()
        return state

    def layer(self, layer_id: str) -> LayerState | None:
        return self._layers.get(layer_id)

    def layers(self) -> list[LayerState]:
        return list(self._layers.values())

    def set_item(self, layer_id: str, item) -> None:
        state = self._require(layer_id)
        state.item = item
        self._apply_item_state(state)

    def set_visible(self, layer_id: str, visible: bool) -> None:
        state = self._require(layer_id)
        state.spec.visible = bool(visible)
        self._apply_item_state(state)

    def set_opacity(self, layer_id: str, opacity: float) -> None:
        state = self._require(layer_id)
        state.spec.opacity = max(0.0, min(float(opacity), 1.0))
        self._apply_item_state(state)

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
