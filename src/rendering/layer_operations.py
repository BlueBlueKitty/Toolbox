"""
可复用的图层操作辅助函数。
"""

from __future__ import annotations

from typing import Any

import numpy as np


NON_REMOVABLE_LAYER_IDS = {"base_raster", "mask", "preview_mask"}


def is_layer_removable(layer_id: str) -> bool:
    return str(layer_id) not in NON_REMOVABLE_LAYER_IDS


def ui_index_to_z_index(ui_index: int, total: int) -> int:
    """
    UI 列表顶部为最高层（QGIS风格）：
    UI index 0 -> z 最大
    """
    total = max(int(total), 1)
    ui_index = max(0, min(int(ui_index), total - 1))
    return total - 1 - ui_index


def z_index_to_ui_index(z_index: int, total: int) -> int:
    total = max(int(total), 1)
    z_index = max(0, min(int(z_index), total - 1))
    return total - 1 - z_index


def nodata_to_text(value: Any) -> str:
    if value is None:
        return "None"
    try:
        if np.isnan(value):
            return "NaN"
    except Exception:
        pass
    return str(value)


def array_minmax(values) -> tuple[float | None, float | None]:
    if values is None:
        return None, None
    arr = np.asarray(values)
    if arr.size == 0:
        return None, None
    mask = np.isfinite(arr)
    if not np.any(mask):
        return None, None
    valid = arr[mask]
    return float(np.min(valid)), float(np.max(valid))

