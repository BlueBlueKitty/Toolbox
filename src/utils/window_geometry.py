"""
窗口几何工具：用于窗口自适配屏幕和安全展开侧边栏。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtGui import QGuiApplication, QScreen
from PySide6.QtWidgets import QWidget


def _resolve_screen(window: QWidget) -> Optional[QScreen]:
    handle = window.windowHandle()
    if handle is not None and handle.screen() is not None:
        return handle.screen()
    center = window.frameGeometry().center()
    screen = QGuiApplication.screenAt(center)
    if screen is not None:
        return screen
    return QGuiApplication.primaryScreen()


def _clamp(value: int, low: int, high: int) -> int:
    if low > high:
        return low
    return max(low, min(value, high))


def fit_window_to_screen(
    window: QWidget,
    *,
    target_width: int | None = None,
    target_height: int | None = None,
    margin: int = 24,
    center: bool = True,
) -> tuple[int, int]:
    """
    将窗口限制在当前屏幕可用区域内；可选居中。
    """
    screen = _resolve_screen(window)
    if screen is None:
        width = max(320, int(target_width or window.width()))
        height = max(220, int(target_height or window.height()))
        window.resize(width, height)
        return width, height

    available = screen.availableGeometry()
    inner_margin = max(0, int(margin))
    max_width = max(320, int(available.width() - inner_margin * 2))
    max_height = max(220, int(available.height() - inner_margin * 2))

    width = int(target_width if target_width is not None else window.width())
    height = int(target_height if target_height is not None else window.height())
    width = _clamp(width, 320, max_width)
    height = _clamp(height, 220, max_height)

    window.resize(width, height)

    if center:
        new_x = available.x() + (available.width() - width) // 2
        new_y = available.y() + (available.height() - height) // 2
    else:
        x_min = available.x() + inner_margin
        y_min = available.y() + inner_margin
        x_max = available.x() + available.width() - inner_margin - width
        y_max = available.y() + available.height() - inner_margin - height
        g = window.frameGeometry()
        new_x = _clamp(g.x(), x_min, x_max)
        new_y = _clamp(g.y(), y_min, y_max)

    window.move(new_x, new_y)
    return width, height


def expand_window_width_safely(
    window: QWidget,
    extra_width: int,
    *,
    min_main_width: int = 480,
    margin: int = 24,
) -> int:
    """
    在不越界的前提下扩展窗口宽度，返回实际可分配给侧边栏的宽度。
    """
    desired_sidebar = max(0, int(extra_width))
    screen = _resolve_screen(window)
    if screen is None:
        fit_window_to_screen(window, target_width=window.width() + desired_sidebar, margin=margin, center=False)
        return desired_sidebar

    available = screen.availableGeometry()
    inner_margin = max(0, int(margin))
    max_total_width = max(320, int(available.width() - inner_margin * 2))

    base_width = int(window.width())
    main_min = max(240, int(min_main_width))
    max_sidebar_width = max(0, max_total_width - main_min)
    actual_sidebar = min(desired_sidebar, max_sidebar_width)

    main_width = base_width
    total_width = main_width + actual_sidebar
    if total_width > max_total_width:
        shrink = min(total_width - max_total_width, max(0, main_width - main_min))
        main_width -= shrink
        total_width = main_width + actual_sidebar
    if total_width > max_total_width:
        actual_sidebar = max(0, max_total_width - main_width)
        total_width = main_width + actual_sidebar

    fit_window_to_screen(
        window,
        target_width=total_width,
        target_height=window.height(),
        margin=margin,
        center=False,
    )
    return actual_sidebar
