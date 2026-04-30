"""
可复用的图层操作辅助函数。
"""

from __future__ import annotations

from typing import Any

import numpy as np


NON_REMOVABLE_LAYER_IDS = {"base_raster", "mask", "preview_mask"}


def is_layer_removable(layer_id: str) -> bool:
    """is_layer_removable。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        layer_id (str): 输入参数。
    返回:
        bool: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
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
    """z_index_to_ui_index。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        z_index (int): 输入参数。
        total (int): 输入参数。
    返回:
        int: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    total = max(int(total), 1)
    z_index = max(0, min(int(z_index), total - 1))
    return total - 1 - z_index


def nodata_to_text(value: Any) -> str:
    """nodata_to_text。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        value (Any): 输入参数。
    返回:
        str: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    if value is None:
        return "None"
    try:
        if np.isnan(value):
            return "NaN"
    except Exception:
        pass
    return str(value)


def array_minmax(values) -> tuple[float | None, float | None]:
    """array_minmax。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        values (Any): 输入参数。
    返回:
        tuple[float | None, float | None]: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
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

