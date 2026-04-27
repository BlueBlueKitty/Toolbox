"""
多窗口视口同步。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import ViewportState


@dataclass
class SyncOptions:
    sync_pan: bool = True
    sync_zoom: bool = True
    sync_geographic_extent: bool = True
    sync_cursor: bool = True
    sync_scale_bar: bool = True
    sync_active_layer: bool = False
    sync_render_style: bool = False


class ViewportSyncGroup:
    def __init__(self, options: SyncOptions | None = None):
        self.options = options or SyncOptions()
        self._canvases = []

    def add_canvas(self, canvas) -> None:
        if canvas in self._canvases:
            return
        self._canvases.append(canvas)

    def remove_canvas(self, canvas) -> None:
        if canvas in self._canvases:
            self._canvases.remove(canvas)

    def canvases(self):
        return list(self._canvases)


class MultiCanvasSyncController:
    def __init__(self, canvases=None, options: SyncOptions | None = None):
        self.group = ViewportSyncGroup(options=options)
        self._updating = False
        self._enabled = True
        for canvas in canvases or []:
            self.add_canvas(canvas)

    def add_canvas(self, canvas) -> None:
        self.group.add_canvas(canvas)
        canvas.view_transformed.connect(lambda transform, source=canvas: self._on_view_transformed(source, transform))
        canvas.cursor_changed.connect(lambda cursor, source=canvas: self._on_cursor_changed(source, cursor))
        canvas.scroll_changed.connect(lambda h, v, source=canvas: self._on_scroll_changed(source, h, v))

    def set_active_canvases(self, canvases) -> None:
        self.group._canvases = list(canvases or [])

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    def _on_view_transformed(self, source, _transform):
        if not self._enabled:
            return
        if not (self.group.options.sync_pan or self.group.options.sync_zoom or self.group.options.sync_geographic_extent):
            return
        if self._updating:
            return
        self._updating = True
        try:
            # 优先使用事件携带的精确视窗范围（x_range/y_range），避免不同窗口尺寸下的缩放偏差。
            state = _transform if isinstance(_transform, dict) else None
            if state is None and hasattr(source, "capture_view_state"):
                state = source.capture_view_state()
            if state is None and hasattr(source, "current_view_state"):
                state = source.current_view_state()
            for canvas in self.group.canvases():
                if canvas is source:
                    continue
                if state is not None and hasattr(canvas, "restore_view_state"):
                    canvas.restore_view_state(state)
                else:
                    canvas.sync_transform(_transform)
        finally:
            self._updating = False

    def _on_cursor_changed(self, source, cursor):
        if not self._enabled:
            return
        if self._updating or not self.group.options.sync_cursor:
            return
        self._updating = True
        try:
            for canvas in self.group.canvases():
                if canvas is not source:
                    canvas.sync_cursor(cursor)
        finally:
            self._updating = False

    def _on_scroll_changed(self, source, h_value, v_value):
        if not self._enabled:
            return
        if self._updating:
            return
        self._updating = True
        try:
            for canvas in self.group.canvases():
                if canvas is not source:
                    canvas.sync_scroll(h_value, v_value)
        finally:
            self._updating = False


class MultiCanvasWidget:
    def __init__(self, canvases=None, options: SyncOptions | None = None):
        self.sync_controller = MultiCanvasSyncController(canvases=canvases or [], options=options)
