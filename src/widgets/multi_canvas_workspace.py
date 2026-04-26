"""
多窗口画布工作区（1/2 窗口）复用组件。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QSplitter, QVBoxLayout, QWidget

from src.rendering.sync import MultiCanvasSyncController, SyncOptions
from src.widgets.render_sidebar_widget import (
    MultiCanvasRenderBinding,
    RenderSidebarController,
    RenderSidebarWidget,
)


@runtime_checkable
class CanvasFactoryProtocol(Protocol):
    def __call__(self, window_id: str):
        ...


class MultiCanvasWorkspace(QWidget):
    active_window_changed = Signal(str)
    window_count_changed = Signal(int)

    def __init__(
        self,
        canvas_factory: CanvasFactoryProtocol,
        *,
        window_ids: list[str] | None = None,
        window_labels: dict[str, str] | None = None,
        panel_factory: Callable[[str, object], QWidget] | None = None,
        sync_options: SyncOptions | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._window_ids = list(window_ids or ["viewer_1", "viewer_2"])
        self._window_labels = dict(window_labels or {})
        self._canvas_factory = canvas_factory
        self._panel_factory = panel_factory
        self._active_window_id = self._window_ids[0]
        self._window_count = min(2, len(self._window_ids))
        self._window_canvases: dict[str, object] = {}
        self._window_widgets: dict[str, QWidget] = {}
        self._detached_dialogs: dict[str, QDialog] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.windows_splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(self.windows_splitter)

        for window_id in self._window_ids:
            canvas = self._canvas_factory(window_id)
            try:
                canvas.canvas_left_clicked.connect(lambda _wid=window_id: self.set_active_window(_wid))
            except Exception:
                pass
            panel = self._panel_factory(window_id, canvas) if self._panel_factory else canvas
            self._window_canvases[window_id] = canvas
            self._window_widgets[window_id] = panel
            self.windows_splitter.addWidget(panel)

        self.render_sidebar = RenderSidebarWidget(mode="multi_target")
        self.render_sidebar_binding = MultiCanvasRenderBinding(self._window_canvases, self._window_labels)
        self.render_sidebar_controller = RenderSidebarController(self.render_sidebar, self.render_sidebar_binding)
        self.viewport_sync_controller = MultiCanvasSyncController(
            list(self._window_canvases.values()),
            options=sync_options or SyncOptions(sync_pan=True, sync_zoom=True, sync_geographic_extent=True, sync_cursor=True),
        )
        self.render_sidebar.target_changed.connect(self.set_active_window)

        self.set_window_count(self._window_count)
        self.set_active_window(self._active_window_id)

    @property
    def window_ids(self) -> list[str]:
        return list(self._window_ids)

    def window_canvas(self, window_id: str | int):
        resolved = self._resolve_window_id(window_id)
        return self._window_canvases.get(resolved)

    def current_target_id(self) -> str:
        return self._active_window_id

    def current_canvas(self):
        return self._window_canvases.get(self._active_window_id)

    def window_count(self) -> int:
        return self._window_count

    def set_window_count(self, count: int) -> None:
        count = 1 if int(count) <= 1 else 2
        count = min(count, len(self._window_ids))
        visible_ids = self._window_ids[:count]
        for window_id, widget in self._window_widgets.items():
            if window_id in self._detached_dialogs:
                self._detached_dialogs[window_id].setVisible(window_id in visible_ids)
            else:
                widget.setVisible(window_id in visible_ids)
        self.render_sidebar_binding.set_available_targets(visible_ids)
        self._window_count = count
        if self._active_window_id not in visible_ids:
            self.set_active_window(visible_ids[0])
        if count == 1:
            self.windows_splitter.setSizes([max(1, self.width()), 0])
        else:
            self.windows_splitter.setSizes([1, 1])
        self.window_count_changed.emit(self._window_count)

    def set_active_window(self, window_id: str | int) -> None:
        resolved = self._resolve_window_id(window_id)
        if resolved not in self._window_ids:
            return
        if resolved not in self._window_ids[: self._window_count]:
            return
        self._active_window_id = resolved
        self.render_sidebar_binding.set_current_target(resolved)
        try:
            self.render_sidebar.set_current_target(resolved)
        except Exception:
            pass
        self.active_window_changed.emit(resolved)

    def set_source(self, window_id: str | int, source, *, reset_view: bool = True, refresh: bool = True, nodata_value=None) -> None:
        canvas = self.window_canvas(window_id)
        if canvas is None:
            return
        if nodata_value is None:
            canvas.set_raster_source(source, reset_view=reset_view, refresh=refresh)
        else:
            canvas.set_raster_source(source, reset_view=reset_view, refresh=refresh, nodata_value=nodata_value)

    def sync_options_dict(self) -> dict[str, bool]:
        options = self.viewport_sync_controller.group.options
        return {
            "sync_pan": bool(options.sync_pan),
            "sync_zoom": bool(options.sync_zoom),
            "sync_geographic_extent": bool(options.sync_geographic_extent),
            "sync_cursor": bool(options.sync_cursor),
            "sync_scale_bar": bool(options.sync_scale_bar),
            "sync_active_layer": bool(options.sync_active_layer),
            "sync_render_style": bool(options.sync_render_style),
        }

    def apply_sync_options(self, options: dict[str, bool] | None) -> None:
        if not options:
            return
        target = self.viewport_sync_controller.group.options
        for key, value in options.items():
            if hasattr(target, key):
                setattr(target, key, bool(value))

    def _resolve_window_id(self, window_id: str | int) -> str:
        if isinstance(window_id, int):
            if window_id <= 1:
                return self._window_ids[0]
            return self._window_ids[min(window_id - 1, len(self._window_ids) - 1)]
        return str(window_id)

    def is_window_detached(self, window_id: str | int) -> bool:
        resolved = self._resolve_window_id(window_id)
        return resolved in self._detached_dialogs

    def detach_window(self, window_id: str | int, title: str | None = None) -> None:
        resolved = self._resolve_window_id(window_id)
        if resolved not in self._window_widgets or resolved in self._detached_dialogs:
            return
        widget = self._window_widgets[resolved]
        widget.setParent(None)
        dialog = QDialog(self)
        dialog.setWindowTitle(title or self._window_labels.get(resolved, resolved))
        dialog.resize(max(900, self.width() // 2), max(700, self.height() // 2))
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(widget)
        dialog.finished.connect(lambda _result, wid=resolved: self.attach_window(wid))
        dialog.show()
        self._detached_dialogs[resolved] = dialog

    def attach_window(self, window_id: str | int) -> None:
        resolved = self._resolve_window_id(window_id)
        if resolved not in self._detached_dialogs:
            return
        dialog = self._detached_dialogs.pop(resolved)
        widget = self._window_widgets[resolved]
        widget.setParent(None)
        self.windows_splitter.addWidget(widget)
        if dialog.isVisible():
            dialog.close()
