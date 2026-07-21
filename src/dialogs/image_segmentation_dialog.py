"""
图像分割工具主窗口。
"""

from __future__ import annotations

import copy
import json
import os
import re
import time
from dataclasses import replace
from collections import OrderedDict
from pathlib import Path
import numpy as np

from PySide6.QtCore import QObject, QPointF, Qt, QThread, QTimer, Signal, QEvent
from PySide6.QtGui import QAction, QActionGroup, QColor, QCursor, QFont, QFontDatabase, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut, QTransform
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QSpinBox,
    QSizePolicy,
    QStyle,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from src.segmentation import (
    AddAnnotationCommand,
    AnnotationObject,
    BatchCommand,
    CommandStack,
    DeleteAnnotationCommand,
    LabelStore,
    SegmentationProject,
    SegmentationProjectManager,
    UpdateGeometryCommand,
    UpdateLabelAssignmentCommand,
    UpdateMaskPatchCommand,
)
from src.segmentation.models import DisplayState, LabelClass, PreviewSelection
from src.segmentation.algorithms import MagicWandSegmenter
from src.segmentation.exporters import (
    export_coco,
    export_mask_file,
    export_vector_file,
    export_voc,
    export_yolo,
)
from src.segmentation.geometry_service import GeometryService
from src.segmentation.mask_importer import import_mask_for_image
from src.rendering.sources import GdalRasterSource, StandardImageSource
from src.rendering.raster_source_utils import (
    SEGMENTATION_RASTER_EXTENSIONS,
    SEGMENTATION_RASTER_FILE_FILTER,
    is_segmentation_raster_file,
    open_raster_source,
)
from src.rendering.style_auto_selector import DefaultRenderStyleFactory
from src.rendering.styles import default_display_settings, legacy_config_to_style, style_to_legacy_config
from src.rendering.config import default_raster_render_config, render_raster_rgb
from src.rendering.layer_operations import is_layer_removable, nodata_to_text
from src.rendering.layer_panel_controller import LayerPanelController
from src.utils.display_pyramid import DEFAULT_PYRAMID_THRESHOLD_MB
from src.utils.window_geometry import expand_window_width_safely, fit_window_to_screen
from src.utils.image_io import (
    bounds_overlap,
    build_coordinate_transform,
    get_raster_bounds_wgs84,
    invert_geotransform,
    pixel_to_lonlat,
    pixel_to_map_coords,
    transform_point,
)
from src.dialogs.segmentation_export_dialog import SegmentationExportDialog
from src.widgets.layer_panel_widget import LayerPanelWidget
from src.widgets.label_panel_widget import LabelPanelWidget
from src.widgets.magic_wand_panel import MagicWandPanel
from src.widgets.multi_canvas_workspace import MultiCanvasWorkspace
from src.widgets.operation_progress_widget import OperationProgressWidget
from src.widgets.segmentation_canvas import SegmentationCanvas
from src.widgets.segmentation_tool_controller import SegmentationToolController


class AutosaveWorker(QObject):
    finished = Signal(bool, str)

    def __init__(self, project_manager: SegmentationProjectManager, project_snapshot: SegmentationProject, project_path: str):
        super().__init__()
        self.project_manager = project_manager
        self.project_snapshot = project_snapshot
        self.project_path = project_path

    def run(self) -> None:
        try:
            self.project_manager.save_autosave(self.project_snapshot, self.project_path)
            self.finished.emit(True, "")
        except Exception as exc:  # pragma: no cover
            self.finished.emit(False, str(exc))


class ImageSegmentationDialog(QDialog):
    def __init__(self, parent=None, pyramid_threshold_mb=DEFAULT_PYRAMID_THRESHOLD_MB):
        super().__init__(parent)
        self.setWindowTitle("图像分割工具")
        self.resize(1600, 900)

        self.project_manager = SegmentationProjectManager()
        self.project = SegmentationProject(project_version="1.0", image_asset=None)
        self.label_store = LabelStore(self.project.labels)
        self.command_stack = CommandStack(self.project)
        self.tool_controller = SegmentationToolController(self)
        self.segmenter = MagicWandSegmenter()
        self.pyramid_threshold_mb = pyramid_threshold_mb
        self.render_config = default_raster_render_config()
        self.current_source = None
        self.current_project_path: str | None = None
        self.preview_selection = None
        self._current_magic_seed = None
        self._last_magic_seed = None
        self._preview_mask = None
        self._preview_bbox = None
        self._magic_cancel_requested = False
        self._magic_preview_in_progress = False
        self._dirty = False
        self._node_edit_session_annotation_id: str | None = None
        self._node_edit_original_annotation: AnnotationObject | None = None
        self._node_edit_session_dirty = False
        self._node_edit_session_undo_depth = 0
        self._node_edit_mask_snapshot = None
        self._suspend_selection_sync = False
        self._analysis_full_rgb_cache: np.ndarray | None = None
        self._analysis_full_rgb_cache_signature = None
        self._analysis_tile_cache: dict[tuple, np.ndarray] = {}
        self._analysis_prepared_cache: OrderedDict[tuple, np.ndarray] = OrderedDict()
        self._analysis_prepared_cache_bytes = 0
        self._analysis_prepared_cache_limit_bytes = 256 * 1024 * 1024
        self._last_magic_roi_hint = None
        self._preview_vector_user_enabled = False
        self._mask_overlay_revision = 0
        self._raster_overlay_cache_key = None
        self._raster_overlay_cache_value = (None, None)
        self._last_edit_timestamp = 0.0
        self._mask_painting = False
        self._mask_paint_bbox: tuple[int, int, int, int] | None = None
        self._mask_paint_before_patch: np.ndarray | None = None
        self._last_mask_paint_point: tuple[float, float] | None = None
        self._selected_mask_components: list[dict] = []
        self._mask_selection_dash_offset = 0.0
        self._mask_selection_timer = QTimer(self)
        self._mask_selection_timer.setInterval(100)
        self._mask_selection_timer.timeout.connect(self._advance_mask_selection_animation)
        self._preview_mask_dash_offset = 0.0
        self._preview_mask_outline_timer = QTimer(self)
        self._preview_mask_outline_timer.setInterval(140)
        self._preview_mask_outline_timer.timeout.connect(self._advance_preview_mask_outline_animation)
        self._merge_preview_entries: list[dict] = []
        self._preview_undo_stack: list[dict] = []
        self._preview_redo_stack: list[dict] = []
        self._auxiliary_layers: list[dict] = []
        self._auxiliary_layer_counter = 0
        self._selected_render_layer_id: str | None = None
        self._active_window_id = "viewer_1"
        self._layout_view_states: dict[int, dict | None] = {1: None, 2: None}
        self._layer_window_visibility: dict[str, dict[str, bool]] = {}
        self._base_nodata_override = None
        # 仅记录已经成功写入项目文件的导出目录；未保存项目不复用本次导出目录。
        self._saved_export_output_dir = ""
        self._autosave_thread: QThread | None = None
        self._autosave_worker: AutosaveWorker | None = None
        self._magic_preview_timer = QTimer(self)
        self._magic_preview_timer.setInterval(250)
        self._magic_preview_timer.setSingleShot(True)
        self._magic_preview_timer.timeout.connect(self._trigger_pending_magic_preview)
        self._render_update_timer = QTimer(self)
        self._render_update_timer.setInterval(100)
        self._render_update_timer.setSingleShot(True)
        self._render_update_timer.timeout.connect(self._apply_render_settings_update)
        self._updating_view_overlays = False
        self._mask_nonzero_revision = -1
        self._mask_has_nonzero_cached = False
        self._synced_pointer_clear_timer = QTimer(self)
        self._synced_pointer_clear_timer.setSingleShot(True)
        self._synced_pointer_clear_timer.setInterval(1200)
        self._synced_pointer_clear_timer.timeout.connect(self._clear_synced_pointers)
        self._material_icon_family = self._load_material_icon_font()
        self._last_image_dir = self.project_manager.settings.value("last_image_dir", "", type=str)
        self._last_project_dir = self.project_manager.settings.value("last_project_dir", "", type=str)
        self._last_aux_dir = self.project_manager.settings.value("last_aux_dir", self._last_image_dir, type=str)
        self._last_mask_dir = self.project_manager.settings.value("last_mask_dir", self._last_image_dir, type=str)
        self._geotiff_full_render_cache_limit_mb = self.pyramid_threshold_mb

        self._create_ui()
        self._bind_signals()
        self._load_render_preferences()
        self._setup_shortcuts()
        self._autosave_interval_ms = self._load_autosave_interval_ms()

        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(self._autosave_interval_ms)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.timeout.connect(self._autosave_if_needed)

    def _create_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        self.toolbar = QToolBar()
        main_layout.addWidget(self.toolbar)

        self.open_action = QAction(self.style().standardIcon(QStyle.SP_DialogOpenButton), "打开图像", self)
        self.open_project_action = QAction(self.style().standardIcon(QStyle.SP_FileDialogContentsView), "打开项目", self)
        self.save_project_action = QAction(self.style().standardIcon(QStyle.SP_DialogSaveButton), "保存项目", self)
        self.import_aux_action = QAction(self._make_tool_icon("layers"), "导入辅助数据，可直接将辅助数据拖入图层管理窗口中", self)
        self.import_mask_action = QAction(self._make_tool_icon("grid_on"), "导入 Mask（替换当前 Mask）", self)
        self.export_action = QAction(self.style().standardIcon(QStyle.SP_DialogApplyButton), "导出...", self)
        self.undo_action = QAction(self.style().standardIcon(QStyle.SP_ArrowBack), "撤销", self)
        self.redo_action = QAction(self.style().standardIcon(QStyle.SP_ArrowForward), "重做", self)
        self.clear_annotations_action = QAction(self._make_tool_icon("delete_sweep"), "清空绘制", self)
        self.open_action.setIcon(self._make_tool_icon("image"))
        self.open_project_action.setIcon(self._make_tool_icon("folder_open"))
        self.save_project_action.setIcon(self._make_tool_icon("save"))
        self.import_aux_action.setIcon(self._make_tool_icon("layers"))
        self.import_mask_action.setIcon(self._make_tool_icon("grid_on"))
        self.export_action.setIcon(self._make_tool_icon("ios_share"))
        self.undo_action.setIcon(self._make_tool_icon("undo"))
        self.redo_action.setIcon(self._make_tool_icon("redo"))
        self.actual_size_action = QAction(self._make_tool_icon("fit_screen"), "缩放到全图", self)
        self.toggle_window_count_action = QAction(self._make_tool_icon("splitscreen", 90), "单窗/双窗切换", self)
        self.toggle_detach_window2_action = QAction(self._make_tool_icon("open_in_new"), "窗口2独立窗口", self)
        self.toggle_sidebar_action = QAction(self._make_tool_icon("tune"), "渲染控制侧边栏", self)
        self.toggle_sidebar_action.setToolTip("显示或隐藏渲染控制侧边栏")
        for action in [
            self.open_action,
            self.open_project_action,
            self.save_project_action,
            self.import_aux_action,
            self.import_mask_action,
            self.export_action,
            self.undo_action,
            self.redo_action,
            self.clear_annotations_action,
            self.actual_size_action,
        ]:
            action.setToolTip(action.text())
            action.setStatusTip(action.text())
            self.toolbar.addAction(action)
        self.toolbar.setToolTipDuration(5000)

        self.toolbar.addSeparator()
        self.tool_action_group = QActionGroup(self)
        self.tool_action_group.setExclusive(True)
        self.browse_tool_action = self._create_tool_action("浏览", SegmentationToolController.TOOL_BROWSE, self._make_tool_icon("pan_tool"))
        self.rectangle_tool_action = self._create_tool_action("矩形框", SegmentationToolController.TOOL_RECTANGLE, self._make_tool_icon("crop_square"))
        self.polygon_tool_action = self._create_tool_action("多边形", SegmentationToolController.TOOL_POLYGON, self._make_tool_icon("gesture"))
        self.magic_tool_action = self._create_tool_action("魔法棒", SegmentationToolController.TOOL_MAGIC_WAND, self._make_tool_icon("auto_fix_high", -90))
        self.brush_tool_action = self._create_tool_action("笔刷", SegmentationToolController.TOOL_BRUSH, self._make_tool_icon("brush", 90))
        self.eraser_tool_action = self._create_tool_action("橡皮擦", SegmentationToolController.TOOL_ERASER, self._make_tool_icon("ink_eraser", 0))
        self.browse_tool_action.setChecked(True)
        for action in [
            self.browse_tool_action,
            self.rectangle_tool_action,
            self.polygon_tool_action,
            self.magic_tool_action,
            self.brush_tool_action,
            self.eraser_tool_action,
        ]:
            self.tool_action_group.addAction(action)
            self.toolbar.addAction(action)
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.toggle_window_count_action)
        self.toolbar.addAction(self.toggle_detach_window2_action)
        self.toolbar.addSeparator()
        self._toolbar_spacer = QWidget(self)
        self._toolbar_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.toolbar.addWidget(self._toolbar_spacer)
        self.toolbar.addAction(self.toggle_sidebar_action)

        splitter = QSplitter(Qt.Horizontal)
        # 图像工作区是主内容，最大化时应优先获得额外的垂直空间。
        main_layout.addWidget(splitter, 1)

        self.workspace = MultiCanvasWorkspace(
            canvas_factory=lambda _wid: SegmentationCanvas(),
            window_ids=["viewer_1", "viewer_2"],
            window_labels={"viewer_1": "窗口1", "viewer_2": "窗口2"},
            render_binding_layer_mode="active",
            pointer_sync=True,
        )
        self.canvas = self.workspace.window_canvas("viewer_1")
        for window_id in self.workspace.window_ids:
            window_canvas = self.workspace.window_canvas(window_id)
            if window_canvas is None:
                continue
            window_canvas.set_tool_icons({
                SegmentationToolController.TOOL_MAGIC_WAND: self._make_tool_icon("auto_fix_high"),
                SegmentationToolController.TOOL_BRUSH: self._make_tool_icon("brush"),
                SegmentationToolController.TOOL_ERASER: self._make_tool_icon("ink_eraser"),
            })
            window_canvas.files_dropped.connect(self._on_canvas_files_dropped)
            window_canvas.set_tool_color(self._active_label_color())
        splitter.addWidget(self.workspace)

        right_splitter = QSplitter(Qt.Horizontal)
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 0, 4, 0)
        right_layout.setSpacing(0)
        self.label_panel = LabelPanelWidget()
        self.layer_panel = LayerPanelWidget()
        self.layer_controller = LayerPanelController(
            self.layer_panel,
            self.canvas,
            exclude_layer_ids={"draft", "snap", "preview_vector", "annotations"},
        )
        self.magic_panel = MagicWandPanel()
        self.render_sidebar = self.workspace.render_sidebar
        self.render_settings = self.render_sidebar.render_settings
        self.colormap_combo = self.render_sidebar.colormap_combo
        self._remove_hillshade_mode()
        self.render_settings.set_smooth_display(False)
        self.render_sidebar_binding = self.workspace.render_sidebar_binding
        self.render_sidebar_controller = self.workspace.render_sidebar_controller
        self.render_sidebar.mode = "simple"
        self.render_sidebar.target_combo.setVisible(False)
        renderer_layout = self.render_sidebar.renderer_group.layout()
        if renderer_layout is not None and hasattr(renderer_layout, "labelForField"):
            target_label = renderer_layout.labelForField(self.render_sidebar.target_combo)
            if target_label is not None:
                target_label.setVisible(False)
        self.label_panel.setMinimumHeight(190)
        self.layer_panel.setMinimumHeight(150)
        self.layer_panel.setMaximumHeight(220)
        self._rebuild_layer_panel_items()
        right_layout.addWidget(self.label_panel, 1)
        right_layout.addWidget(self.layer_panel, 0)
        right_layout.addWidget(self.magic_panel, 0)
        right_splitter.addWidget(right_panel)
        right_splitter.addWidget(self.render_sidebar)
        right_splitter.setStretchFactor(0, 2)
        right_splitter.setStretchFactor(1, 1)
        right_splitter.setSizes([740, 0])
        self.right_splitter = right_splitter
        self._sidebar_visible = False
        self._sidebar_base_width = self.width()
        self.render_sidebar.setVisible(False)
        # 分割工具默认单窗口启动，避免上次状态残留造成初始活动窗口异常。
        self.workspace.set_window_count(1)
        self.workspace.set_active_window("viewer_1")
        self._active_window_id = self.workspace.current_target_id()
        self.canvas = self.workspace.window_canvas(self._active_window_id) or self.workspace.window_canvas("viewer_1")
        self.layer_controller.set_canvas(self.canvas)
        self._rebuild_layer_panel_items()
        splitter.addWidget(right_splitter)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        bottom_layout = QHBoxLayout()
        self.mouse_pos_label = QLabel("行: -, 列: - | 渲染RGB: - | 原值: - | Mask 标签: 无标签 | Mask 值: -")
        self.status_label = QLabel("未打开图像")
        bottom_layout.addWidget(self.mouse_pos_label)
        bottom_layout.addStretch(1)
        bottom_layout.addWidget(self.status_label)
        main_layout.addLayout(bottom_layout)
        self.operation_progress = OperationProgressWidget()
        main_layout.addWidget(self.operation_progress, 0)

    def _rebuild_layer_panel_items(self) -> None:
        self.layer_controller.rebuild_panel_items()
        for layer_id in self.layer_panel.layer_order():
            self.layer_panel.set_window_visibility(layer_id, "viewer_1", self._layer_visible_in_window(layer_id, "viewer_1"))
            self.layer_panel.set_window_visibility(layer_id, "viewer_2", self._layer_visible_in_window(layer_id, "viewer_2"))

    def _all_canvases(self) -> list[SegmentationCanvas]:
        canvases: list[SegmentationCanvas] = []
        for window_id in self.workspace.window_ids:
            canvas = self.workspace.window_canvas(window_id)
            if canvas is not None:
                canvases.append(canvas)
        return canvases

    def _canvas_for_window(self, window_id: str):
        return self.workspace.window_canvas(window_id)

    def _layer_visible_in_window(self, layer_id: str, window_id: str) -> bool:
        return bool(self._layer_window_visibility.get(layer_id, {}).get(window_id, True))

    def _set_layer_visible_for_window(self, layer_id: str, window_id: str, visible: bool) -> None:
        self._layer_window_visibility.setdefault(layer_id, {})[window_id] = bool(visible)
        canvas = self._canvas_for_window(window_id)
        if canvas is None:
            return
        if canvas.layer_manager.layer(layer_id):
            canvas.set_layer_visible(layer_id, bool(visible))

    def _on_layer_window_visibility_changed(self, layer_id: str, window_id: str, visible: bool) -> None:
        global_visible = bool(self.project.layer_visibility.get(layer_id, True))
        self._set_layer_visible_for_window(layer_id, window_id, bool(visible and global_visible))
        self._set_dirty(True)

    def _bind_canvas_signals(self, canvas) -> None:
        canvas.mouse_pressed.connect(self._handle_mouse_press)
        canvas.mouse_moved.connect(self._update_mouse_position)
        canvas.mouse_moved.connect(self._handle_mouse_move)
        canvas.mouse_moved.connect(self.tool_controller.handle_move)
        canvas.mouse_released.connect(self._handle_mouse_release)
        canvas.view_transformed.connect(self._on_canvas_view_transformed)
        # Mask 图层按当前底图渲染窗口裁剪；视图刷新后必须同步更新该裁剪窗口。
        canvas.view_state_changed.connect(self._on_view_state_changed)
        canvas.tool_wheel_adjust_requested.connect(self._adjust_active_tool_slider)

    def _unbind_canvas_signals(self, canvas) -> None:
        for signal, slot in [
            (canvas.mouse_pressed, self._handle_mouse_press),
            (canvas.mouse_moved, self._update_mouse_position),
            (canvas.mouse_moved, self._handle_mouse_move),
            (canvas.mouse_moved, self.tool_controller.handle_move),
            (canvas.mouse_released, self._handle_mouse_release),
            (canvas.view_transformed, self._on_canvas_view_transformed),
            (canvas.view_state_changed, self._on_view_state_changed),
            (canvas.tool_wheel_adjust_requested, self._adjust_active_tool_slider),
        ]:
            try:
                signal.disconnect(slot)
            except Exception:
                pass

    def _on_canvas_view_transformed(self, _state) -> None:
        if self.project.image_asset is None:
            return
        try:
            self.tool_controller.set_view_state(self.canvas.current_view_state())
        except Exception:
            pass

    def _on_workspace_active_window_changed(self, window_id: str) -> None:
        self._set_active_window(window_id)

    def _set_active_window(self, window_id: str) -> None:
        if window_id == self._active_window_id:
            return
        old_canvas = self.canvas
        new_canvas = self.workspace.window_canvas(window_id)
        if new_canvas is None:
            return
        self._unbind_canvas_signals(old_canvas)
        self.canvas = new_canvas
        self._bind_canvas_signals(self.canvas)
        self.layer_controller.set_canvas(self.canvas)
        self._active_window_id = window_id
        self.workspace.set_active_window(window_id)
        self.project_manager.settings.setValue("workspace/active_window", window_id)
        # 新活动窗口需要立即同步当前工具对应的自定义光标状态。
        self.canvas.set_interaction_mode(self.tool_controller.active_tool)
        self.canvas.set_tool_color(self._active_label_color())
        self.canvas.set_brush_radius(max(0.2, float(self.magic_panel.brush_size())))
        if self.current_source is not None:
            self.canvas.set_render_config(self.render_config)
            self._refresh_canvas()
        self._rebuild_layer_panel_items()
        if hasattr(self, "render_sidebar_controller"):
            self.render_sidebar_controller.refresh()

    def _toggle_window_count(self) -> None:
        viewer1 = self._canvas_for_window("viewer_1")
        active_canvas = self.canvas
        preserved_state = None
        current_count = self.workspace.window_count()
        if viewer1 is not None:
            preserved_state = viewer1.capture_view_state()
        elif active_canvas is not None:
            preserved_state = active_canvas.capture_view_state()
        if preserved_state is not None:
            self._layout_view_states[int(current_count)] = dict(preserved_state)
        target_count = 1 if self.workspace.window_count() == 2 else 2
        target_state = self._layout_view_states.get(int(target_count))
        if (
            int(target_count) >= 2
            and isinstance(target_state, dict)
            and "x_range" in target_state
            and "y_range" in target_state
        ):
            preserved_state = dict(target_state)
        elif preserved_state is not None:
            preserved_state = dict(preserved_state)
            preserved_state["_preserve_axis"] = "y"
        self.workspace.windows_splitter.setUpdatesEnabled(False)
        self.workspace.set_window_count(target_count)
        self.workspace.windows_splitter.setUpdatesEnabled(True)
        if target_count == 1:
            self._set_active_window("viewer_1")
        else:
            self._ensure_window2_ready()
        if preserved_state is not None:
            self._restore_workspace_view_state_after_toggle(preserved_state, target_count)
        self.project_manager.settings.setValue("workspace/window_count", int(target_count))

    def _toggle_detach_window2(self) -> None:
        if self.workspace.is_window_detached("viewer_2"):
            self.workspace.attach_window("viewer_2")
        else:
            if self.workspace.window_count() < 2:
                self.workspace.set_window_count(2)
                self.project_manager.settings.setValue("workspace/window_count", 2)
                self._ensure_window2_ready()
            self.workspace.detach_window("viewer_2", title="图像分割工具 - 窗口2")

    def _on_workspace_window_detached_changed(self, window_id: str, detached: bool) -> None:
        if window_id != "viewer_2":
            return
        self.toggle_detach_window2_action.setText("窗口2回嵌" if detached else "窗口2独立窗口")
        if not detached and self.workspace.window_count() >= 2:
            self._ensure_window2_ready()

    def _toggle_sidebar(self) -> None:
        if not hasattr(self, "render_sidebar") or not hasattr(self, "right_splitter"):
            return
        if self._sidebar_visible:
            self.render_sidebar.setVisible(False)
            self.right_splitter.setSizes([self.right_splitter.width(), 0])
            base_width = getattr(self, "_sidebar_base_width", 0)
            if base_width > 0:
                self.resize(base_width, self.height())
            fit_window_to_screen(self, margin=24, center=False)
            self._sidebar_visible = False
            return
        sidebar_width = max(180, min(240, int(self.render_sidebar.sizeHint().width())))
        self._sidebar_base_width = self.width()
        applied_sidebar_width = expand_window_width_safely(
            self,
            sidebar_width,
            min_main_width=560,
            margin=24,
        )
        self.render_sidebar.setVisible(True)
        self.right_splitter.setSizes([max(1, self.width() - applied_sidebar_width), max(1, applied_sidebar_width)])
        self._sidebar_visible = True

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, lambda: fit_window_to_screen(self, margin=24, center=True))

    def _bind_signals(self) -> None:
        self.open_action.triggered.connect(self.open_image)
        self.open_project_action.triggered.connect(self.open_project)
        self.save_project_action.triggered.connect(self.save_project)
        self.import_aux_action.triggered.connect(self.import_auxiliary_data)
        self.import_mask_action.triggered.connect(self.import_mask)
        self.export_action.triggered.connect(self.export_data)
        self.undo_action.triggered.connect(self.undo)
        self.redo_action.triggered.connect(self.redo)
        self.clear_annotations_action.triggered.connect(self.clear_all_annotations)
        self.actual_size_action.triggered.connect(lambda: self.canvas.fit_image())
        self.toggle_window_count_action.triggered.connect(self._toggle_window_count)
        self.toggle_detach_window2_action.triggered.connect(self._toggle_detach_window2)
        self.toggle_sidebar_action.triggered.connect(self._toggle_sidebar)
        self.tool_action_group.triggered.connect(self._on_tool_action_triggered)
        self.workspace.active_window_changed.connect(self._on_workspace_active_window_changed)
        self.workspace.window_detached_changed.connect(self._on_workspace_window_detached_changed)
        self.render_sidebar.target_changed.connect(self._on_workspace_active_window_changed)
        for canvas in self._all_canvases():
            canvas.layer_manager.layer_style_changed.connect(self._on_canvas_layer_rendering_changed)
            canvas.layer_manager.layer_display_changed.connect(self._on_canvas_layer_rendering_changed)

        self._bind_canvas_signals(self.canvas)

        self.tool_controller.polygon_finished.connect(self._add_polygon_annotation)
        self.tool_controller.rectangle_finished.connect(self._add_rectangle_annotation)
        self.tool_controller.magic_wand_requested.connect(self._run_magic_wand_preview)
        self.tool_controller.selection_changed.connect(self._on_selection_changed)
        self.tool_controller.geometry_changed.connect(self._on_geometry_changed)
        self.tool_controller.geometry_committed.connect(self._on_geometry_committed)
        self.tool_controller.draft_changed.connect(self._on_draft_changed)
        self.tool_controller.snap_indicator_changed.connect(self._on_snap_indicator_changed)
        self.tool_controller.message_requested.connect(self._show_tool_message)

        self.label_panel.active_label_changed.connect(self._set_active_label)
        self.label_panel.label_value_changed.connect(self._on_label_value_changed)
        self.label_panel.label_deleted.connect(self._on_label_deleted)
        self.label_panel.labels_changed.connect(self._replace_labels)
        self.layer_controller.on_layer_visibility = self._layer_visibility_callback
        self.layer_controller.on_layer_order = self._layer_order_callback
        self.layer_controller.on_layer_opacity = self._layer_opacity_callback
        self.layer_controller.on_layer_blend_mode = self._layer_blend_mode_callback
        self.layer_controller.on_layer_selected = self._on_layer_selected
        self.layer_controller.on_layer_remove = self._remove_layer
        self.layer_controller.on_layer_nodata = self._on_layer_nodata_changed
        self.layer_controller.on_layer_style = self._edit_layer_style
        self.layer_controller.on_layer_property = self._show_layer_properties
        self.layer_controller.on_zoom_bbox = self._layer_bbox
        self.layer_controller.after_change = self._on_layer_controller_changed
        self.layer_controller.bind()
        self.layer_panel.window_visibility_changed.connect(self._on_layer_window_visibility_changed)
        self.layer_panel.files_dropped.connect(self._on_layer_panel_files_dropped)
        self.magic_panel.params_changed.connect(self._schedule_magic_preview)
        self.magic_panel.params_changed.connect(lambda *_: self._sync_magic_panel_state_to_project(mark_dirty=True))
        self.magic_panel.merge_preview_changed.connect(self._on_merge_preview_changed)
        self.magic_panel.show_new_region_only_changed.connect(lambda *_: self._update_preview_display())
        self.magic_panel.show_new_region_only_changed.connect(lambda *_: self._sync_magic_panel_state_to_project(mark_dirty=True))
        self.magic_panel.slider_config_changed.connect(self._on_magic_slider_config_changed)
        self.magic_panel.brush_size_changed.connect(self._on_brush_size_changed)
        self.magic_panel.brush_size_changed.connect(lambda *_: self._sync_magic_panel_state_to_project(mark_dirty=True))
        self.magic_panel.confirm_requested.connect(self._confirm_magic_preview)
        self.magic_panel.cancel_requested.connect(self._clear_magic_preview)
        self.render_settings.settings_changed.connect(self.on_render_settings_changed)
        self.render_settings.suggest_colormap.connect(self.on_suggest_colormap)
        self.colormap_combo.currentTextChanged.connect(self.on_colormap_changed)
    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self.redo)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save_project)
        QShortcut(QKeySequence("Ctrl+C"), self, activated=self._cancel_magic_recognition)
        QShortcut(QKeySequence("Ctrl+A"), self, activated=self._select_all_annotations)
        QShortcut(QKeySequence(Qt.Key_Delete), self, activated=self.delete_selected)
        QShortcut(QKeySequence(Qt.Key_Backspace), self, activated=self._backspace_action)
        QShortcut(QKeySequence(Qt.Key_Return), self, activated=self._enter_action)
        QShortcut(QKeySequence(Qt.Key_Enter), self, activated=self._enter_action)
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self._escape_action)
        for idx in range(1, 10):
            QShortcut(QKeySequence(str(idx)), self, activated=lambda value=idx: self._activate_label_shortcut(str(value)))

    def changeEvent(self, event) -> None:
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange):
            self._refresh_toolbar_icons()
            self.magic_panel.refresh_icons()
        super().changeEvent(event)

    def on_theme_mode_changed(self, _mode: str) -> None:
        self._refresh_toolbar_icons()
        self.magic_panel.refresh_icons()

    def _refresh_toolbar_icons(self) -> None:
        if not hasattr(self, "open_action"):
            return
        self.open_action.setIcon(self._make_tool_icon("image"))
        self.open_project_action.setIcon(self._make_tool_icon("folder_open"))
        self.save_project_action.setIcon(self._make_tool_icon("save"))
        self.import_aux_action.setIcon(self._make_tool_icon("layers"))
        self.import_mask_action.setIcon(self._make_tool_icon("grid_on"))
        self.export_action.setIcon(self._make_tool_icon("ios_share"))
        self.undo_action.setIcon(self._make_tool_icon("undo"))
        self.redo_action.setIcon(self._make_tool_icon("redo"))
        self.clear_annotations_action.setIcon(self._make_tool_icon("delete_sweep"))
        self.actual_size_action.setIcon(self._make_tool_icon("fit_screen"))
        self.toggle_window_count_action.setIcon(self._make_tool_icon("splitscreen", 90))
        self.toggle_detach_window2_action.setIcon(self._make_tool_icon("open_in_new"))
        self.toggle_sidebar_action.setIcon(self._make_tool_icon("tune"))
        self.browse_tool_action.setIcon(self._make_tool_icon("pan_tool"))
        self.rectangle_tool_action.setIcon(self._make_tool_icon("crop_square"))
        self.polygon_tool_action.setIcon(self._make_tool_icon("gesture"))
        self.magic_tool_action.setIcon(self._make_tool_icon("auto_fix_high", -90))
        self.brush_tool_action.setIcon(self._make_tool_icon("brush", 90))
        self.eraser_tool_action.setIcon(self._make_tool_icon("ink_eraser", 0))
        for canvas in self._all_canvases():
            canvas.set_tool_icons({
                SegmentationToolController.TOOL_MAGIC_WAND: self._make_tool_icon("auto_fix_high"),
                SegmentationToolController.TOOL_BRUSH: self._make_tool_icon("brush"),
                SegmentationToolController.TOOL_ERASER: self._make_tool_icon("ink_eraser"),
            })

    def _on_magic_slider_config_changed(self, _key: str, configs: dict) -> None:
        state = self.project.magic_panel_settings if isinstance(self.project.magic_panel_settings, dict) else {}
        state = dict(state)
        state["slider_configs"] = dict(configs or {})
        self.project.magic_panel_settings = state
        self._set_dirty(True)

    def _sync_magic_panel_state_to_project(self, mark_dirty: bool = False) -> None:
        self.project.magic_panel_settings = self.magic_panel.get_panel_state()
        if mark_dirty:
            self._set_dirty(True)

    def _cancel_magic_recognition(self) -> None:
        if self.tool_controller.active_tool != SegmentationToolController.TOOL_MAGIC_WAND:
            return
        if not (self._magic_preview_timer.isActive() or self._magic_preview_in_progress):
            return
        self._magic_cancel_requested = True
        self._current_magic_seed = None
        self._magic_preview_timer.stop()
        self._finish_progress("魔法棒识别已取消")
        self.status_label.setText("已取消魔法棒识别")

    def _on_canvas_files_dropped(self, paths: list[str]) -> None:
        if not paths:
            return
        file_path = next((item for item in paths if os.path.isfile(item)), None)
        if file_path is None:
            QMessageBox.warning(self, "拖拽打开失败", "请拖入图像文件。")
            return
        if not is_segmentation_raster_file(file_path):
            QMessageBox.warning(
                self,
                "拖拽打开失败",
                "图像分割工具支持 TIF/TIFF、GRD、PNG、JPG/JPEG、BMP 图像。",
            )
            return
        self.open_image(file_path)

    def _activate_label_shortcut(self, shortcut: str) -> None:
        for label in self.project.labels:
            if label.shortcut == shortcut:
                self._apply_label_choice(label.id)
                break

    def _create_tool_action(self, text: str, tool_name: str, icon: QIcon) -> QAction:
        action = QAction(icon, text, self)
        action.setCheckable(True)
        action.setData(tool_name)
        action.setToolTip(text)
        action.setStatusTip(text)
        return action

    def _make_tool_icon(self, icon_name: str, rotation_angle: float = 0) -> QIcon:
        if icon_name == "ink_eraser":
            pixmap = QPixmap(20, 20)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing, True)
            color = self.palette().color(self.foregroundRole())
            painter.setPen(QPen(color, 2))
            painter.setBrush(Qt.NoBrush)
            path = QPainterPath()
            path.moveTo(5, 13)
            path.lineTo(12, 6)
            path.lineTo(16, 10)
            path.lineTo(9, 17)
            path.lineTo(5, 13)
            painter.drawPath(path)
            painter.drawLine(8, 16, 17, 16)
            painter.end()
            return QIcon(self._rotated_icon_pixmap(pixmap, rotation_angle))
        if self._material_icon_family:
            pixmap = QPixmap(20, 20)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing, True)
            font = QFont(self._material_icon_family)
            font.setPixelSize(20)
            painter.setFont(font)
            painter.setPen(self.palette().color(self.foregroundRole()))
            painter.drawText(pixmap.rect(), Qt.AlignCenter, icon_name)
            painter.end()
            return QIcon(self._rotated_icon_pixmap(pixmap, rotation_angle))
        pixmap = QPixmap(18, 18)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor("#1f2937"), 2))
        if icon_name == "pan_tool":
            painter.drawLine(9, 2, 9, 16)
            painter.drawLine(2, 9, 16, 9)
        elif icon_name == "crop_square":
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(3, 4, 12, 10)
        else:
            painter.drawLine(9, 2, 9, 16)
            painter.drawLine(2, 9, 16, 9)
            painter.setPen(QPen(QColor("#f59f00"), 2))
            painter.drawEllipse(QPointF(9, 9), 5, 5)
        painter.end()
        return QIcon(self._rotated_icon_pixmap(pixmap, rotation_angle))

    def _rotated_icon_pixmap(self, pixmap: QPixmap, angle: float) -> QPixmap:
        if not angle:
            return pixmap
        return pixmap.transformed(QTransform().rotate(angle), Qt.SmoothTransformation)

    def _load_material_icon_font(self) -> str | None:
        font_path = Path(__file__).resolve().parents[2] / "resources" / "fonts" / "MaterialIcons-Regular.ttf"
        if not font_path.exists():
            return None
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id < 0:
            return None
        families = QFontDatabase.applicationFontFamilies(font_id)
        return families[0] if families else None

    def _set_dirty(self, value: bool = True) -> None:
        self._dirty = value
        if value:
            self._last_edit_timestamp = time.monotonic()
            self.autosave_timer.setInterval(self._autosave_interval_ms)
            self.autosave_timer.start()
        else:
            self.autosave_timer.stop()

    def _load_autosave_interval_ms(self) -> int:
        seconds = self.project_manager.settings.value("autosave/interval_seconds", 60, type=int)
        seconds = max(5, int(seconds))
        return seconds * 1000

    def _remove_hillshade_mode(self) -> None:
        combo = self.render_settings.display_mode_combo
        for index in range(combo.count() - 1, -1, -1):
            if combo.itemText(index) == "晕渲地貌":
                combo.removeItem(index)
        if combo.currentText() == "晕渲地貌":
            combo.setCurrentText("灰度")

    def _show_tool_message(self, text: str) -> None:
        QMessageBox.information(self, "提示", text)

    def _load_render_preferences(self) -> None:
        settings = self.project_manager.settings
        self._set_display_mode(settings.value("render/display_mode", "灰度", type=str))
        self.render_settings.set_stretch_mode(settings.value("render/stretch_mode", self.render_settings.STRETCH_MIN_MAX, type=str))
        self.render_settings.reverse_check.setChecked(settings.value("render/reversed", False, type=bool))
        self.render_settings.auto_range_check.setChecked(settings.value("render/auto_range", True, type=bool))
        self.render_settings.set_value_range(
            settings.value("render/value_min", 0.0, type=float),
            settings.value("render/value_max", 1.0, type=float),
        )
        self.render_settings.set_gamma(settings.value("render/gamma", 1.0, type=float))
        self.render_settings.gray_band_spin.setValue(settings.value("render/gray_band", 1, type=int))
        self.render_settings.band_r_spin.setValue(settings.value("render/band_r", 1, type=int))
        self.render_settings.band_g_spin.setValue(settings.value("render/band_g", 2, type=int))
        self.render_settings.band_b_spin.setValue(settings.value("render/band_b", 3, type=int))
        self.render_settings.percent_low_spin.setValue(settings.value("render/percent_low", 2.0, type=float))
        self.render_settings.percent_high_spin.setValue(settings.value("render/percent_high", 98.0, type=float))
        self.render_settings.std_dev_spin.setValue(settings.value("render/std_dev_n", 2.0, type=float))
        self.colormap_combo.setCurrentText(settings.value("render/colormap", "gray", type=str))
        sync_options_raw = settings.value("workspace/sync_options", "", type=str)
        if isinstance(sync_options_raw, str) and sync_options_raw.strip():
            try:
                parsed = json.loads(sync_options_raw)
                if isinstance(parsed, dict):
                    self.workspace.apply_sync_options(parsed)
            except Exception:
                pass
        # 分割工具默认开启双窗联动，避免历史配置关闭后看起来“无法同步”。
        self.workspace.apply_sync_options(
            {
                "sync_pan": True,
                "sync_zoom": True,
                "sync_geographic_extent": True,
                "sync_cursor": True,
            }
        )
        self.on_render_settings_changed()

    def _save_render_preferences(self) -> None:
        settings = self.project_manager.settings
        current = self.render_settings.get_all_settings()
        settings.setValue("render/display_mode", current["display_mode"])
        settings.setValue("render/stretch_mode", current["stretch_mode"])
        settings.setValue("render/reversed", current["colormap_reversed"])
        settings.setValue("render/auto_range", current["auto_range"])
        settings.setValue("render/value_min", current["value_min"])
        settings.setValue("render/value_max", current["value_max"])
        settings.setValue("render/gamma", current["gamma"])
        settings.setValue("render/gray_band", current["gray_band"])
        settings.setValue("render/band_r", current["rgb_bands"][0])
        settings.setValue("render/band_g", current["rgb_bands"][1])
        settings.setValue("render/band_b", current["rgb_bands"][2])
        settings.setValue("render/percent_low", current["percent_clip"][0])
        settings.setValue("render/percent_high", current["percent_clip"][1])
        settings.setValue("render/std_dev_n", current["std_dev_n"])
        settings.setValue("render/colormap", self.colormap_combo.currentText())
        settings.setValue("workspace/window_count", int(self.workspace.window_count()))
        settings.setValue("workspace/active_window", self._active_window_id)
        settings.setValue("workspace/sync_options", json.dumps(self.workspace.sync_options_dict(), ensure_ascii=False))

    def _analysis_cache_signature(self):
        return (
            self.render_config.display_mode,
            self.render_config.gray_band,
            tuple(self.render_config.rgb_bands),
            self.render_config.gamma,
            self.render_config.stretch_mode,
            tuple(self.render_config.percent_clip),
            self.render_config.std_dev_n,
            self.render_config.auto_range,
            tuple(self.render_config.value_range),
            tuple(self.render_config.global_value_range) if self.render_config.global_value_range else None,
            self.render_config.colormap_name,
            self.render_config.colormap_reversed,
        )

    def _clear_analysis_cache(self) -> None:
        self._analysis_full_rgb_cache = None
        self._analysis_full_rgb_cache_signature = None
        self._analysis_tile_cache.clear()
        self._analysis_prepared_cache.clear()
        self._analysis_prepared_cache_bytes = 0
        self._last_magic_roi_hint = None

    def _prepared_analysis_key(self, x0: int, y0: int, width: int, height: int, params) -> tuple:
        return (
            self._analysis_cache_signature(),
            params.similarity_mode,
            int(x0),
            int(y0),
            int(width),
            int(height),
        )

    def _prepare_magic_analysis_image(
        self,
        rgb_image: np.ndarray,
        x0: int,
        y0: int,
        width: int,
        height: int,
        params,
    ) -> np.ndarray:
        key = self._prepared_analysis_key(x0, y0, width, height, params)
        cached = self._analysis_prepared_cache.get(key)
        if cached is not None:
            self._analysis_prepared_cache.move_to_end(key)
            return cached
        prepared = self.segmenter.prepare_image(rgb_image, params)
        self._cache_prepared_analysis_image(key, prepared)
        return prepared

    def _cache_prepared_analysis_image(self, key: tuple, prepared: np.ndarray) -> None:
        prepared_size = int(getattr(prepared, "nbytes", 0))
        if prepared_size > self._analysis_prepared_cache_limit_bytes:
            return
        self._analysis_prepared_cache[key] = prepared
        self._analysis_prepared_cache_bytes += prepared_size
        while (
            self._analysis_prepared_cache
            and self._analysis_prepared_cache_bytes > self._analysis_prepared_cache_limit_bytes
        ):
            _old_key, old_value = self._analysis_prepared_cache.popitem(last=False)
            self._analysis_prepared_cache_bytes -= int(getattr(old_value, "nbytes", 0))

    def _magic_roi_hint_key(self, x: int, y: int, params) -> tuple:
        return (
            self._analysis_cache_signature(),
            params.similarity_mode,
            int(params.connectivity),
            int(x),
            int(y),
        )

    def _initial_magic_roi_radius(self, x: int, y: int, params) -> int:
        key = self._magic_roi_hint_key(x, y, params)
        if self._last_magic_roi_hint and self._last_magic_roi_hint[0] == key:
            return int(self._last_magic_roi_hint[1])
        return self._viewport_magic_roi_radius()

    def _viewport_magic_roi_radius(self) -> int:
        if self.project.image_asset is None:
            return 512
        try:
            request = self.canvas.current_render_request()
            visible_side = max(float(request.width), float(request.height))
        except Exception:
            visible_side = 1024.0
        tile_size = self._analysis_tile_size()
        radius = int(np.ceil(max(visible_side / 2.0, tile_size) / tile_size) * tile_size)
        return max(tile_size, min(radius, self._analysis_max_roi_side()))

    def _remember_magic_roi_radius(self, x: int, y: int, params, radius: int) -> None:
        self._last_magic_roi_hint = (self._magic_roi_hint_key(x, y, params), int(radius))

    def _geo_should_cache_full_render(self) -> bool:
        if self.project.image_asset is None or self.current_source is None:
            return False
        if not isinstance(self.current_source, GdalRasterSource):
            return False
        try:
            file_size_mb = Path(self.project.image_asset.path).stat().st_size / (1024 * 1024)
        except Exception:
            return False
        return file_size_mb < max(1, self._geotiff_full_render_cache_limit_mb)

    def _ensure_full_analysis_rgb(self) -> np.ndarray | None:
        if self.current_source is None or self.project.image_asset is None:
            return None
        signature = self._analysis_cache_signature()
        if self._analysis_full_rgb_cache is not None and self._analysis_full_rgb_cache_signature == signature:
            return self._analysis_full_rgb_cache
        if isinstance(self.current_source, StandardImageSource) or self._geo_should_cache_full_render():
            raw = self.current_source.read_window_native(
                0,
                0,
                self.project.image_asset.width,
                self.project.image_asset.height,
            )
            self._analysis_full_rgb_cache = render_raster_rgb(
                raw,
                self.render_config,
                nodata_value=self.project.image_asset.nodata,
            )
            self._analysis_full_rgb_cache_signature = signature
            return self._analysis_full_rgb_cache
        return None

    def _analysis_tile_size(self) -> int:
        return 512

    def _analysis_max_roi_side(self) -> int:
        if self.project.image_asset is None:
            return 4096
        max_dim = max(self.project.image_asset.width, self.project.image_asset.height)
        return min(max_dim, max(4096, min(16384, 1 << int(np.ceil(np.log2(max(2048, min(max_dim, 16384))))))))

    def _fetch_rendered_tile(self, tile_x: int, tile_y: int) -> np.ndarray:
        if self.current_source is None or self.project.image_asset is None:
            return np.zeros((1, 1, 3), dtype=np.uint8)
        tile_size = self._analysis_tile_size()
        signature = self._analysis_cache_signature()
        key = (signature, tile_x, tile_y)
        cached = self._analysis_tile_cache.get(key)
        if cached is not None:
            return cached
        x0 = tile_x * tile_size
        y0 = tile_y * tile_size
        width = min(tile_size, self.project.image_asset.width - x0)
        height = min(tile_size, self.project.image_asset.height - y0)
        raw = self.current_source.read_window_native(x0, y0, width, height)
        rendered = render_raster_rgb(
            raw,
            self.render_config,
            nodata_value=self.project.image_asset.nodata,
            color_table=getattr(self.project.image_asset, "color_table", None),
        )
        self._analysis_tile_cache[key] = rendered
        return rendered

    def _get_analysis_rgb_roi(self, x0: int, y0: int, width: int, height: int) -> np.ndarray:
        full_cache = self._ensure_full_analysis_rgb()
        if full_cache is not None:
            return full_cache[y0:y0 + height, x0:x0 + width].copy()
        tile_size = self._analysis_tile_size()
        roi = np.zeros((height, width, 3), dtype=np.uint8)
        start_tile_x = x0 // tile_size
        end_tile_x = (x0 + width - 1) // tile_size
        start_tile_y = y0 // tile_size
        end_tile_y = (y0 + height - 1) // tile_size
        for tile_y in range(start_tile_y, end_tile_y + 1):
            for tile_x in range(start_tile_x, end_tile_x + 1):
                tile = self._fetch_rendered_tile(tile_x, tile_y)
                tile_origin_x = tile_x * tile_size
                tile_origin_y = tile_y * tile_size
                src_x0 = max(x0 - tile_origin_x, 0)
                src_y0 = max(y0 - tile_origin_y, 0)
                src_x1 = min(x0 + width - tile_origin_x, tile.shape[1])
                src_y1 = min(y0 + height - tile_origin_y, tile.shape[0])
                dst_x0 = tile_origin_x + src_x0 - x0
                dst_y0 = tile_origin_y + src_y0 - y0
                dst_x1 = dst_x0 + (src_x1 - src_x0)
                dst_y1 = dst_y0 + (src_y1 - src_y0)
                roi[dst_y0:dst_y1, dst_x0:dst_x1] = tile[src_y0:src_y1, src_x0:src_x1]
        return roi

    def _set_display_mode(self, mode: str) -> None:
        if mode not in {"灰度", "RGB"}:
            mode = "灰度"
        self.render_settings.display_mode_combo.setCurrentText(mode)

    def _update_project_coordinate_mode(self, image_asset) -> None:
        has_georef = bool(getattr(image_asset, "has_georef", False) and getattr(image_asset, "geotransform", None))
        self.project.coordinate_mode = "geo_wgs84" if has_georef else "pixel"
        self.project.primary_window_id = self._active_window_id

    def _apply_annotation_coord_ref(self, annotation: AnnotationObject) -> AnnotationObject:
        item = annotation.clone()
        coord_ref: dict[str, object] = {
            "mode": self.project.coordinate_mode,
            "window_id": self._active_window_id,
        }
        if self.project.coordinate_mode == "geo_wgs84" and self.project.image_asset is not None and self.project.image_asset.geotransform is not None:
            try:
                coord_ref["exterior_lonlat"] = [
                    list(
                        pixel_to_lonlat(
                            int(round(point[0])),
                            int(round(point[1])),
                            self.project.image_asset.geotransform,
                            self.project.image_asset.crs_wkt,
                            use_pixel_center=True,
                        )
                    )
                    for point in item.exterior
                ]
                coord_ref["holes_lonlat"] = [
                    [
                        list(
                            pixel_to_lonlat(
                                int(round(point[0])),
                                int(round(point[1])),
                                self.project.image_asset.geotransform,
                                self.project.image_asset.crs_wkt,
                                use_pixel_center=True,
                            )
                        )
                        for point in hole
                    ]
                    for hole in item.holes
                ]
            except Exception:
                coord_ref["mode"] = "pixel"
        item.coord_ref = coord_ref
        return item

    def _configure_default_render_for_source(self, source) -> None:
        metadata = source.metadata()
        style = DefaultRenderStyleFactory.create(metadata)
        config = style_to_legacy_config(style, DefaultRenderStyleFactory.create_display_settings(metadata))
        self.render_settings.reset_to_defaults(metadata.band_count)
        self.render_settings.display_mode_combo.setCurrentText(config.display_mode)
        self.render_settings.gray_band_spin.setValue(int(config.gray_band))
        self.render_settings.band_r_spin.setValue(int(config.rgb_bands[0]))
        self.render_settings.band_g_spin.setValue(int(config.rgb_bands[1]))
        self.render_settings.band_b_spin.setValue(int(config.rgb_bands[2]))
        self.render_settings.set_stretch_mode(config.stretch_mode)
        self.render_settings.auto_range_check.setChecked(not bool(config.auto_range))
        self.render_settings.min_spin.setValue(float(config.value_range[0]))
        self.render_settings.max_spin.setValue(float(config.value_range[1]))
        self.render_settings.gamma_spin.setValue(float(config.gamma))
        self.colormap_combo.setCurrentText(config.colormap_name)

    def _current_global_range(self, settings: dict) -> tuple[float, float] | None:
        if self.current_source is None:
            return None
        return self.current_source.value_range_for_settings(settings)

    def _set_project_mask(self, mask) -> None:
        self.project.mask_data = None if mask is None else np.asarray(mask, dtype=np.uint16).copy()
        self._mark_mask_overlay_dirty()

    def _mark_mask_overlay_dirty(self) -> None:
        self._mask_overlay_revision += 1
        self._raster_overlay_cache_key = None
        self._raster_overlay_cache_value = (None, None)

    def _project_mask_has_foreground(self) -> bool:
        mask = self.project.mask_data
        if mask is None:
            return False
        if self._mask_nonzero_revision != self._mask_overlay_revision:
            self._mask_has_nonzero_cached = bool(np.any(mask))
            self._mask_nonzero_revision = self._mask_overlay_revision
        return bool(self._mask_has_nonzero_cached)

    def _start_progress(self, message: str, maximum: int = 0) -> None:
        self.operation_progress.start_task(message, maximum)
        QApplication.processEvents()

    def _update_progress(self, value: int, message: str | None = None, maximum: int | None = None) -> None:
        self.operation_progress.set_progress(value, message, maximum)
        QApplication.processEvents()

    def _finish_progress(self, message: str = "完成") -> None:
        self.operation_progress.finish_task(message)
        QApplication.processEvents()

    def _fail_progress(self, message: str) -> None:
        self.operation_progress.fail_task(message)
        QApplication.processEvents()

    def _format_exception_message(self, exc: Exception, default_message: str) -> str:
        text = str(exc).strip()
        if text:
            return text
        return f"{default_message}\n可能原因：目标文件正被其他程序占用，或当前没有写入权限。"

    def _sync_project_mask_from_annotations(self) -> None:
        if self.project.image_asset is None:
            self._set_project_mask(None)
            return
        # 运行时矢量转 Mask 入口暂时关闭，仅保留代码以便后续恢复。
        # QMessageBox.information(
        #     self,
        #     "Mask 已恢复",
        #     "当前项目中的 Mask 数据已缺失，已根据现有矢量重新栅格化生成 Mask。",
        # )
        # mask = GeometryService.rasterize_annotations(
        #     self.project.annotations,
        #     self.project.image_asset.width,
        #     self.project.image_asset.height,
        # )
        # self._set_project_mask(mask)
        self._ensure_project_mask_shape()

    def _ensure_project_mask_shape(self) -> None:
        if self.project.image_asset is None:
            return
        height = self.project.image_asset.height
        width = self.project.image_asset.width
        if self.project.mask_data is None:
            self.project.mask_data = np.zeros((height, width), dtype=np.uint16)
            self._mark_mask_overlay_dirty()
            return
        if self.project.mask_data.shape != (height, width):
            updated = np.zeros((height, width), dtype=np.uint16)
            min_h = min(height, self.project.mask_data.shape[0])
            min_w = min(width, self.project.mask_data.shape[1])
            updated[:min_h, :min_w] = self.project.mask_data[:min_h, :min_w]
            self.project.mask_data = updated
            self._mark_mask_overlay_dirty()

    def _extract_mask_patch(self, bbox: tuple[int, int, int, int] | None) -> np.ndarray | None:
        if bbox is None or self.project.image_asset is None:
            return None
        self._ensure_project_mask_shape()
        x, y, width, height = bbox
        if width <= 0 or height <= 0:
            return None
        return self.project.mask_data[y:y + height, x:x + width].copy()

    def _rasterize_annotations_patch(self, annotations, bbox: tuple[int, int, int, int] | None) -> np.ndarray | None:
        if bbox is None:
            return None
        x, y, width, height = bbox
        if width <= 0 or height <= 0:
            return None
        shifted = []
        for annotation in annotations:
            if not GeometryService.bbox_intersects(annotation.bbox, bbox):
                continue
            clone = annotation.clone()
            clone.exterior = [[pt[0] - x, pt[1] - y] for pt in clone.exterior]
            clone.holes = [[[pt[0] - x, pt[1] - y] for pt in hole] for hole in clone.holes]
            GeometryService.refresh_annotation_metadata(clone)
            shifted.append(clone)
        if not shifted:
            return np.zeros((height, width), dtype=np.uint16)
        return GeometryService.rasterize_annotations(shifted, width, height)

    def _update_mask_patch_for_annotation_edit(
        self,
        before: AnnotationObject,
        after: AnnotationObject,
    ) -> None:
        bbox = GeometryService.affected_bbox_from_annotations(before, after)
        if bbox is None or self.project.image_asset is None:
            return
        snapshot = None if self._node_edit_mask_snapshot is None else self._node_edit_mask_snapshot.copy()
        if snapshot is None:
            self._sync_project_mask_from_annotations()
            return

        x, y, width, height = bbox
        if width <= 0 or height <= 0:
            return

        patch = snapshot[y:y + height, x:x + width].copy()

        old_patch = self._rasterize_annotations_patch([before], bbox)
        if old_patch is not None:
            patch[old_patch > 0] = 0

        new_patch = self._rasterize_annotations_patch([after], bbox)
        if new_patch is not None:
            patch[new_patch > 0] = new_patch[new_patch > 0]

        self._ensure_project_mask_shape()
        self.project.mask_data[y:y + height, x:x + width] = patch
        self._mark_mask_overlay_dirty()

    def _annotations_after_commands(self, commands, base_annotations=None) -> list[AnnotationObject]:
        annotations = [item.clone() for item in (base_annotations if base_annotations is not None else self.project.annotations)]
        for command in commands:
            if isinstance(command, BatchCommand):
                annotations = self._annotations_after_commands(command.commands, annotations)
            elif isinstance(command, AddAnnotationCommand):
                annotations.append(command.annotation.clone())
            elif isinstance(command, DeleteAnnotationCommand):
                annotations = [item for item in annotations if item.id != command.annotation.id]
            elif isinstance(command, UpdateGeometryCommand):
                annotations = [command.after.clone() if item.id == command.annotation_id else item for item in annotations]
            elif isinstance(command, UpdateLabelAssignmentCommand):
                updated = []
                for item in annotations:
                    if item.id == command.annotation_id:
                        clone = item.clone()
                        clone.label_id = command.after_label_id
                        updated.append(clone)
                    else:
                        updated.append(item)
                annotations = updated
        return annotations

    def _push_commands_with_mask_patch(
        self,
        commands,
        affected_bbox: tuple[int, int, int, int] | None = None,
        update_mask: bool = True,
        explicit_mask_patch: tuple[tuple[int, int, int, int], np.ndarray | None, np.ndarray | None] | None = None,
    ) -> None:
        if not commands and explicit_mask_patch is None and not update_mask:
            return
        batch_commands = commands[:]
        if explicit_mask_patch is not None:
            bbox, before_patch, after_patch = explicit_mask_patch
        elif update_mask:
            bbox = affected_bbox
            before_patch = self._extract_mask_patch(bbox)
            after_patch = self._apply_annotation_commands_to_mask_patch(before_patch, commands, bbox)
        else:
            bbox = None
            before_patch = None
            after_patch = None
        if bbox is not None:
            batch_commands.append(UpdateMaskPatchCommand(bbox, before_patch, after_patch))
        self.command_stack.push(BatchCommand(batch_commands))
        if update_mask or explicit_mask_patch is not None:
            self._mark_mask_overlay_dirty()
        self.tool_controller.set_annotations(self.project.annotations)

    def _push_mask_only_patch(
        self,
        bbox: tuple[int, int, int, int],
        after_patch: np.ndarray | None,
    ) -> None:
        before_patch = self._extract_mask_patch(bbox)
        self._push_commands_with_mask_patch(
            [],
            affected_bbox=None,
            update_mask=False,
            explicit_mask_patch=(bbox, before_patch, after_patch),
        )

    def _apply_binary_preview_mask(
        self,
        preview_mask: np.ndarray,
        preview_bbox: tuple[int, int, int, int],
        label_id: int,
    ) -> None:
        x, y, width, height = preview_bbox
        before_patch = self._extract_mask_patch(preview_bbox)
        after_patch = np.zeros((height, width), dtype=np.uint16) if before_patch is None else before_patch.copy()
        after_patch[preview_mask > 0] = int(label_id)
        self._push_mask_only_patch(preview_bbox, after_patch)

    def _apply_annotation_to_mask(
        self,
        annotation: AnnotationObject,
    ) -> None:
        bbox = GeometryService.affected_bbox_from_annotations(annotation)
        if bbox is None:
            return
        before_patch = self._extract_mask_patch(bbox)
        after_patch = np.zeros((bbox[3], bbox[2]), dtype=np.uint16) if before_patch is None else before_patch.copy()
        raster_patch = self._rasterize_annotations_patch([annotation], bbox)
        if raster_patch is not None:
            after_patch[raster_patch > 0] = raster_patch[raster_patch > 0]
        self._push_mask_only_patch(bbox, after_patch)

    def _build_magic_mask_patch(
        self,
        new_annotations: list[AnnotationObject],
        preview_mask: np.ndarray,
        preview_bbox: tuple[int, int, int, int],
    ) -> tuple[tuple[int, int, int, int], np.ndarray | None, np.ndarray | None]:
        new_union = GeometryService.annotations_union(new_annotations)
        affected_annotations: list[AnnotationObject] = []
        if new_union is not None and not new_union.is_empty:
            minx, miny, maxx, maxy = new_union.bounds
            bounds_bbox = (minx, miny, maxx - minx, maxy - miny)
            for annotation in self.project.annotations:
                if not GeometryService.bbox_intersects(annotation.bbox, bounds_bbox):
                    continue
                polygon = GeometryService.annotation_to_polygon(annotation)
                if polygon is None or polygon.is_empty or not polygon.intersects(new_union):
                    continue
                affected_annotations.append(annotation)

        bbox = GeometryService.affected_bbox_from_annotations(affected_annotations, new_annotations) or preview_bbox
        before_patch = self._extract_mask_patch(bbox)
        x, y, width, height = bbox
        after_patch = np.zeros((height, width), dtype=np.uint16)
        if before_patch is not None:
            after_patch[:] = before_patch

        old_patch = self._rasterize_annotations_patch(affected_annotations, bbox)
        if old_patch is not None:
            after_patch[old_patch > 0] = 0

        px, py, pw, ph = preview_bbox
        patch_x0 = max(px, x)
        patch_y0 = max(py, y)
        patch_x1 = min(px + pw, x + width)
        patch_y1 = min(py + ph, y + height)
        if patch_x1 > patch_x0 and patch_y1 > patch_y0:
            src_x0 = patch_x0 - px
            src_y0 = patch_y0 - py
            src_x1 = src_x0 + (patch_x1 - patch_x0)
            src_y1 = src_y0 + (patch_y1 - patch_y0)
            dst_x0 = patch_x0 - x
            dst_y0 = patch_y0 - y
            dst_x1 = dst_x0 + (patch_x1 - patch_x0)
            dst_y1 = dst_y0 + (patch_y1 - patch_y0)
            label_id = int(self.project.active_label_id or 1)
            preview_patch = preview_mask[src_y0:src_y1, src_x0:src_x1]
            region = after_patch[dst_y0:dst_y1, dst_x0:dst_x1]
            region[preview_patch > 0] = label_id
            after_patch[dst_y0:dst_y1, dst_x0:dst_x1] = region

        return bbox, before_patch, after_patch

    def _prompt_save_project_if_needed(self) -> bool:
        if not self._dirty:
            return True
        reply = QMessageBox.question(
            self,
            "保存项目",
            "当前项目有未保存的更改。是否先保存项目？",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.Cancel:
            return False
        if reply == QMessageBox.Yes:
            return self.save_project()
        return True

    def _replace_labels(self, labels) -> None:
        self.project.labels = labels[:]
        self.label_store.set_labels(labels[:])
        label_ids = {label.id for label in labels}
        if self.project.active_label_id not in label_ids:
            self.project.active_label_id = labels[0].id if labels else None
        self._refresh_label_ui()
        for canvas in self._all_canvases():
            canvas.set_tool_color(self._active_label_color())
        self._refresh_canvas()
        self._set_dirty(True)

    def _on_label_value_changed(self, old_value: int, new_value: int) -> None:
        """标签 ID 就是 Mask 分类值，改值必须同步重写项目数据。"""
        old_value, new_value = int(old_value), int(new_value)
        if old_value == new_value:
            return
        if self.project.mask_data is not None:
            self.project.mask_data[self.project.mask_data == old_value] = new_value
            self._mark_mask_overlay_dirty()
        for annotation in self.project.annotations:
            if annotation.label_id == old_value:
                annotation.label_id = new_value
        if self.project.active_label_id == old_value:
            self.project.active_label_id = new_value
        self.command_stack = CommandStack(self.project)
        self.tool_controller.set_annotations(self.project.annotations)
        self._set_dirty(True)

    def _apply_annotation_commands_to_mask_patch(self, before_patch, commands, bbox):
        """只修改命令实际影响的像素，保留外部 Mask 在同一补丁内的其余分类。"""
        if bbox is None:
            return None
        _, _, width, height = bbox
        patch = np.zeros((height, width), dtype=np.uint16) if before_patch is None else before_patch.copy()

        def erase(annotation: AnnotationObject) -> None:
            raster = self._rasterize_annotations_patch([annotation], bbox)
            if raster is not None:
                patch[raster == annotation.label_id] = 0

        def paint(annotation: AnnotationObject) -> None:
            raster = self._rasterize_annotations_patch([annotation], bbox)
            if raster is not None:
                patch[raster > 0] = raster[raster > 0]

        for command in commands:
            nested = command.commands if isinstance(command, BatchCommand) else [command]
            for item in nested:
                if isinstance(item, AddAnnotationCommand):
                    paint(item.annotation)
                elif isinstance(item, DeleteAnnotationCommand):
                    erase(item.annotation)
                elif isinstance(item, UpdateGeometryCommand):
                    erase(item.before)
                    paint(item.after)
                elif isinstance(item, UpdateLabelAssignmentCommand):
                    annotation = next((value for value in self.project.annotations if value.id == item.annotation_id), None)
                    if annotation is not None:
                        old = annotation.clone()
                        old.label_id = item.before_label_id
                        new = annotation.clone()
                        new.label_id = item.after_label_id
                        erase(old)
                        paint(new)
        return patch

    def _on_label_deleted(self, label_value: int) -> None:
        """删除只移除标签定义，像素和标注依产品约定保留。"""
        self.status_label.setText(f"已删除标签值 {label_value}；对应 Mask 像素保留并以灰色显示")

    def _set_active_label(self, label_id: int) -> None:
        self._apply_label_choice(label_id)

    def _apply_label_choice(self, label_id: int) -> None:
        if self._selected_mask_components:
            self._relabel_selected_mask_components(int(label_id))
        selected_annotations = [
            item for item in self.project.annotations
            if item.id in self.tool_controller.selected_annotation_ids and item.label_id != label_id
        ]
        if selected_annotations:
            commands = [
                UpdateLabelAssignmentCommand(item.id, item.label_id, int(label_id))
                for item in selected_annotations
            ]
            self.command_stack.push(BatchCommand(commands))
            self.tool_controller.set_annotations(self.project.annotations)
            self._set_dirty(True)
            self._refresh_canvas()
        self.project.active_label_id = int(label_id)
        self._refresh_label_ui()
        for canvas in self._all_canvases():
            canvas.set_tool_color(self._active_label_color())

    def _on_brush_size_changed(self, radius: float) -> None:
        value = max(0.2, float(radius))
        for canvas in self._all_canvases():
            canvas.set_brush_radius(value)

    def _refresh_label_ui(self) -> None:
        self.label_panel.blockSignals(True)
        mask_values = [] if self.project.mask_data is None else np.unique(self.project.mask_data)
        self.label_panel.set_reserved_values(mask_values)
        self.label_panel.set_labels(self.project.labels, self.project.active_label_id)
        self.label_panel.blockSignals(False)

    def on_colormap_changed(self, colormap_name: str) -> None:
        if colormap_name.startswith("━"):
            return
        self._render_update_timer.start()

    def on_suggest_colormap(self, colormap_name: str) -> None:
        self.colormap_combo.setCurrentText(colormap_name)

    def on_render_settings_changed(self) -> None:
        self._render_update_timer.start()

    def _apply_render_settings_update(self) -> None:
        settings = self.render_settings.get_all_settings()
        self.colormap_combo.setEnabled(settings.get("display_mode") != "RGB")
        selected_aux_raster = next(
            (
                layer for layer in self._auxiliary_layers
                if layer.get("id") == self._selected_render_layer_id and layer.get("type") == "raster"
            ),
            None,
        )
        if selected_aux_raster is not None:
            cfg = selected_aux_raster.get("render_config")
            if cfg is None:
                source = selected_aux_raster.get("source")
                meta = source.metadata() if source is not None else None
                cfg = default_raster_render_config(
                    band_count=meta.band_count if meta is not None else 1,
                    has_color_table=bool(meta.has_color_table) if meta is not None else False,
                )
            cfg.display_mode = settings["display_mode"]
            cfg.gray_band = settings["gray_band"]
            cfg.rgb_bands = tuple(settings["rgb_bands"])
            cfg.gamma = settings["gamma"]
            cfg.stretch_mode = settings["stretch_mode"]
            cfg.percent_clip = tuple(settings["percent_clip"])
            cfg.std_dev_n = settings["std_dev_n"]
            cfg.auto_range = settings["auto_range"]
            cfg.value_range = tuple(settings["value_range"])
            cfg.colormap_reversed = settings["colormap_reversed"]
            cfg.colormap_name = self.colormap_combo.currentText()
            cfg.smooth_display = settings.get("smooth_display", False)
            selected_aux_raster["render_config"] = cfg
            self._refresh_canvas()
            self._set_dirty(True)
            return
        self.render_config.display_mode = settings["display_mode"]
        self.render_config.gray_band = settings["gray_band"]
        self.render_config.rgb_bands = tuple(settings["rgb_bands"])
        self.render_config.gamma = settings["gamma"]
        self.render_config.stretch_mode = settings["stretch_mode"]
        self.render_config.percent_clip = tuple(settings["percent_clip"])
        self.render_config.std_dev_n = settings["std_dev_n"]
        self.render_config.auto_range = settings["auto_range"]
        self.render_config.value_range = tuple(settings["value_range"])
        self.render_config.global_value_range = (
            self._current_global_range(settings)
            if settings["auto_range"] and settings["stretch_mode"] != "直方图均衡化"
            else None
        )
        self.render_config.colormap_reversed = settings["colormap_reversed"]
        self.render_config.colormap_name = self.colormap_combo.currentText()
        self.render_config.smooth_display = settings.get("smooth_display", False)
        self._save_render_preferences()
        self._clear_analysis_cache()
        if self.current_source is not None:
            for canvas in self._all_canvases():
                canvas.set_render_config(self.render_config)
            self._refresh_canvas()

    def _update_render_settings_bands(self) -> None:
        if self.project.image_asset is None:
            return
        self.render_settings.set_num_bands(max(1, self.project.image_asset.band_count))

    def _update_image_stats_to_render_settings(self) -> None:
        settings = self.render_settings.get_all_settings()
        if not settings.get("auto_range", True):
            return
        value_range = self._current_global_range(settings)
        if value_range is None:
            return
        self.render_settings.set_image_stats(*value_range)

    def open_image(self, file_path: str | None = None) -> None:
        if not self._finish_node_edit_session():
            return
        if not self._handle_pending_magic_session():
            return
        if not self._prompt_save_project_if_needed():
            return
        if not file_path:
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "打开图像",
                self._last_image_dir,
                SEGMENTATION_RASTER_FILE_FILTER,
            )
        if not file_path:
            return
        self._last_image_dir = os.path.dirname(file_path)
        self.project_manager.settings.setValue("last_image_dir", self._last_image_dir)
        self._load_image(file_path)

    def _load_image(self, file_path: str) -> None:
        source = open_raster_source(file_path, pyramid_threshold_mb=self.pyramid_threshold_mb)
        self._configure_default_render_for_source(source)
        self._apply_source(
            source,
            reset_project=True,
            annotations=[],
            labels=self.label_store.labels(),
            active_label_id=self.project.active_label_id or 1,
        )
        self.current_project_path = None
        self._saved_export_output_dir = ""
        self._set_dirty(False)

    def _apply_source(
        self,
        source,
        reset_project: bool,
        annotations,
        labels,
        active_label_id,
    ) -> None:
        meta = source.metadata()
        self.current_source = source
        self._clear_auxiliary_layers()
        if reset_project:
            self.project = SegmentationProject(
                project_version="1.0",
                image_asset=meta,
                labels=labels,
                annotations=annotations,
                active_label_id=active_label_id,
                magic_panel_settings=self.magic_panel.get_panel_state(),
            )
        else:
            self.project.image_asset = meta
            self.project.labels = labels
            self.project.annotations = annotations
            self.project.active_label_id = active_label_id
        self._update_project_coordinate_mode(meta)
        if self.project.mask_data is None:
            self._sync_project_mask_from_annotations()
        self.command_stack = CommandStack(self.project)
        self._clear_node_edit_session_state(clear_override=True)
        self._clear_analysis_cache()
        if isinstance(self.project.export_prefs, dict):
            self._base_nodata_override = self.project.export_prefs.get("base_nodata_override")
        else:
            self._base_nodata_override = None
        for canvas in self._all_canvases():
            canvas.set_render_config(self.render_config)
            canvas.set_raster_source(source)
            if self._base_nodata_override is not None:
                canvas.set_nodata_value(self._base_nodata_override)
            canvas.set_interaction_mode(self.tool_controller.active_tool)
        self.tool_controller.set_annotations(self.project.annotations)
        if hasattr(self, "render_sidebar_controller"):
            self.render_sidebar_controller.refresh()
        self.status_label.setText(f"{os.path.basename(meta.path)} | {meta.width} x {meta.height}")
        self._update_render_settings_bands()
        self._update_image_stats_to_render_settings()
        self._apply_render_settings_update()
        self._replace_labels(self.project.labels)
        if not self.project.magic_panel_settings:
            self.project.magic_panel_settings = self.magic_panel.get_panel_state()
        self.magic_panel.apply_panel_state(self.project.magic_panel_settings)
        self.magic_panel.refresh_icons()
        self._clear_magic_preview()
        self._current_magic_seed = None
        self._apply_layer_visual_prefs()
        self._rebuild_layer_panel_items()
        self._refresh_canvas()
        if reset_project:
            for canvas in self._all_canvases():
                canvas.fit_image()

    def _clear_auxiliary_layers(self) -> None:
        for layer in self._auxiliary_layers:
            layer_id = layer.get("id")
            if not layer_id:
                continue
            for canvas in self._all_canvases():
                try:
                    canvas.remove_layer(layer_id)
                except Exception:
                    pass
            self.project.layer_visibility.pop(layer_id, None)
            self._layer_window_visibility.pop(str(layer_id), None)
            if isinstance(self.project.export_prefs, dict):
                self.project.export_prefs.get("layer_opacity", {}).pop(layer_id, None)
                self.project.export_prefs.get("layer_blend_mode", {}).pop(layer_id, None)
        self._auxiliary_layers = []
        self._auxiliary_layer_counter = 0

    def open_project(self) -> None:
        if not self._finish_node_edit_session():
            return
        if not self._handle_pending_magic_session():
            return
        if not self._prompt_save_project_if_needed():
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "打开项目",
            self._last_project_dir,
            f"Segmentation Project (*{self.project_manager.PROJECT_SUFFIX} *{self.project_manager.LEGACY_PROJECT_SUFFIX});;JSON (*.json)",
        )
        if not file_path:
            return
        self._last_project_dir = os.path.dirname(file_path)
        self.project_manager.settings.setValue("last_project_dir", self._last_project_dir)
        try:
            project = self.project_manager.load_project(file_path)
        except Exception as exc:
            QMessageBox.warning(self, "打开失败", f"该文件不是有效的分割项目文件，或项目已损坏：\n{exc}")
            return
        saved_display_state = copy.deepcopy(project.display_state)
        self.project = project
        self.label_store.set_labels(project.labels)
        self.current_project_path = file_path
        image_path = project.image_asset.path if project.image_asset else None
        if not image_path:
            QMessageBox.warning(self, "错误", "项目文件中缺少图像路径。")
            return
        source = open_raster_source(image_path, pyramid_threshold_mb=self.pyramid_threshold_mb)
        self._configure_default_render_for_source(source)
        self._apply_source(
            source,
            reset_project=False,
            annotations=project.annotations,
            labels=project.labels,
            active_label_id=project.active_label_id,
        )
        self.project.display_state = saved_display_state
        self.project.layer_visibility = project.layer_visibility
        self.project.export_prefs = project.export_prefs
        self._saved_export_output_dir = self._project_export_output_dir(project)
        self._restore_auxiliary_layers_from_project()
        self._restore_canvas_view_state()
        self.layer_controller.apply_visibility_map(self.project.layer_visibility)
        self._apply_layer_visual_prefs()
        self._restore_layer_order_from_project()
        self._selected_render_layer_id = self.project.export_prefs.get("selected_layer") if isinstance(self.project.export_prefs, dict) else None
        self._refresh_label_ui()
        self._refresh_canvas()
        self._set_dirty(False)

    def _apply_layer_visual_prefs(self) -> None:
        opacity_map = self.project.export_prefs.get("layer_opacity", {}) if isinstance(self.project.export_prefs, dict) else {}
        blend_map = self.project.export_prefs.get("layer_blend_mode", {}) if isinstance(self.project.export_prefs, dict) else {}
        for canvas in self._all_canvases():
            for layer_id, value in (opacity_map or {}).items():
                if canvas.layer_manager.layer(layer_id):
                    try:
                        canvas.set_layer_opacity(layer_id, float(value))
                    except Exception:
                        continue
            for layer_id, mode in (blend_map or {}).items():
                if canvas.layer_manager.layer(layer_id):
                    canvas.set_layer_blend_mode(layer_id, str(mode))

    def _save_layer_order_to_project(self) -> None:
        if not isinstance(self.project.export_prefs, dict):
            self.project.export_prefs = {}
        self.project.export_prefs["layer_order"] = [
            state.spec.id
            for state in self.canvas.layer_manager.layers()
            if state.spec.id not in {"draft", "snap", "preview_vector", "annotations"}
        ]

    def _restore_layer_order_from_project(self) -> None:
        if not isinstance(self.project.export_prefs, dict):
            return
        order = self.project.export_prefs.get("layer_order")
        if not isinstance(order, list):
            return
        for canvas in self._all_canvases():
            for index, layer_id in enumerate(order):
                if canvas.layer_manager.layer(layer_id):
                    canvas.move_layer(layer_id, index)
        self._rebuild_layer_panel_items()

    def _save_auxiliary_layers_to_project(self) -> None:
        if not isinstance(self.project.export_prefs, dict):
            self.project.export_prefs = {}
        serializable = []
        for layer in self._auxiliary_layers:
            render_cfg = layer.get("render_config")
            if render_cfg is not None and hasattr(render_cfg, "__dict__"):
                render_cfg = dict(render_cfg.__dict__)
            raw_path = layer.get("path")
            path_mode = "absolute"
            stored_path = str(Path(raw_path).resolve()) if raw_path else raw_path
            serializable.append(
                {
                    "id": layer.get("id"),
                    "name": layer.get("name"),
                    "type": layer.get("type"),
                    "path": stored_path,
                    "path_mode": path_mode,
                    "mode": layer.get("mode"),
                    "bbox": layer.get("bbox"),
                    "nodata_override": layer.get("nodata_override"),
                    "vector_style": layer.get("vector_style"),
                    "render_config": render_cfg,
                }
            )
        self.project.export_prefs["aux_layers"] = serializable

    def _restore_auxiliary_layers_from_project(self) -> None:
        self._clear_auxiliary_layers()
        if not isinstance(self.project.export_prefs, dict):
            return
        items = self.project.export_prefs.get("aux_layers")
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            if not path or not os.path.exists(path):
                if self.current_project_path:
                    path = self.project_manager.resolve_image_path(
                        str(item.get("path", "")),
                        str(item.get("path_mode", "absolute")),
                        self.current_project_path,
                    )
            if not path or not os.path.exists(path):
                continue
            try:
                if str(item.get("type")) == "vector":
                    self._import_aux_vector(path, reuse=item)
                else:
                    self._import_aux_raster(path, reuse=item)
            except Exception:
                continue

    def save_project(self) -> bool:
        if not self._finish_node_edit_session():
            return False
        if self.project.image_asset is None:
            QMessageBox.warning(self, "提示", "请先打开图像。")
            return False
        if not self.current_project_path:
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "保存项目",
                str(
                    (
                        Path(self._last_project_dir) / (Path(self.project.image_asset.path).stem + self.project_manager.PROJECT_SUFFIX)
                    ) if self._last_project_dir else Path(self.project.image_asset.path).with_suffix(self.project_manager.PROJECT_SUFFIX)
                ),
                f"Segmentation Project (*{self.project_manager.PROJECT_SUFFIX})",
            )
            if not file_path:
                return False
            if not file_path.endswith(self.project_manager.PROJECT_SUFFIX):
                file_path = f"{file_path}{self.project_manager.PROJECT_SUFFIX}"
            self.current_project_path = file_path
        self._last_project_dir = os.path.dirname(self.current_project_path)
        self.project_manager.settings.setValue("last_project_dir", self._last_project_dir)
        self._sync_display_state_from_canvas()
        self._save_canvas_view_state()
        self._save_layer_order_to_project()
        self._save_auxiliary_layers_to_project()
        self.project.magic_panel_settings = self.magic_panel.get_panel_state()
        self._start_progress("正在保存项目...")
        try:
            self._update_progress(30, "正在写入项目文件...", maximum=100)
            self.project_manager.save_project(self.project, self.current_project_path)
            self._saved_export_output_dir = self._project_export_output_dir(self.project)
            self._update_progress(100, "项目保存完成", maximum=100)
            self._finish_progress("项目保存完成")
        except Exception as exc:
            self._fail_progress("项目保存失败")
            QMessageBox.warning(self, "保存失败", str(exc))
            return False
        self._set_dirty(False)
        QMessageBox.information(
            self,
            "保存成功",
            f"项目已保存到:\n{self.current_project_path}",
        )
        return True

    def import_auxiliary_data(self) -> None:
        if self.project.image_asset is None:
            QMessageBox.warning(self, "提示", "请先打开待分割影像，再导入辅助数据。")
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入辅助数据",
            self._last_aux_dir or self._last_image_dir,
            "辅助数据 ("
            + " ".join(f"*{ext}" for ext in SEGMENTATION_RASTER_EXTENSIONS)
            + " *.shp *.geojson *.json *.gpkg *.kml *.kmz *.gml)",
        )
        if not file_path:
            return
        self._last_aux_dir = str(Path(file_path).parent)
        self.project_manager.settings.setValue("last_aux_dir", self._last_aux_dir)
        try:
            self._import_auxiliary_paths([file_path])
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", str(exc))

    def import_mask(self) -> None:
        """导入单波段分类 Mask；该操作始终替换当前 Mask 与标签列表。"""
        if self.project.image_asset is None:
            QMessageBox.warning(self, "提示", "请先打开待分割影像，再导入 Mask。")
            return
        if not self._finish_node_edit_session() or not self._handle_pending_magic_session():
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入 Mask（替换）",
            self._last_mask_dir or self._last_image_dir,
            "分类 Mask (*.tif *.tiff *.png *.bmp)",
        )
        if not file_path:
            return
        reply = QMessageBox.question(
            self,
            "替换当前 Mask",
            "导入将替换当前 Mask、标签列表和撤销/重做历史。\n"
            "如需合并多个 Mask，请先在外部完成合并。是否继续？",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            mask, asset = import_mask_for_image(file_path, self.project.image_asset)
            self._replace_imported_mask(mask, asset)
        except Exception as exc:
            QMessageBox.warning(self, "导入 Mask 失败", str(exc))
            return
        self._last_mask_dir = str(Path(file_path).parent)
        self.project_manager.settings.setValue("last_mask_dir", self._last_mask_dir)
        QMessageBox.information(self, "导入 Mask", f"已导入 {len(asset['values'])} 个分类值。")

    def _replace_imported_mask(self, mask: np.ndarray, mask_asset: dict) -> None:
        """无 UI 的替换入口，便于测试与后续拖放扩展。"""
        values = [int(value) for value in np.unique(mask) if int(value) != 0]
        labels: list[LabelClass] = []
        colors: list[str] = []
        for value in values:
            color = self._next_import_label_color(colors)
            colors.append(color)
            labels.append(LabelClass(value, f"类别 {value}", color, str(value)))
        self._clear_mask_selection()
        self._set_project_mask(mask)
        self.project.mask_asset = dict(mask_asset)
        self.project.labels = labels
        self.label_store.set_labels(labels)
        self.project.active_label_id = labels[0].id if labels else None
        self.command_stack = CommandStack(self.project)
        self._refresh_label_ui()
        for canvas in self._all_canvases():
            canvas.set_tool_color(self._active_label_color())
        self._refresh_canvas()
        self._set_dirty(True)

    @staticmethod
    def _next_import_label_color(colors: list[str]) -> str:
        from src.widgets.label_panel_widget import generate_distinct_label_color
        return generate_distinct_label_color(colors)

    def _on_layer_panel_files_dropped(self, paths: list[str]) -> None:
        if self.project.image_asset is None:
            QMessageBox.warning(self, "提示", "请先打开待分割影像，再导入辅助数据。")
            return
        valid_paths = [item for item in (paths or []) if os.path.isfile(item)]
        if not valid_paths:
            return
        try:
            self._import_auxiliary_paths(valid_paths)
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", str(exc))

    def _import_auxiliary_paths(self, file_paths: list[str]) -> None:
        imported_any = False
        for file_path in file_paths:
            lower = str(file_path).lower()
            if lower.endswith((".shp", ".geojson", ".json", ".gpkg", ".kml", ".kmz", ".gml")):
                self._import_aux_vector(file_path)
                imported_any = True
                continue
            if is_segmentation_raster_file(file_path):
                self._import_aux_raster(file_path)
                imported_any = True
                continue
        if imported_any:
            self._rebuild_layer_panel_items()
            self._refresh_canvas()
            self._set_dirty(True)

    def _next_aux_layer_id(self, prefix: str) -> str:
        self._auxiliary_layer_counter += 1
        return f"{prefix}_{self._auxiliary_layer_counter}"

    def _bump_aux_counter_from_layer_id(self, layer_id: str) -> None:
        text = str(layer_id or "")
        if "_" not in text:
            return
        try:
            value = int(text.rsplit("_", 1)[1])
        except ValueError:
            return
        self._auxiliary_layer_counter = max(self._auxiliary_layer_counter, value)

    def _base_has_georef(self) -> bool:
        asset = self.project.image_asset
        return bool(asset and asset.has_georef and asset.geotransform and asset.crs_wkt)

    def _base_raster_bounds_wgs84(self):
        asset = self.project.image_asset
        if asset is None:
            return None
        return get_raster_bounds_wgs84(
            int(asset.width),
            int(asset.height),
            asset.geotransform,
            asset.crs_wkt,
        )

    def _import_aux_raster(self, file_path: str, reuse: dict | None = None) -> None:
        source = open_raster_source(file_path, pyramid_threshold_mb=self.pyramid_threshold_mb)
        meta = source.metadata()
        aux_has_geo = bool(meta.has_georef and meta.geotransform and meta.crs_wkt)
        base_has_geo = self._base_has_georef()
        overlap = False
        if base_has_geo and aux_has_geo:
            base_bounds = self._base_raster_bounds_wgs84()
            aux_bounds = get_raster_bounds_wgs84(int(meta.width), int(meta.height), meta.geotransform, meta.crs_wkt)
            overlap = bounds_overlap(base_bounds, aux_bounds)

        mode = str((reuse or {}).get("mode", "independent"))
        if base_has_geo and aux_has_geo and overlap:
            mode = "align"
        elif (not base_has_geo) and (not aux_has_geo):
            mode = "center_align"
        elif mode not in {"align", "center_align", "independent"}:
            mode = "independent"
        bbox = tuple((reuse or {}).get("bbox", self._aux_raster_bbox(meta, mode)))
        layer_id = str((reuse or {}).get("id") or self._next_aux_layer_id("aux_raster"))
        self._bump_aux_counter_from_layer_id(layer_id)
        layer_name = str((reuse or {}).get("name") or f"辅助栅格-{Path(file_path).name}")
        render_cfg = copy.deepcopy((reuse or {}).get("render_config") or default_raster_render_config(meta.band_count, bool(meta.has_color_table)))
        if isinstance(render_cfg, dict):
            cfg = default_raster_render_config(meta.band_count, bool(meta.has_color_table))
            for key, value in render_cfg.items():
                if hasattr(cfg, key):
                    setattr(cfg, key, value)
            render_cfg = cfg
        if int(meta.band_count or 1) < 3:
            render_cfg.display_mode = "灰度"
            render_cfg.gray_band = 1
        elif int(meta.band_count or 1) >= 3:
            render_cfg.display_mode = "RGB"
            render_cfg.rgb_bands = (1, 2, 3)
        nodata_override = (reuse or {}).get("nodata_override")

        for canvas in self._all_canvases():
            canvas.set_raster_overlay(layer_id, None, None, name=layer_name, opacity=1.0)
        self.project.layer_visibility[layer_id] = True
        self._auxiliary_layers.append(
            {
                "id": layer_id,
                "name": layer_name,
                "type": "raster",
                "path": file_path,
                "source": source,
                "render_config": render_cfg,
                "nodata_override": nodata_override,
                "bbox": bbox,
                "mode": mode,
            }
        )
        if reuse is None and base_has_geo and aux_has_geo and not overlap:
            QMessageBox.information(self, "提示", "辅助栅格与当前影像地理范围无重叠，已放入独立坐标系显示。")
        elif reuse is None and base_has_geo and (not aux_has_geo):
            QMessageBox.information(self, "提示", "辅助栅格无地理信息，已放入独立坐标系显示。")
        elif reuse is None and (not base_has_geo) and aux_has_geo:
            QMessageBox.information(self, "提示", "当前影像无地理信息，辅助栅格含地理信息，已放入独立坐标系显示。")

    def _aux_raster_bbox(self, meta, mode: str) -> tuple[float, float, float, float]:
        width = float(meta.width)
        height = float(meta.height)
        if self.project.image_asset is None:
            return (0.0, 0.0, width, height)
        base_w = float(self.project.image_asset.width)
        base_h = float(self.project.image_asset.height)
        if mode == "center_align":
            return ((base_w - width) / 2.0, (base_h - height) / 2.0, width, height)
        if mode == "align" and self._base_has_georef() and meta.geotransform and meta.crs_wkt:
            mapped = self._map_geo_raster_to_base_bbox(meta)
            if mapped is not None:
                return mapped
        offset = 40.0 * (len(self._auxiliary_layers) + 1)
        return (base_w + offset, offset, width, height)

    def _map_geo_raster_to_base_bbox(self, aux_meta) -> tuple[float, float, float, float] | None:
        if self.project.image_asset is None or self.project.image_asset.geotransform is None:
            return None
        try:
            from osgeo import gdal
            base_inv_gt = invert_geotransform(self.project.image_asset.geotransform)
            if base_inv_gt is None:
                return None
            to_base = build_coordinate_transform(
                source_projection=aux_meta.crs_wkt,
                target_projection=self.project.image_asset.crs_wkt,
            )
            corners = [(0, 0), (aux_meta.width, 0), (0, aux_meta.height), (aux_meta.width, aux_meta.height)]
            pixels = []
            for px, py in corners:
                map_x, map_y = pixel_to_map_coords(px, py, aux_meta.geotransform, use_pixel_center=False)
                if map_x is None or map_y is None:
                    continue
                tx, ty = transform_point(map_x, map_y, to_base)
                if tx is None or ty is None:
                    continue
                bx, by = gdal.ApplyGeoTransform(base_inv_gt, tx, ty)
                pixels.append((float(bx), float(by)))
            if not pixels:
                return None
            xs = [item[0] for item in pixels]
            ys = [item[1] for item in pixels]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            return (x0, y0, max(1.0, x1 - x0), max(1.0, y1 - y0))
        except Exception:
            return None

    def _import_aux_vector(self, file_path: str, reuse: dict | None = None) -> None:
        try:
            from osgeo import ogr
        except Exception as exc:
            raise RuntimeError(f"当前环境缺少矢量读取依赖（GDAL/OGR）：{exc}") from exc
        ds = ogr.Open(file_path)
        if ds is None:
            raise RuntimeError("无法打开矢量数据。")
        layer = ds.GetLayer(0)
        if layer is None:
            raise RuntimeError("矢量数据不包含可用图层。")
        srs = layer.GetSpatialRef()
        aux_has_geo = srs is not None
        base_has_geo = self._base_has_georef()
        overlap = False
        if base_has_geo and aux_has_geo:
            base_bounds = self._base_raster_bounds_wgs84()
            overlap = bounds_overlap(base_bounds, self._vector_layer_bounds_wgs84(layer, srs))

        mode = str((reuse or {}).get("mode", "independent"))
        if base_has_geo and aux_has_geo and overlap:
            mode = "align"
        elif (not base_has_geo) and (not aux_has_geo):
            mode = "center_align"
        elif mode not in {"align", "center_align", "independent"}:
            mode = "independent"

        annotations = self._vector_layer_to_annotations(layer, srs, mode)
        if not annotations:
            raise RuntimeError("当前仅支持导入面要素（Polygon/MultiPolygon），未发现可显示要素。")
        layer_id = str((reuse or {}).get("id") or self._next_aux_layer_id("aux_vector"))
        self._bump_aux_counter_from_layer_id(layer_id)
        layer_name = str((reuse or {}).get("name") or f"辅助矢量-{Path(file_path).name}")
        vector_style = dict((reuse or {}).get("vector_style") or {})
        color = vector_style.get("color", "#22c55e")
        for canvas in self._all_canvases():
            canvas.set_vector_overlay(layer_id, annotations, color, name=layer_name)
        bbox = GeometryService.affected_bbox_from_annotations(annotations)
        self.project.layer_visibility[layer_id] = True
        self._auxiliary_layers.append(
            {
                "id": layer_id,
                "name": layer_name,
                "type": "vector",
                "path": file_path,
                "annotations": annotations,
                "bbox": bbox,
                "mode": mode,
                "vector_style": {
                    "color": color,
                    "line_width": int(vector_style.get("line_width", 2)),
                    "fill_alpha": int(vector_style.get("fill_alpha", 50)),
                },
                "geometry_type": layer.GetGeomType(),
                "geometry_type_name": ogr.GeometryTypeToName(layer.GetGeomType()),
                "feature_count": int(layer.GetFeatureCount()),
                "extent": layer.GetExtent(),
                "crs_wkt": srs.ExportToWkt() if srs else None,
                "fields": self._collect_vector_fields(layer),
            }
        )
        if reuse is None and base_has_geo and aux_has_geo and not overlap:
            QMessageBox.information(self, "提示", "辅助矢量与当前影像地理范围无重叠，已放入独立坐标系显示。")
        elif reuse is None and base_has_geo and (not aux_has_geo):
            QMessageBox.information(self, "提示", "辅助矢量无地理信息，已放入独立坐标系显示。")
        elif reuse is None and (not base_has_geo) and aux_has_geo:
            QMessageBox.information(self, "提示", "当前影像无地理信息，辅助矢量含地理信息，已放入独立坐标系显示。")

    def _collect_vector_fields(self, layer) -> list[dict]:
        layer_defn = layer.GetLayerDefn()
        fields = []
        for i in range(layer_defn.GetFieldCount()):
            field_defn = layer_defn.GetFieldDefn(i)
            fields.append(
                {
                    "name": field_defn.GetName(),
                    "type": field_defn.GetTypeName(),
                }
            )
        return fields

    def _vector_layer_bounds_wgs84(self, layer, srs) -> tuple[float, float, float, float] | None:
        extent = layer.GetExtent()
        if extent is None:
            return None
        min_x, max_x, min_y, max_y = extent
        to_wgs84 = build_coordinate_transform(
            source_projection=srs.ExportToWkt() if srs else None,
            target_epsg=4326,
        )
        corners = [(min_x, min_y), (min_x, max_y), (max_x, min_y), (max_x, max_y)]
        lon_values = []
        lat_values = []
        for x, y in corners:
            lon, lat = transform_point(x, y, to_wgs84)
            if lon is None or lat is None:
                return None
            lon_values.append(lon)
            lat_values.append(lat)
        return (min(lon_values), min(lat_values), max(lon_values), max(lat_values))

    def _vector_layer_to_annotations(self, layer, srs, mode: str) -> list[AnnotationObject]:
        if self.project.image_asset is None:
            return []
        features = []
        layer.ResetReading()
        extent = layer.GetExtent()
        min_x, max_x, min_y, max_y = extent if extent else (0.0, 1.0, 0.0, 1.0)
        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0
        base_center_x = float(self.project.image_asset.width) / 2.0
        base_center_y = float(self.project.image_asset.height) / 2.0
        base_w = float(self.project.image_asset.width)
        offset = 40.0 * (len(self._auxiliary_layers) + 1)

        base_inv_gt = invert_geotransform(self.project.image_asset.geotransform) if (mode == "align" and self.project.image_asset.geotransform) else None
        to_base = build_coordinate_transform(
            source_projection=srs.ExportToWkt() if srs else None,
            target_projection=self.project.image_asset.crs_wkt if self.project.image_asset else None,
        ) if mode == "align" else None

        def map_point(x: float, y: float) -> tuple[float, float] | None:
            if mode == "align" and base_inv_gt is not None:
                try:
                    from osgeo import gdal
                    tx, ty = transform_point(x, y, to_base)
                    if tx is None or ty is None:
                        return None
                    px, py = gdal.ApplyGeoTransform(base_inv_gt, tx, ty)
                    return float(px), float(py)
                except Exception:
                    return None
            if mode == "center_align":
                return float(x - center_x + base_center_x), float(y - center_y + base_center_y)
            return float(x - min_x + base_w + offset), float(y - min_y + offset)

        def convert_polygon(geom) -> AnnotationObject | None:
            exterior_ring = geom.GetGeometryRef(0)
            if exterior_ring is None:
                return None
            exterior = []
            for i in range(exterior_ring.GetPointCount()):
                x, y, *_ = exterior_ring.GetPoint(i)
                mapped = map_point(x, y)
                if mapped is None:
                    continue
                exterior.append([mapped[0], mapped[1]])
            if len(exterior) < 4:
                return None
            holes = []
            for ring_index in range(1, geom.GetGeometryCount()):
                ring = geom.GetGeometryRef(ring_index)
                hole = []
                for i in range(ring.GetPointCount()):
                    x, y, *_ = ring.GetPoint(i)
                    mapped = map_point(x, y)
                    if mapped is None:
                        continue
                    hole.append([mapped[0], mapped[1]])
                if len(hole) >= 4:
                    holes.append(hole)
            label_id = int(self.project.active_label_id or 1)
            return AnnotationObject.from_polygon(label_id=label_id, exterior=exterior, holes=holes, source_tool="auxiliary")

        feature = layer.GetNextFeature()
        while feature is not None:
            geom = feature.GetGeometryRef()
            if geom is not None:
                geom_type = geom.GetGeometryName().upper()
                if geom_type == "POLYGON":
                    annotation = convert_polygon(geom)
                    if annotation is not None:
                        features.append(annotation)
                elif geom_type == "MULTIPOLYGON":
                    for index in range(geom.GetGeometryCount()):
                        annotation = convert_polygon(geom.GetGeometryRef(index))
                        if annotation is not None:
                            features.append(annotation)
            feature = layer.GetNextFeature()
        return features

    def export_data(self) -> None:
        if not self._finish_node_edit_session():
            return
        if self.project.image_asset is None:
            return
        default_name = f"{Path(self.project.image_asset.path).stem}_mask"
        default_dir = self._saved_export_output_dir
        settings = SegmentationExportDialog.get_settings(
            default_name=default_name,
            default_dir=default_dir,
            has_geo=bool(self.project.image_asset.has_georef),
            prefer_tif_mask=(
                bool(self.project.image_asset.has_georef)
                and self.project.image_asset.path.lower().endswith((".tif", ".tiff"))
            ),
            initial_settings=self._export_dialog_preferences(),
            parent=self,
        )
        if not settings:
            return
        output_dir = Path(settings["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        exported_paths = []
        split_labels = list(self.project.labels) if settings["export_mask"] and settings.get("export_split_masks") else []
        total_steps = int(settings["export_vector"]) + int(settings["export_mask"]) + len(split_labels)
        step_index = 0
        failed_exports = []
        self._start_progress("正在准备导出...", maximum=max(total_steps * 100, 100))
        try:
            if settings["export_vector"]:
                vector_path = output_dir / f"{settings['base_name']}{settings['vector_extension']}"
                try:
                    self._update_progress(step_index * 100 + 10, f"正在导出矢量: {vector_path.name}", maximum=max(total_steps * 100, 100))
                    export_project = self._build_export_project_from_mask()
                    vector_format = settings["vector_format"]
                    if vector_format in {"GeoJSON", "Shapefile", "GPKG"}:
                        vector_driver = {
                            "GeoJSON": "GeoJSON",
                            "Shapefile": "ESRI Shapefile",
                            "GPKG": "GPKG",
                        }[vector_format]
                        export_vector_file(
                            export_project,
                            str(vector_path),
                            vector_driver,
                            coordinate_mode=SegmentationExportDialog.coordinate_mode_for_format(
                                vector_format,
                                bool(self.project.image_asset and self.project.image_asset.has_georef),
                            ),
                        )
                    elif vector_format == "COCO":
                        export_coco(export_project, str(vector_path))
                    elif vector_format == "YOLO":
                        export_yolo(export_project, str(vector_path))
                    elif vector_format == "VOC":
                        export_voc(export_project, str(vector_path))
                    else:
                        raise RuntimeError(f"不支持的导出格式：{vector_format}")
                    exported_paths.append(str(vector_path))
                    self._update_progress(step_index * 100 + 90, f"矢量导出完成: {vector_path.name}", maximum=max(total_steps * 100, 100))
                except Exception as exc:
                    failed_exports.append(f"矢量导出失败（{vector_path.name}）：{self._format_exception_message(exc, '导出失败。')}")
                step_index += 1
                self._update_progress(step_index * 100, maximum=max(total_steps * 100, 100))
            if settings["export_mask"]:
                mask_path = output_dir / f"{settings['base_name']}{settings['mask_extension']}"
                try:
                    if self.project.mask_data is None:
                        raise RuntimeError("当前项目中没有可导出的 Mask。")
                    self._update_progress(step_index * 100 + 10, f"正在导出 Mask: {mask_path.name}", maximum=max(total_steps * 100, 100))
                    export_mask_file(
                        self.project,
                        str(mask_path),
                        encoding=settings["mask_encoding"],
                    )
                    exported_paths.append(str(mask_path))
                    self._update_progress(step_index * 100 + 90, f"Mask 导出完成: {mask_path.name}", maximum=max(total_steps * 100, 100))
                except Exception as exc:
                    failed_exports.append(f"Mask 导出失败（{mask_path.name}）：{self._format_exception_message(exc, '导出失败。')}")
                step_index += 1
                self._update_progress(step_index * 100, maximum=max(total_steps * 100, 100))
                for label_index, label in enumerate(split_labels, start=1):
                    split_mask_path = output_dir / f"{settings['base_name']}{label_index}{settings['mask_extension']}"
                    try:
                        self._update_progress(
                            step_index * 100 + 10,
                            f"正在导出标签 Mask: {split_mask_path.name}",
                            maximum=max(total_steps * 100, 100),
                        )
                        export_mask_file(
                            self.project,
                            str(split_mask_path),
                            binary_label_id=label.id,
                            encoding=settings["mask_encoding"],
                        )
                        exported_paths.append(str(split_mask_path))
                        self._update_progress(
                            step_index * 100 + 90,
                            f"标签 Mask 导出完成: {split_mask_path.name}",
                            maximum=max(total_steps * 100, 100),
                        )
                    except Exception as exc:
                        failed_exports.append(
                            f"标签 Mask 导出失败（{split_mask_path.name}）：{self._format_exception_message(exc, '导出失败。')}"
                        )
                    step_index += 1
                    self._update_progress(step_index * 100, maximum=max(total_steps * 100, 100))
            if not isinstance(self.project.export_prefs, dict):
                self.project.export_prefs = {}
            self.project.export_prefs["export_dialog"] = dict(settings)
            self._saved_export_output_dir = str(settings["output_dir"])
            # 导出选项属于项目状态；后续保存项目时必须写入 .seg_proj。
            self._set_dirty(True)
            if failed_exports:
                final_message = []
                if exported_paths:
                    final_message.append("成功导出：")
                    final_message.extend(exported_paths)
                final_message.append("")
                final_message.append("失败详情：")
                final_message.extend(failed_exports)
                self._fail_progress("部分导出失败")
                QMessageBox.warning(self, "导出完成（部分失败）", "\n".join(final_message))
            else:
                self._finish_progress("导出完成")
                QMessageBox.information(self, "导出成功", "已导出到:\n" + "\n".join(exported_paths))
        except Exception as exc:
            self._fail_progress("导出失败")
            QMessageBox.warning(self, "导出失败", self._format_exception_message(exc, "导出失败。"))

    @staticmethod
    def _project_export_output_dir(project: SegmentationProject) -> str:
        """读取项目中已保存的导出目录；无有效设置时保持为空。"""
        preferences = project.export_prefs if isinstance(project.export_prefs, dict) else {}
        dialog_preferences = preferences.get("export_dialog", {})
        if not isinstance(dialog_preferences, dict):
            dialog_preferences = preferences
        output_dir = dialog_preferences.get("output_dir", "")
        return str(output_dir).strip() if isinstance(output_dir, str) else ""

    def _export_dialog_preferences(self) -> dict:
        """读取项目保存的导出对话框设置，兼容旧项目的扁平结构。"""
        preferences = self.project.export_prefs if isinstance(self.project.export_prefs, dict) else {}
        nested = preferences.get("export_dialog")
        if isinstance(nested, dict):
            return dict(nested)
        return {
            key: preferences[key]
            for key in (
                "output_dir", "base_name", "export_vector", "export_mask", "vector_format",
                "mask_format", "mask_encoding", "export_split_masks",
            )
            if key in preferences
        }

    def _build_export_project_from_mask(self):
        if self.project.image_asset is None:
            raise RuntimeError("缺少图像信息，无法导出矢量。")
        if self.project.mask_data is None:
            raise RuntimeError("当前项目中没有 Mask，无法导出矢量。")
        export_project = copy.deepcopy(self.project)
        export_project.annotations = []
        unique_labels = [int(value) for value in np.unique(export_project.mask_data) if int(value) > 0]
        for label_id in unique_labels:
            binary_mask = np.where(export_project.mask_data == label_id, 255, 0).astype(np.uint8)
            export_project.annotations.extend(
                GeometryService.mask_to_annotations(
                    binary_mask,
                    bbox=(0, 0, export_project.image_asset.width, export_project.image_asset.height),
                    label_id=label_id,
                    connectivity=8,
                    source_tool="export",
                )
            )
        return export_project

    def undo(self) -> None:
        if self._undo_preview_state():
            return
        if self.tool_controller.is_node_edit_active():
            self._undo_node_edit_command()
            return
        if not self._finish_node_edit_session():
            return
        if self.command_stack.undo():
            self._clear_mask_selection()
            self._mark_mask_overlay_dirty()
            self.tool_controller.set_annotations(self.project.annotations)
            self._refresh_canvas()
            self._set_dirty(True)

    def redo(self) -> None:
        if self._redo_preview_state():
            return
        if self.tool_controller.is_node_edit_active():
            self._redo_node_edit_command()
            return
        if not self._finish_node_edit_session():
            return
        if self.command_stack.redo():
            self._clear_mask_selection()
            self._mark_mask_overlay_dirty()
            self.tool_controller.set_annotations(self.project.annotations)
            self._refresh_canvas()
            self._set_dirty(True)

    def _undo_node_edit_command(self) -> None:
        annotation_id = self._node_edit_session_annotation_id
        if annotation_id is None:
            return
        if self.command_stack.undo_depth() <= self._node_edit_session_undo_depth:
            return
        if self.command_stack.undo():
            self._mark_mask_overlay_dirty()
            self.tool_controller.set_annotations(self.project.annotations)
            self.tool_controller.selected_annotation_id = annotation_id
            self.tool_controller.selected_annotation_ids = {annotation_id}
            self.tool_controller.selected_vertex_index = None
            self._node_edit_session_dirty = self.command_stack.undo_depth() > self._node_edit_session_undo_depth
            self._refresh_canvas()
            self._set_dirty(True)

    def _redo_node_edit_command(self) -> None:
        annotation_id = self._node_edit_session_annotation_id
        if annotation_id is None:
            return
        if self.command_stack.redo():
            self._mark_mask_overlay_dirty()
            self.tool_controller.set_annotations(self.project.annotations)
            self.tool_controller.selected_annotation_id = annotation_id
            self.tool_controller.selected_annotation_ids = {annotation_id}
            self.tool_controller.selected_vertex_index = None
            self._node_edit_session_dirty = True
            self._refresh_canvas()
            self._set_dirty(True)

    def delete_selected(self) -> None:
        if self._selected_mask_components:
            self._delete_selected_mask_components()
            return
        if self.tool_controller.selected_vertex_index is not None:
            updated = self.tool_controller.remove_selected_vertex()
            if updated is not None:
                self._set_dirty(True)
            return
        if not self._finish_node_edit_session():
            return
        selected_items = [
            item for item in self.project.annotations
            if item.id in self.tool_controller.selected_annotation_ids
        ]
        if not selected_items:
            return
        self.command_stack.push(BatchCommand([DeleteAnnotationCommand(item) for item in selected_items]))
        self.tool_controller.set_annotations(self.project.annotations)
        self.tool_controller.selected_annotation_id = None
        self.tool_controller.selected_annotation_ids.clear()
        self._refresh_canvas()
        self._set_dirty(True)

    def _delete_selected_mask_components(self) -> None:
        """Delete all selected Mask connected components as one undoable edit."""
        if not self._selected_mask_components or self.project.mask_data is None:
            return
        left = min(item["bbox"][0] for item in self._selected_mask_components)
        top = min(item["bbox"][1] for item in self._selected_mask_components)
        right = max(item["bbox"][0] + item["bbox"][2] for item in self._selected_mask_components)
        bottom = max(item["bbox"][1] + item["bbox"][3] for item in self._selected_mask_components)
        bbox = (left, top, right - left, bottom - top)
        before_patch = self._extract_mask_patch(bbox)
        if before_patch is None:
            self._clear_mask_selection()
            return
        after_patch = before_patch.copy()
        for selection in self._selected_mask_components:
            x, y, width, height = selection["bbox"]
            offset_x, offset_y = x - left, y - top
            region = after_patch[offset_y:offset_y + height, offset_x:offset_x + width]
            region[np.asarray(selection["mask"], dtype=bool)] = 0
        deleted_count = len(self._selected_mask_components)
        self.command_stack.push(UpdateMaskPatchCommand(bbox, before_patch, after_patch))
        self._mark_mask_overlay_dirty()
        self._clear_mask_selection()
        self._refresh_canvas()
        self._set_dirty(True)
        self.status_label.setText(f"已删除 {deleted_count} 个选中 Mask 区域；可按 Ctrl+Z 撤销")

    def _backspace_action(self) -> None:
        if self.tool_controller.active_tool == SegmentationToolController.TOOL_POLYGON:
            if self.tool_controller._polygon_points:
                self.tool_controller._polygon_points.pop()
                if self.tool_controller._polygon_points:
                    self.canvas.update_draft(self.tool_controller._polygon_points[:])
                else:
                    self.canvas.update_draft(None)
        else:
            if self.tool_controller.remove_selected_vertex() is not None:
                self._set_dirty(True)

    def _enter_action(self) -> None:
        if self.tool_controller.active_tool == SegmentationToolController.TOOL_MAGIC_WAND:
            self._confirm_magic_preview()
        else:
            self.tool_controller.finish_polygon()

    def _escape_action(self) -> None:
        if self.tool_controller.active_tool == SegmentationToolController.TOOL_MAGIC_WAND and self._preview_mask is not None:
            self._clear_magic_preview()
        elif self._selected_mask_components:
            self._clear_mask_selection()
        elif self.tool_controller.selected_annotation_ids:
            self._set_controller_selection(set())
        else:
            self.canvas.update_draft(None)

    def _handle_mouse_press(self, payload) -> None:
        if (
            self.tool_controller.active_tool == SegmentationToolController.TOOL_BROWSE
            and payload.button == Qt.LeftButton
        ):
            self._select_mask_component(payload.x, payload.y, bool(payload.modifiers & Qt.ControlModifier))
            return
        if self._handle_mask_paint_payload(payload, begin=True):
            return
        self.tool_controller.handle_press(payload)

    def _show_raster_pixel_menu(self, payload) -> None:
        x = int(np.floor(float(payload.x)))
        y = int(np.floor(float(payload.y)))
        if self.project.image_asset is None:
            return
        if not (0 <= x < int(self.project.image_asset.width) and 0 <= y < int(self.project.image_asset.height)):
            return
        values: list[tuple[str, str]] = []
        if self.current_source is not None:
            values.append(("图像", self._pixel_value_text(self.current_source.read_pixel(x, y))))
        for layer in self._auxiliary_layers:
            if str(layer.get("type")) != "raster":
                continue
            if not self.project.layer_visibility.get(str(layer.get("id")), True):
                continue
            name = str(layer.get("name") or layer.get("id") or "栅格")
            value = self._read_aux_raster_pixel(layer, x, y)
            values.append((name, self._pixel_value_text(value)))
        if not values:
            return
        menu = QMenu(self)
        title_action = menu.addAction(f"像素 ({x}, {y})")
        title_action.setEnabled(False)
        menu.addSeparator()
        for name, text in values:
            action = menu.addAction(f"{name}: {text}")
            action.setEnabled(False)
        menu.exec(QCursor.pos())

    def _select_mask_component(self, image_x: float, image_y: float, additive: bool = False) -> None:
        """Select the clicked non-background Mask connected component (8-neighbourhood)."""
        mask = self.project.mask_data
        x, y = int(np.floor(image_x)), int(np.floor(image_y))
        if mask is None or not (0 <= y < mask.shape[0] and 0 <= x < mask.shape[1]):
            if not additive:
                self._clear_mask_selection()
            return
        label_id = int(mask[y, x])
        if label_id <= 0:
            if not additive:
                self._clear_mask_selection()
            return
        try:
            import cv2
            binary = np.ascontiguousarray(mask == label_id, dtype=np.uint8)
            component_count, component_map, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        except Exception as exc:
            self.status_label.setText(f"无法选择 Mask：{exc}")
            return
        component_id = int(component_map[y, x])
        if component_id <= 0 or component_id >= component_count:
            if not additive:
                self._clear_mask_selection()
            return
        left = int(stats[component_id, cv2.CC_STAT_LEFT])
        top = int(stats[component_id, cv2.CC_STAT_TOP])
        width = int(stats[component_id, cv2.CC_STAT_WIDTH])
        height = int(stats[component_id, cv2.CC_STAT_HEIGHT])
        area = int(stats[component_id, cv2.CC_STAT_AREA])
        component_mask = (component_map[top:top + height, left:left + width] == component_id).astype(np.uint8)
        selection = {
            "bbox": (left, top, width, height),
            "mask": component_mask,
            "label_id": label_id,
            "area": area,
        }
        selection_index = next(
            (
                index for index, item in enumerate(self._selected_mask_components)
                if item["label_id"] == label_id and item["bbox"] == selection["bbox"]
            ),
            None,
        )
        if additive and selection_index is not None:
            self._selected_mask_components.pop(selection_index)
        elif additive:
            self._selected_mask_components.append(selection)
        else:
            self._selected_mask_components = [selection]
        self._set_controller_selection(set())
        self._mask_selection_dash_offset = 0.0
        if self._selected_mask_components:
            self._mask_selection_timer.start()
        else:
            self._mask_selection_timer.stop()
        self._update_mask_selection_display()
        selected_count = len(self._selected_mask_components)
        if selected_count:
            label_name = next((item.name for item in self.project.labels if item.id == label_id), f"标签 {label_id}")
            hint = "Ctrl+点击可继续多选" if not additive else "Ctrl+再次点击可取消选择"
            self.status_label.setText(f"已选中 {selected_count} 个 Mask 区域；当前：{label_name}（{area:,} 像素）。{hint}，点击标签可批量修改类别")
        else:
            self.status_label.setText("已取消 Mask 选择")

    def _relabel_selected_mask_components(self, label_id: int) -> None:
        if not self._selected_mask_components or self.project.mask_data is None:
            return
        changed_selections = [
            item for item in self._selected_mask_components
            if int(item["label_id"]) != label_id
        ]
        if not changed_selections:
            return
        left = min(item["bbox"][0] for item in changed_selections)
        top = min(item["bbox"][1] for item in changed_selections)
        right = max(item["bbox"][0] + item["bbox"][2] for item in changed_selections)
        bottom = max(item["bbox"][1] + item["bbox"][3] for item in changed_selections)
        bbox = (left, top, right - left, bottom - top)
        before_patch = self._extract_mask_patch(bbox)
        if before_patch is None:
            self._clear_mask_selection()
            return
        after_patch = before_patch.copy()
        for selection in changed_selections:
            x, y, width, height = selection["bbox"]
            offset_x, offset_y = x - left, y - top
            region = after_patch[offset_y:offset_y + height, offset_x:offset_x + width]
            region[np.asarray(selection["mask"], dtype=bool)] = int(label_id)
            selection["label_id"] = int(label_id)
        self.command_stack.push(UpdateMaskPatchCommand(bbox, before_patch, after_patch))
        self._mark_mask_overlay_dirty()
        self._set_dirty(True)
        self._refresh_canvas()
        label_name = next((item.name for item in self.project.labels if item.id == label_id), f"标签 {label_id}")
        self.status_label.setText(f"已将 {len(changed_selections)} 个选中 Mask 区域修改为：{label_name}（值 {label_id}）")

    def _clear_mask_selection(self) -> None:
        if not self._selected_mask_components:
            return
        self._selected_mask_components = []
        self._mask_selection_timer.stop()
        self._update_mask_selection_display()

    def _advance_mask_selection_animation(self) -> None:
        if not self._selected_mask_components:
            self._mask_selection_timer.stop()
            return
        self._mask_selection_dash_offset += 1.2
        for canvas in self._all_canvases():
            canvas.set_mask_selection_dash_offset(self._mask_selection_dash_offset)

    def _update_mask_selection_display(self) -> None:
        selections = self._selected_mask_components
        for canvas in self._all_canvases():
            if not selections:
                canvas.update_mask_selections([])
            else:
                canvas.update_mask_selections([(item["mask"], item["bbox"]) for item in selections])
                canvas.set_mask_selection_dash_offset(self._mask_selection_dash_offset)

    def _read_aux_raster_pixel(self, layer: dict, image_x: int, image_y: int):
        source = layer.get("source")
        bbox = layer.get("bbox")
        if source is None or bbox is None:
            return None
        try:
            bx, by, bw, bh = (float(v) for v in bbox)
        except Exception:
            return None
        if bw <= 0 or bh <= 0:
            return None
        if not (bx <= image_x < bx + bw and by <= image_y < by + bh):
            return None
        meta = source.metadata()
        width = max(int(meta.width), 1)
        height = max(int(meta.height), 1)
        px = int(np.floor((float(image_x) - bx) * width / max(bw, 1e-6)))
        py = int(np.floor((float(image_y) - by) * height / max(bh, 1e-6)))
        px = max(0, min(width - 1, px))
        py = max(0, min(height - 1, py))
        try:
            return source.read_pixel(px, py)
        except Exception:
            return None

    def _pixel_value_text(self, value) -> str:
        if value is None:
            return "-"
        arr = np.asarray(value)
        if arr.ndim == 0:
            try:
                return f"{float(arr):.6g}"
            except Exception:
                return str(value)
        flat = arr.reshape(-1).tolist()
        parts = []
        for item in flat:
            try:
                parts.append(f"{float(item):.6g}")
            except Exception:
                parts.append(str(item))
        return "[" + ", ".join(parts) + "]"

    def _handle_mouse_move(self, payload) -> None:
        tool = self.tool_controller.active_tool
        if (
            self._mask_painting
            and tool in {SegmentationToolController.TOOL_BRUSH, SegmentationToolController.TOOL_ERASER}
            and bool(payload.buttons & Qt.LeftButton)
        ):
            self._paint_mask_line_to(payload.x, payload.y, erase=(tool == SegmentationToolController.TOOL_ERASER))

    def _handle_mouse_release(self, payload) -> None:
        if self._handle_mask_paint_payload(payload, end=True):
            return
        self.tool_controller.handle_release(payload)
        self.tool_controller.set_annotations(self.project.annotations)
        self._refresh_canvas()

    def _handle_mask_paint_payload(self, payload, begin: bool = False, end: bool = False) -> bool:
        tool = self.tool_controller.active_tool
        if tool not in {SegmentationToolController.TOOL_BRUSH, SegmentationToolController.TOOL_ERASER}:
            return False
        if end:
            if self._mask_painting and payload.button == Qt.LeftButton:
                self._paint_mask_line_to(payload.x, payload.y, erase=(tool == SegmentationToolController.TOOL_ERASER))
            self._commit_mask_paint_session()
            self._mask_painting = False
            self._last_mask_paint_point = None
            return True
        if begin and payload.button == Qt.LeftButton:
            self._begin_mask_paint_session()
            self._mask_painting = True
            self._last_mask_paint_point = None
            self._paint_mask_line_to(payload.x, payload.y, erase=(tool == SegmentationToolController.TOOL_ERASER))
            return True
        return False

    def _on_tool_action_triggered(self, action: QAction) -> None:
        target = action.data()
        if not self._handle_pending_magic_session():
            current = self.tool_controller.active_tool
            for item in self.tool_action_group.actions():
                if item.data() == current:
                    item.blockSignals(True)
                    item.setChecked(True)
                    item.blockSignals(False)
                    break
            return
        if target != self.tool_controller.active_tool and not self._finish_node_edit_session():
            current = self.tool_controller.active_tool
            for item in self.tool_action_group.actions():
                if item.data() == current:
                    item.blockSignals(True)
                    item.setChecked(True)
                    item.blockSignals(False)
                    break
            return
        self.tool_controller.set_tool(target)
        if target != SegmentationToolController.TOOL_BROWSE:
            self._clear_mask_selection()
        for canvas in self._all_canvases():
            canvas.set_interaction_mode(target)
        if target != SegmentationToolController.TOOL_MAGIC_WAND:
            self._clear_magic_preview()

    def _adjust_active_tool_slider(self, steps: int) -> None:
        tool = self.tool_controller.active_tool
        if tool == SegmentationToolController.TOOL_MAGIC_WAND:
            slider = self.magic_panel.tolerance_slider
            step = self.magic_panel.slider_step("tolerance")
        elif tool in {SegmentationToolController.TOOL_BRUSH, SegmentationToolController.TOOL_ERASER}:
            slider = self.magic_panel.brush_size_slider
            step = self.magic_panel.slider_step("brush_size")
        else:
            return
        delta = int(steps) * max(1, int(step))
        slider.setValue(max(slider.minimum(), min(slider.maximum(), slider.value() + delta)))

    def _begin_mask_paint_session(self) -> None:
        self._mask_paint_bbox = None
        self._mask_paint_before_patch = None
        self._last_mask_paint_point = None

    def _commit_mask_paint_session(self) -> None:
        bbox = self._mask_paint_bbox
        before_patch = self._mask_paint_before_patch
        self._mask_paint_bbox = None
        self._mask_paint_before_patch = None
        if bbox is None:
            return
        after_patch = self._extract_mask_patch(bbox)
        if before_patch is not None and after_patch is not None and np.array_equal(before_patch, after_patch):
            return
        self._push_commands_with_mask_patch(
            [],
            affected_bbox=None,
            update_mask=False,
            explicit_mask_patch=(bbox, before_patch, after_patch),
        )
        self._set_dirty(True)
        self._refresh_canvas()

    def _paint_mask_line_to(self, x: float, y: float, erase: bool = False) -> None:
        previous = self._last_mask_paint_point
        radius = max(0.2, float(self.magic_panel.brush_size()))
        if previous is None:
            self._paint_mask_at(x, y, erase=erase)
            self._last_mask_paint_point = (x, y)
            return

        px, py = previous
        distance = float(np.hypot(x - px, y - py))
        spacing = max(0.4, radius * 0.45)
        segments = max(1, int(np.ceil(distance / spacing)))
        for index in range(1, segments + 1):
            t = index / segments
            self._paint_mask_at(px + (x - px) * t, py + (y - py) * t, erase=erase, refresh=index == segments)
        self._last_mask_paint_point = (x, y)

    def _paint_mask_at(self, x: float, y: float, erase: bool = False, refresh: bool = True) -> None:
        if self.project.image_asset is None:
            return
        self._ensure_project_mask_shape()
        if self.project.mask_data is None:
            return
        if not erase and self.project.active_label_id is None:
            self._show_tool_message("请先选择一个活动标签。")
            return
        radius = max(0.2, float(self.magic_panel.brush_size()))
        cx = int(np.floor(x))
        cy = int(np.floor(y))
        height, width = self.project.mask_data.shape
        bound_radius = int(np.ceil(radius))
        x0 = max(0, cx - bound_radius)
        y0 = max(0, cy - bound_radius)
        x1 = min(width, cx + bound_radius + 1)
        y1 = min(height, cy + bound_radius + 1)
        if x0 >= x1 or y0 >= y1:
            return
        bbox = (x0, y0, x1 - x0, y1 - y0)
        if self._mask_painting:
            self._extend_mask_paint_before_patch(bbox)
            after_patch = self._extract_mask_patch(bbox)
        else:
            before_patch = self._extract_mask_patch(bbox)
            after_patch = np.zeros((y1 - y0, x1 - x0), dtype=np.uint16) if before_patch is None else before_patch.copy()
        if after_patch is None:
            after_patch = np.zeros((y1 - y0, x1 - x0), dtype=np.uint16)
        yy, xx = np.ogrid[y0:y1, x0:x1]
        disk = (xx - cx) ** 2 + (yy - cy) ** 2 <= float(radius) ** 2
        after_patch[disk] = 0 if erase else int(self.project.active_label_id)
        if self._mask_painting:
            self.project.mask_data[y0:y1, x0:x1] = after_patch
            self._mark_mask_overlay_dirty()
        else:
            self._push_mask_only_patch(bbox, after_patch)
            self._set_dirty(True)
        if refresh:
            self._refresh_canvas()

    def _extend_mask_paint_before_patch(self, bbox: tuple[int, int, int, int]) -> None:
        current_before = self._extract_mask_patch(bbox)
        if current_before is None:
            current_before = np.zeros((bbox[3], bbox[2]), dtype=np.uint16)
        if self._mask_paint_bbox is None or self._mask_paint_before_patch is None:
            self._mask_paint_bbox = bbox
            self._mask_paint_before_patch = current_before.copy()
            return

        old_x, old_y, old_w, old_h = self._mask_paint_bbox
        new_x, new_y, new_w, new_h = bbox
        union_x0 = min(old_x, new_x)
        union_y0 = min(old_y, new_y)
        union_x1 = max(old_x + old_w, new_x + new_w)
        union_y1 = max(old_y + old_h, new_y + new_h)
        union_bbox = (union_x0, union_y0, union_x1 - union_x0, union_y1 - union_y0)
        union_before = self._extract_mask_patch(union_bbox)
        if union_before is None:
            union_before = np.zeros((union_bbox[3], union_bbox[2]), dtype=np.uint16)

        old_dx = old_x - union_x0
        old_dy = old_y - union_y0
        union_before[old_dy:old_dy + old_h, old_dx:old_dx + old_w] = self._mask_paint_before_patch

        self._mask_paint_bbox = union_bbox
        self._mask_paint_before_patch = union_before.copy()

    def _add_polygon_annotation(self, polygon_points) -> None:
        if self.project.active_label_id is None:
            return
        annotation = AnnotationObject.from_polygon(
            label_id=self.project.active_label_id,
            exterior=polygon_points,
            source_tool="polygon",
        )
        annotation = self._apply_annotation_coord_ref(annotation)
        # 运行时矢量新增入口暂时关闭，仅保留代码以便后续恢复。
        # affected_bbox = GeometryService.affected_bbox_from_annotations(annotation)
        # self._push_commands_with_mask_patch([AddAnnotationCommand(annotation)], affected_bbox)
        # self.tool_controller.selected_annotation_id = annotation.id
        self._apply_annotation_to_mask(annotation)
        self._refresh_canvas()
        self._set_dirty(True)

    def _add_rectangle_annotation(self, polygon_points) -> None:
        if self.project.active_label_id is None:
            return
        annotation = AnnotationObject.from_polygon(
            label_id=self.project.active_label_id,
            exterior=polygon_points,
            geom_type="rectangle",
            source_tool="rectangle",
        )
        annotation = self._apply_annotation_coord_ref(annotation)
        # 运行时矢量新增入口暂时关闭，仅保留代码以便后续恢复。
        # affected_bbox = GeometryService.affected_bbox_from_annotations(annotation)
        # self._push_commands_with_mask_patch([AddAnnotationCommand(annotation)], affected_bbox)
        # self.tool_controller.selected_annotation_id = annotation.id
        self._apply_annotation_to_mask(annotation)
        self._refresh_canvas()
        self._set_dirty(True)

    def _on_selection_changed(self, selection) -> None:
        selection_ids = set(selection or [])
        previous_id = self._node_edit_session_annotation_id
        next_single_id = next(iter(selection_ids), None) if len(selection_ids) == 1 else None
        if (
            not self._suspend_selection_sync
            and previous_id is not None
            and previous_id != next_single_id
            and not self._finish_node_edit_session()
        ):
            self._set_controller_selection({previous_id})
            return
        self.tool_controller.selected_annotation_ids = selection_ids
        self.tool_controller.selected_annotation_id = next(iter(selection_ids), None)
        self.tool_controller.selected_vertex_index = None if len(selection_ids) != 1 else self.tool_controller.selected_vertex_index
        if self.tool_controller.active_tool == SegmentationToolController.TOOL_BROWSE and len(selection_ids) == 1:
            self._ensure_node_edit_session(self.tool_controller.selected_annotation_id)
        else:
            self._clear_node_edit_session_state()
        self._refresh_canvas()
        self._refresh_label_ui()

    def _on_geometry_changed(self, annotation_id: str, updated: AnnotationObject) -> None:
        self._ensure_node_edit_session(annotation_id)
        updated = self._apply_annotation_coord_ref(updated)
        self.project.annotations = [
            updated.clone() if item.id == annotation_id else item
            for item in self.project.annotations
        ]
        self.tool_controller.set_annotations(self.project.annotations)
        self._refresh_canvas()

    def _on_geometry_committed(self, annotation_id: str, before: AnnotationObject, after: AnnotationObject) -> None:
        self._ensure_node_edit_session(annotation_id)
        after = self._apply_annotation_coord_ref(after)
        self._push_commands_with_mask_patch(
            [UpdateGeometryCommand(annotation_id, before, after)],
            affected_bbox=None,
            update_mask=False,
        )
        self._node_edit_session_dirty = True
        self._set_dirty(True)
        self._refresh_canvas()

    def _on_draft_changed(self, draft_type: str, points) -> None:
        if draft_type == "clear":
            self.canvas.update_draft(None)
            return
        if draft_type == "selection_box":
            self.canvas.update_draft(points, color_name="#64748b", fill_alpha=10)
        else:
            self.canvas.update_draft(points, color_name=self._active_label_color())

    def _on_snap_indicator_changed(self, snap_type, position) -> None:
        self.canvas.update_snap_indicator(snap_type, position)

    def _set_preview_vector_visibility(self, visible: bool, user_initiated: bool = False) -> None:
        # 运行时矢量预览入口暂时关闭，仅保留代码以便后续恢复。
        self.project.layer_visibility["preview_vector"] = False
        self._preview_vector_user_enabled = False

    def _preview_result_touches_roi_boundary(self, preview, roi_width: int, roi_height: int) -> bool:
        bx, by, bw, bh = preview.bbox
        if bw <= 0 or bh <= 0:
            return False
        return bx <= 0 or by <= 0 or bx + bw >= roi_width or by + bh >= roi_height

    def _build_magic_preview_result(self, x: int, y: int):
        if self.current_source is None or self.project.image_asset is None:
            return None, None, None
        params = self.magic_panel.params()
        full_rgb = self._ensure_full_analysis_rgb()
        can_run_full = (
            full_rgb is not None
            and int(full_rgb.shape[0]) * int(full_rgb.shape[1]) <= 4_000_000
        )
        if can_run_full:
            QApplication.processEvents()
            if self._magic_cancel_requested:
                return None, None, {"cancelled": True}
            prepared = self._prepare_magic_analysis_image(
                full_rgb,
                0,
                0,
                self.project.image_asset.width,
                self.project.image_asset.height,
                params,
            )
            preview = self.segmenter.run_prepared(prepared, (x, y), params)
            if preview.bbox[2] <= 0 or preview.bbox[3] <= 0:
                return None, None, {
                    "filtered_by_min_area": bool(preview.filtered_by_min_area),
                    "pixel_area": int(preview.pixel_area),
                    "min_area": int(params.min_area),
                }
            self._remember_magic_roi_radius(x, y, params, max(self.project.image_asset.width, self.project.image_asset.height))
            return preview.mask.astype(np.uint8), preview.bbox, {
                "filtered_by_min_area": bool(preview.filtered_by_min_area),
                "pixel_area": int(preview.pixel_area),
                "min_area": int(params.min_area),
            }

        radius = self._initial_magic_roi_radius(x, y, params)
        max_side = self._analysis_max_roi_side()
        image_width = self.project.image_asset.width
        image_height = self.project.image_asset.height
        while True:
            QApplication.processEvents()
            if self._magic_cancel_requested:
                return None, None, {"cancelled": True}
            radius = min(radius, max_side)
            x0 = max(0, x - radius)
            y0 = max(0, y - radius)
            x1 = min(image_width, x + radius)
            y1 = min(image_height, y + radius)
            width = x1 - x0
            height = y1 - y0
            roi_rgb = self._get_analysis_rgb_roi(x0, y0, width, height)
            QApplication.processEvents()
            if self._magic_cancel_requested:
                return None, None, {"cancelled": True}
            prepared = self._prepare_magic_analysis_image(roi_rgb, x0, y0, width, height, params)
            preview = self.segmenter.run_prepared(prepared, (x - x0, y - y0), params)
            if preview.bbox[2] <= 0 or preview.bbox[3] <= 0:
                return None, None, {
                    "filtered_by_min_area": bool(preview.filtered_by_min_area),
                    "pixel_area": int(preview.pixel_area),
                    "min_area": int(params.min_area),
                }
            if (
                self._preview_result_touches_roi_boundary(preview, roi_rgb.shape[1], roi_rgb.shape[0])
                and max(x1 - x0, y1 - y0) < max_side
                and (x0 > 0 or y0 > 0 or x1 < image_width or y1 < image_height)
            ):
                radius = min(radius * 2, max_side)
                continue
            bx, by, bw, bh = preview.bbox
            self._remember_magic_roi_radius(x, y, params, max(x - x0, y - y0, x1 - x, y1 - y))
            return preview.mask.astype(np.uint8), (x0 + bx, y0 + by, bw, bh), {
                "filtered_by_min_area": bool(preview.filtered_by_min_area),
                "pixel_area": int(preview.pixel_area),
                "min_area": int(params.min_area),
            }

    def _ensure_preview_polygons(self, source_tool: str = "magic_wand_preview", force: bool = False):
        if (
            self.preview_selection is None
            or self._preview_mask is None
            or self._preview_bbox is None
        ):
            return []
        if self.preview_selection.polygon_preview:
            return self.preview_selection.polygon_preview
        # 这里始终只对当前预览的局部 mask+bbox 做矢量化，不会回落到整图 mask。
        preview = self._build_preview_from_mask(
            self._preview_mask,
            self._preview_bbox,
            label_id=self.project.active_label_id,
            source_tool=source_tool,
            force=force,
        )
        polygons = preview.polygon_preview if preview else []
        self.preview_selection.polygon_preview = polygons
        self.preview_selection.contours = [item.exterior for item in polygons]
        return polygons

    def _refresh_preview_vector(
        self,
        source_tool: str = "magic_wand_preview",
        *,
        show_progress: bool = False,
        progress_message: str = "正在将Mask矢量化...",
        force: bool = False,
    ):
        if (
            self.preview_selection is None
            or self._preview_mask is None
            or self._preview_bbox is None
        ):
            return []
        should_show = force or self.project.layer_visibility.get("preview_vector", False)
        if not should_show:
            self.preview_selection.polygon_preview = []
            self.preview_selection.contours = []
            return []
        if show_progress:
            self._start_progress(progress_message, maximum=100)
        try:
            if show_progress:
                self._update_progress(30, "正在将 Mask 矢量化...", maximum=100)
            polygons = self._ensure_preview_polygons(source_tool, force=True)
            self._update_preview_display()
            if show_progress:
                self._finish_progress("矢量预览已更新")
            return polygons
        except Exception:
            if show_progress:
                self._fail_progress("矢量预览生成失败")
            raise

    def _run_magic_wand_preview(self, x: int, y: int) -> None:
        if self.current_source is None or self.project.image_asset is None:
            return
        if self._magic_cancel_requested:
            self._magic_cancel_requested = False
            self._finish_progress("魔法棒识别已取消")
            return
        self._current_magic_seed = (int(x), int(y))
        full_width = self.project.image_asset.width
        full_height = self.project.image_asset.height
        if not (0 <= x < full_width and 0 <= y < full_height):
            return
        self._start_progress("正在识别魔法棒选区...", maximum=100)
        self._ensure_preview_mask_layer_visible_for_magic()
        if self._last_magic_seed != (x, y):
            self._set_preview_vector_visibility(False, user_initiated=False)
        self._magic_preview_in_progress = True
        try:
            self._update_progress(20, "正在计算局部识别区域...", maximum=100)
            mapped_mask, mapped_bbox, preview_info = self._build_magic_preview_result(int(np.floor(x)), int(np.floor(y)))
            if self._magic_cancel_requested:
                self._magic_cancel_requested = False
                self._finish_progress("魔法棒识别已取消")
                return
            if mapped_mask is None or mapped_bbox is None:
                self._current_magic_seed = None
                if preview_info and preview_info.get("cancelled"):
                    self._magic_cancel_requested = False
                    self._finish_progress("魔法棒识别已取消")
                    return
                if preview_info and preview_info.get("filtered_by_min_area"):
                    area = int(preview_info.get("pixel_area", 0))
                    min_area = int(preview_info.get("min_area", self.magic_panel.params().min_area))
                    self._finish_progress(f"识别区域像素数 {area} 小于最小面积阈值 {min_area}，已忽略")
                else:
                    self._finish_progress("没有识别到有效选区，可能是阈值太小或者最小面积参数太大了")
                return
            self._update_progress(70, "正在合并预览 Mask...", maximum=100)
            if self.magic_panel.merge_preview_enabled():
                if not self._merge_preview_has_seed((x, y)):
                    self._push_preview_history()
                self._upsert_merge_preview_entry((x, y), mapped_mask, mapped_bbox)
                self._rebuild_merge_preview_from_entries()
            else:
                self._clear_preview_history()
                self._merge_preview_entries = []
                self._preview_mask = mapped_mask
                self._preview_bbox = mapped_bbox
            if self._magic_cancel_requested:
                self._magic_cancel_requested = False
                self._current_magic_seed = None
                self._finish_progress("魔法棒识别已取消")
                return
            self.preview_selection = PreviewSelection(
                seed_point=self._current_magic_seed or (x, y),
                params=self.magic_panel.params(),
                bbox=self._preview_bbox,
                mask=self._preview_mask,
                contours=[],
                polygon_preview=[],
                pixel_area=int(preview_info.get("pixel_area", 0) if preview_info else 0),
                filtered_by_min_area=bool(preview_info.get("filtered_by_min_area", False) if preview_info else False),
            )
            self._last_magic_seed = (x, y)
            if self.preview_selection:
                self._update_progress(95, "正在刷新预览显示...", maximum=100)
                self._update_preview_display()
            if self._magic_cancel_requested:
                self._magic_cancel_requested = False
                self._finish_progress("魔法棒识别已取消")
            else:
                self._finish_progress("魔法棒识别完成")
        except Exception:
            self._fail_progress("魔法棒识别失败")
            raise
        finally:
            self._magic_preview_in_progress = False

    def _schedule_magic_preview(self, _params) -> None:
        if self._current_magic_seed is None or self.tool_controller.active_tool != SegmentationToolController.TOOL_MAGIC_WAND:
            return
        self._magic_preview_timer.start()

    def _trigger_pending_magic_preview(self) -> None:
        if self._current_magic_seed is None:
            return
        self._run_magic_wand_preview(*self._current_magic_seed)

    def _on_merge_preview_changed(self, enabled: bool) -> None:
        self._sync_magic_panel_state_to_project(mark_dirty=True)
        if not enabled:
            self._merge_preview_entries = []
            self._clear_preview_history()
            self._preview_mask = None
            self._preview_bbox = None
            self.preview_selection = None
            self._update_preview_display()

    def _ensure_preview_mask_layer_visible_for_magic(self) -> None:
        """预览 Mask 是临时交互叠加，不在图层面板中提供可见性开关。"""
        return

    def _snapshot_preview_state(self) -> dict:
        entries = []
        for item in self._merge_preview_entries:
            entries.append(
                {
                    "seed": tuple(item["seed"]),
                    "mask": np.asarray(item["mask"], dtype=np.uint8).copy(),
                    "bbox": tuple(item["bbox"]),
                }
            )
        return {
            "entries": entries,
            "preview_mask": None if self._preview_mask is None else np.asarray(self._preview_mask, dtype=np.uint8).copy(),
            "preview_bbox": None if self._preview_bbox is None else tuple(self._preview_bbox),
            "last_seed": self._last_magic_seed,
        }

    def _restore_preview_state(self, state: dict) -> None:
        self._merge_preview_entries = [
            {
                "seed": tuple(item["seed"]),
                "mask": np.asarray(item["mask"], dtype=np.uint8).copy(),
                "bbox": tuple(item["bbox"]),
            }
            for item in state.get("entries", [])
        ]
        self._preview_mask = state.get("preview_mask")
        if self._preview_mask is not None:
            self._preview_mask = np.asarray(self._preview_mask, dtype=np.uint8).copy()
        self._preview_bbox = state.get("preview_bbox")
        self._last_magic_seed = state.get("last_seed")
        if self._preview_mask is not None and self._preview_bbox is not None:
            self.preview_selection = PreviewSelection(
                seed_point=self._last_magic_seed or (0, 0),
                params=self.magic_panel.params(),
                bbox=self._preview_bbox,
                mask=self._preview_mask,
                contours=[],
                polygon_preview=[],
            )
        else:
            self.preview_selection = None
        self._update_preview_display()
        self._refresh_canvas()

    def _push_preview_history(self) -> None:
        self._preview_undo_stack.append(self._snapshot_preview_state())
        self._preview_redo_stack.clear()

    def _clear_preview_history(self) -> None:
        self._preview_undo_stack.clear()
        self._preview_redo_stack.clear()

    def _undo_preview_state(self) -> bool:
        if not self._preview_undo_stack:
            return False
        self._preview_redo_stack.append(self._snapshot_preview_state())
        state = self._preview_undo_stack.pop()
        self._restore_preview_state(state)
        if self._preview_mask is None:
            self._current_magic_seed = None
        self.status_label.setText("已撤销一次预览Mask")
        return True

    def _redo_preview_state(self) -> bool:
        if not self._preview_redo_stack:
            return False
        self._preview_undo_stack.append(self._snapshot_preview_state())
        state = self._preview_redo_stack.pop()
        self._restore_preview_state(state)
        self.status_label.setText("已重做一次预览Mask")
        return True

    def _upsert_merge_preview_entry(self, seed: tuple[int, int], mask: np.ndarray, bbox: tuple[int, int, int, int]) -> None:
        for item in self._merge_preview_entries:
            if item["seed"] == seed:
                item["mask"] = np.asarray(mask, dtype=np.uint8).copy()
                item["bbox"] = tuple(bbox)
                return
        self._merge_preview_entries.append(
            {
                "seed": tuple(seed),
                "mask": np.asarray(mask, dtype=np.uint8).copy(),
                "bbox": tuple(bbox),
            }
        )

    def _merge_preview_has_seed(self, seed: tuple[int, int]) -> bool:
        return any(item.get("seed") == tuple(seed) for item in self._merge_preview_entries)

    def _rebuild_merge_preview_from_entries(self) -> None:
        merged_mask = None
        merged_bbox = None
        for item in self._merge_preview_entries:
            if merged_mask is None or merged_bbox is None:
                merged_mask = np.asarray(item["mask"], dtype=np.uint8).copy()
                merged_bbox = tuple(item["bbox"])
            else:
                merged_mask, merged_bbox = GeometryService.merge_mask_bbox(
                    merged_mask,
                    merged_bbox,
                    np.asarray(item["mask"], dtype=np.uint8),
                    tuple(item["bbox"]),
                    "add",
                )
        self._preview_mask = merged_mask
        self._preview_bbox = merged_bbox

    def _confirm_magic_preview(self) -> None:
        if not self.preview_selection or self.project.active_label_id is None or self._preview_mask is None or self._preview_bbox is None:
            return
        self._start_progress("正在确认魔法棒预览...", maximum=100)
        try:
            self._update_progress(20, "正在准备 Mask 预览...", maximum=100)
            if self._preview_mask is None or self._preview_bbox is None:
                self._clear_magic_preview()
                self._finish_progress("没有可确认的预览结果")
                return

            self._update_progress(80, "正在写入 Mask...", maximum=100)
            effective_mask = self._preview_mask_without_overlap(self._preview_mask, self._preview_bbox)
            if effective_mask is None:
                self._clear_magic_preview()
                self._finish_progress("识别结果与已有Mask重叠，未新增像素")
                return
            self._apply_binary_preview_mask(effective_mask, self._preview_bbox, self.project.active_label_id)
            self._clear_magic_preview()
            self._refresh_canvas()
            self._set_dirty(True)
            self._finish_progress("魔法棒预览已确认")
        except Exception:
            self._fail_progress("确认魔法棒预览失败")
            raise

    def _clear_magic_preview(self) -> None:
        self._merge_preview_entries = []
        self._clear_preview_history()
        self.preview_selection = None
        self._current_magic_seed = None
        self._last_magic_seed = None
        self._preview_mask = None
        self._preview_bbox = None
        self._update_preview_display()
        self._refresh_canvas()

    def _preview_mask_without_overlap(self, mask: np.ndarray | None, bbox: tuple[int, int, int, int] | None) -> np.ndarray | None:
        if mask is None or bbox is None:
            return None
        if self.project.mask_data is None:
            return np.asarray(mask, dtype=np.uint8).copy()
        x, y, width, height = bbox
        if width <= 0 or height <= 0:
            return None
        h, w = self.project.mask_data.shape
        x0 = max(0, int(x))
        y0 = max(0, int(y))
        x1 = min(w, int(x + width))
        y1 = min(h, int(y + height))
        if x0 >= x1 or y0 >= y1:
            return None
        work = np.asarray(mask, dtype=np.uint8).copy()
        local = work[y0 - y:y1 - y, x0 - x:x1 - x]
        occupied = self.project.mask_data[y0:y1, x0:x1] > 0
        local[occupied] = 0
        work[y0 - y:y1 - y, x0 - x:x1 - x] = local
        return work if np.any(work > 0) else None

    def _layer_visibility_callback(self, layer_name: str, visible: bool) -> None:
        self.project.layer_visibility[layer_name] = visible
        for window_id in self.workspace.window_ids:
            canvas = self._canvas_for_window(window_id)
            if canvas is None or not canvas.layer_manager.layer(layer_name):
                continue
            final_visible = bool(visible and self._layer_visible_in_window(layer_name, window_id))
            canvas.set_layer_visible(layer_name, final_visible)
            self.layer_panel.set_window_visibility(
                layer_name,
                window_id,
                self._layer_visible_in_window(layer_name, window_id) if visible else False,
            )
        if layer_name == "preview_vector" and visible:
            self._refresh_preview_vector(force=True)

    def _layer_order_callback(self, layer_name: str, index: int) -> None:
        for canvas in self._all_canvases():
            if canvas is self.canvas:
                continue
            if canvas.layer_manager.layer(layer_name):
                canvas.move_layer(layer_name, index)

    def _layer_opacity_callback(self, layer_name: str, opacity: float) -> None:
        if not isinstance(self.project.export_prefs, dict):
            self.project.export_prefs = {}
        self.project.export_prefs.setdefault("layer_opacity", {})[layer_name] = float(opacity)
        for canvas in self._all_canvases():
            if canvas is self.canvas:
                continue
            if canvas.layer_manager.layer(layer_name):
                canvas.set_layer_opacity(layer_name, float(opacity))

    def _layer_blend_mode_callback(self, layer_name: str, blend_mode: str) -> None:
        if not isinstance(self.project.export_prefs, dict):
            self.project.export_prefs = {}
        self.project.export_prefs.setdefault("layer_blend_mode", {})[layer_name] = str(blend_mode)
        for canvas in self._all_canvases():
            if canvas is self.canvas:
                continue
            if canvas.layer_manager.layer(layer_name):
                canvas.set_layer_blend_mode(layer_name, str(blend_mode))

    def _on_layer_controller_changed(self) -> None:
        self._refresh_canvas()
        self._set_dirty(True)

    def _on_canvas_layer_rendering_changed(self, layer_id: str) -> None:
        if self.project.image_asset is None or not layer_id or layer_id == "base_raster":
            return
        target_layer = None
        for layer in self._auxiliary_layers:
            if layer.get("id") == layer_id and layer.get("type") == "raster":
                target_layer = layer
                break
        if target_layer is None:
            return
        for canvas in self._all_canvases():
            if canvas.layer_manager.layer(layer_id):
                self._refresh_aux_raster_layer(target_layer, canvas=canvas)

    def _on_layer_selected(self, layer_id: str | None) -> None:
        self._selected_render_layer_id = layer_id
        if not isinstance(self.project.export_prefs, dict):
            self.project.export_prefs = {}
        self.project.export_prefs["selected_layer"] = layer_id
        if hasattr(self, "render_sidebar_controller"):
            self.render_sidebar_controller.refresh()

    def _layer_bbox(self, layer_name: str) -> tuple[float, float, float, float] | None:
        if self.project.image_asset is None:
            return None
        for layer in self._auxiliary_layers:
            if layer.get("id") == layer_name:
                bbox = layer.get("bbox")
                if bbox is None:
                    return None
                return tuple(float(v) for v in bbox)
        if layer_name == "base_raster":
            return (0.0, 0.0, float(self.project.image_asset.width), float(self.project.image_asset.height))
        if layer_name == "preview_mask":
            return None if self._preview_bbox is None else tuple(float(v) for v in self._preview_bbox)
        if layer_name == "mask":
            if self.project.mask_data is None or not np.any(self.project.mask_data):
                return (0.0, 0.0, float(self.project.image_asset.width), float(self.project.image_asset.height))
            ys, xs = np.where(self.project.mask_data > 0)
            x0, x1 = int(xs.min()), int(xs.max())
            y0, y1 = int(ys.min()), int(ys.max())
            return (float(x0), float(y0), float(x1 - x0 + 1), float(y1 - y0 + 1))
        if layer_name == "annotations":
            if not self.project.annotations:
                return (0.0, 0.0, float(self.project.image_asset.width), float(self.project.image_asset.height))
            bbox = GeometryService.affected_bbox_from_annotations(self.project.annotations)
            return None if bbox is None else tuple(float(v) for v in bbox)
        return (0.0, 0.0, float(self.project.image_asset.width), float(self.project.image_asset.height))

    def _remove_layer(self, layer_id: str) -> bool:
        if not is_layer_removable(layer_id):
            return False
        target = next((item for item in self._auxiliary_layers if item.get("id") == layer_id), None)
        if target is None:
            return False
        for canvas in self._all_canvases():
            try:
                canvas.remove_layer(layer_id)
            except Exception:
                pass
        self._auxiliary_layers = [item for item in self._auxiliary_layers if item.get("id") != layer_id]
        self.project.layer_visibility.pop(layer_id, None)
        self._layer_window_visibility.pop(str(layer_id), None)
        if isinstance(self.project.export_prefs, dict):
            self.project.export_prefs.get("layer_opacity", {}).pop(layer_id, None)
            self.project.export_prefs.get("layer_blend_mode", {}).pop(layer_id, None)
        self._rebuild_layer_panel_items()
        return True

    def _on_layer_nodata_changed(self, layer_id: str, value) -> None:
        if layer_id == "base_raster":
            self._base_nodata_override = value
            if not isinstance(self.project.export_prefs, dict):
                self.project.export_prefs = {}
            self.project.export_prefs["base_nodata_override"] = value
            for canvas in self._all_canvases():
                canvas.set_nodata_value(value)
            self._refresh_canvas()
            self._set_dirty(True)
            return
        target = next((item for item in self._auxiliary_layers if item.get("id") == layer_id and item.get("type") == "raster"), None)
        if target is None:
            return
        target["nodata_override"] = value
        self._refresh_canvas()
        self._set_dirty(True)

    def _edit_layer_style(self, layer_id: str) -> None:
        target = next((item for item in self._auxiliary_layers if item.get("id") == layer_id), None)
        if target is None:
            return
        if target.get("type") == "vector":
            self._edit_vector_layer_style(target)

    def _show_layer_properties(self, layer_id: str) -> None:
        target = next((item for item in self._auxiliary_layers if item.get("id") == layer_id), None)
        if target is not None:
            if target.get("type") == "raster":
                self._show_raster_layer_properties(target)
            else:
                self._show_vector_layer_properties(target)
            return
        state = self.canvas.layer_manager.layer(layer_id)
        if state is None:
            return
        text = "\n".join(
            [
                f"名称: {state.spec.name}",
                f"ID: {state.spec.id}",
                f"透明度: {int(round(float(state.spec.opacity) * 100.0))}%",
                f"叠加方式: {self._blend_mode_text(state.spec.blend_mode)}",
            ]
        )
        self._show_copyable_text_dialog("图层属性", text)

    def _edit_vector_layer_style(self, layer: dict) -> None:
        style = dict(layer.get("vector_style") or {})
        dialog = QDialog(self)
        dialog.setWindowTitle("矢量样式")
        form = QFormLayout(dialog)
        initial_color = QColor(str(style.get("color", "#22c55e")))
        if not initial_color.isValid():
            initial_color = QColor("#22c55e")
        color_button = QPushButton(initial_color.name(), dialog)
        color_button.setStyleSheet(f"background:{initial_color.name()};")
        selected_color = {"value": initial_color}

        def choose_color() -> None:
            color = QColorDialog.getColor(selected_color["value"], dialog, "选择颜色")
            if color.isValid():
                selected_color["value"] = color
                color_button.setText(color.name())
                color_button.setStyleSheet(f"background:{color.name()};")

        color_button.clicked.connect(choose_color)
        width_spin = QSpinBox(dialog)
        width_spin.setRange(1, 12)
        width_spin.setValue(max(1, int(style.get("line_width", 2))))
        fill_alpha_spin = QSpinBox(dialog)
        fill_alpha_spin.setRange(0, 255)
        fill_alpha_spin.setValue(max(0, min(255, int(style.get("fill_alpha", 50)))))
        form.addRow("颜色", color_button)
        form.addRow("线宽", width_spin)
        form.addRow("填充透明度(0-255)", fill_alpha_spin)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.Accepted:
            return
        style["color"] = selected_color["value"].name()
        style["line_width"] = int(width_spin.value())
        style["fill_alpha"] = int(fill_alpha_spin.value())
        layer["vector_style"] = style
        self._refresh_canvas()
        self._set_dirty(True)

    def _show_raster_layer_properties(self, layer: dict) -> None:
        source = layer.get("source")
        if source is None:
            return
        meta = source.metadata()
        min_val, max_val = None, None
        try:
            min_val, max_val = source.band_minmax(1)
        except Exception:
            pass
        state = self.canvas.layer_manager.layer(layer.get("id"))
        text = [
            f"名称: {layer.get('name', '')}",
            f"路径: {layer.get('path', '')}",
            f"尺寸: {meta.width} x {meta.height}",
            f"是否有地理信息: {'是' if meta.has_georef else '否'}",
            f"CRS: {self._crs_brief(meta.crs_wkt)}",
            f"分辨率: {meta.resolution or '-'}",
            f"波段数: {meta.band_count}",
            f"数据类型: {meta.dtype}",
            f"NoData: {nodata_to_text(layer.get('nodata_override', meta.nodata))}",
            f"Min/Max: {min_val if min_val is not None else '-'} / {max_val if max_val is not None else '-'}",
            f"透明度: {int(round(float(state.spec.opacity) * 100.0)) if state is not None else '-'}%",
            f"叠加方式: {self._blend_mode_text(state.spec.blend_mode) if state is not None else '-'}",
        ]
        self._show_copyable_text_dialog("栅格属性", "\n".join(text))

    def _show_vector_layer_properties(self, layer: dict) -> None:
        state = self.canvas.layer_manager.layer(layer.get("id"))
        geom_text = str(layer.get("geometry_type_name", layer.get("geometry_type", "-")))
        fields = layer.get("fields") or []
        field_lines = [f"- {item.get('name')}: {item.get('type')}" for item in fields]
        text = [
            f"名称: {layer.get('name', '')}",
            f"路径: {layer.get('path', '')}",
            f"几何类型: {geom_text}",
            f"要素数量: {layer.get('feature_count', '-')}",
            f"CRS: {self._crs_brief(layer.get('crs_wkt'))}",
            f"Extent: {layer.get('extent', '-')}",
            f"透明度: {int(round(float(state.spec.opacity) * 100.0)) if state is not None else '-'}%",
            f"叠加方式: {self._blend_mode_text(state.spec.blend_mode) if state is not None else '-'}",
            "字段列表:",
            *(field_lines or ["- 无"]),
        ]
        self._show_copyable_text_dialog("矢量属性", "\n".join(text))

    def _show_copyable_text_dialog(self, title: str, text: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(620, 460)
        layout = QVBoxLayout(dialog)
        viewer = QPlainTextEdit(dialog)
        viewer.setReadOnly(True)
        viewer.setPlainText(text)
        layout.addWidget(viewer)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, dialog)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _crs_brief(self, crs_wkt: str | None) -> str:
        text = (crs_wkt or "").strip()
        if not text:
            return "-"
        epsg = re.search(r'AUTHORITY\["EPSG","(\d+)"\]', text)
        name_match = re.search(r'^(?:PROJCRS|GEOGCRS|PROJCS|GEOGCS)\["([^"]+)"', text)
        epsg_text = f"EPSG:{epsg.group(1)}" if epsg else ""
        name_text = name_match.group(1) if name_match else ""
        if epsg_text and name_text:
            return f"{epsg_text} | {name_text}"
        if epsg_text:
            return epsg_text
        if name_match:
            return name_match.group(1)
        return text[:120] + ("..." if len(text) > 120 else "")

    def _blend_mode_text(self, mode: str | None) -> str:
        mapping = {
            "source_over": "正常",
            "multiply": "正片叠底",
            "screen": "滤色",
            "overlay": "叠加",
            "plus": "线性加亮",
        }
        return mapping.get(str(mode or "source_over"), str(mode or "source_over"))

    def _selected_annotation(self) -> AnnotationObject | None:
        selected_id = self.tool_controller.selected_annotation_id
        if selected_id is None:
            return None
        for annotation in self.project.annotations:
            if annotation.id == selected_id:
                return annotation
        return None

    def _find_annotation_by_id(self, annotation_id: str | None) -> AnnotationObject | None:
        if annotation_id is None:
            return None
        for annotation in self.project.annotations:
            if annotation.id == annotation_id:
                return annotation
        return None

    def _set_controller_selection(self, selection_ids: set[str]) -> None:
        self._suspend_selection_sync = True
        self.tool_controller.selected_annotation_ids = set(selection_ids)
        self.tool_controller.selected_annotation_id = next(iter(selection_ids), None)
        if len(selection_ids) != 1:
            self.tool_controller.selected_vertex_index = None
        self.tool_controller.selection_changed.emit(set(selection_ids))
        self._suspend_selection_sync = False

    def _clear_node_edit_session_state(self, clear_override: bool = False) -> None:
        self._node_edit_session_annotation_id = None
        self._node_edit_original_annotation = None
        self._node_edit_session_dirty = False
        self._node_edit_session_undo_depth = self.command_stack.undo_depth()
        self._node_edit_mask_snapshot = None

    def _ensure_node_edit_session(self, annotation_id: str | None) -> None:
        if annotation_id is None:
            return
        if self._node_edit_session_annotation_id == annotation_id and self._node_edit_original_annotation is not None:
            return
        annotation = self._find_annotation_by_id(annotation_id)
        if annotation is None:
            return
        self._node_edit_session_annotation_id = annotation_id
        self._node_edit_original_annotation = annotation.clone()
        self._node_edit_session_dirty = False
        self._node_edit_session_undo_depth = self.command_stack.undo_depth()
        self._node_edit_mask_snapshot = None if self.project.mask_data is None else self.project.mask_data.copy()

    def _finish_node_edit_session(self) -> bool:
        annotation_id = self._node_edit_session_annotation_id
        original = self._node_edit_original_annotation
        if annotation_id is None or original is None:
            self._clear_node_edit_session_state()
            return True
        current = self._find_annotation_by_id(annotation_id)
        if current is None:
            self._clear_node_edit_session_state()
            return True
        if not self._node_edit_session_dirty or current.to_dict() == original.to_dict():
            self._clear_node_edit_session_state()
            return True
        save_reply = QMessageBox.question(
            self,
            "保存节点编辑",
            "检测到当前对象的节点已编辑。是否保存矢量修改？\n选择“否”将恢复编辑前的几何。",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes,
        )
        if save_reply == QMessageBox.Cancel:
            return False
        if save_reply == QMessageBox.No:
            while self.command_stack.undo_depth() > self._node_edit_session_undo_depth:
                if not self.command_stack.undo():
                    break
            self.command_stack.clear_redo()
            self.tool_controller.set_annotations(self.project.annotations)
            self.project.mask_data = None if self._node_edit_mask_snapshot is None else self._node_edit_mask_snapshot.copy()
            self._clear_node_edit_session_state()
            self._refresh_canvas()
            return True
        self._set_dirty(True)
        mask_reply = QMessageBox.question(
            self,
            "更新 Mask",
            "是否根据保存后的矢量对象更新当前 Mask 显示？\n选择“否”将保留编辑前的 Mask 结果。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if mask_reply == QMessageBox.Yes:
            self._update_mask_patch_for_annotation_edit(original, current)
        else:
            self.project.mask_data = None if self._node_edit_mask_snapshot is None else self._node_edit_mask_snapshot.copy()
        self.tool_controller.set_annotations(self.project.annotations)
        self._clear_node_edit_session_state()
        self._refresh_canvas()
        return True

    def _refresh_canvas(self) -> None:
        label_lookup = {label.id: label for label in self.project.labels}
        annotations = self.project.annotations if self.project.layer_visibility.get("annotations", True) else []
        raster_rgba, raster_bbox = self._current_raster_overlay(label_lookup)
        for window_id in self.workspace.window_ids:
            canvas = self._canvas_for_window(window_id)
            if canvas is None:
                continue
            draw_annotations = annotations
            pixel_mode_non_primary = (
                self.project.coordinate_mode == "pixel"
                and window_id != self.project.primary_window_id
            )
            if pixel_mode_non_primary and draw_annotations:
                draw_annotations = []
            canvas.update_annotations(
                draw_annotations,
                label_lookup,
                self.tool_controller.selected_annotation_ids,
                editable_annotation_id=self.tool_controller.editable_annotation_id(),
                active_vertex=self.tool_controller.selected_vertex_index,
            )
            canvas.update_raster_mask(raster_rgba, raster_bbox)
        self._update_mask_selection_display()
        self._refresh_auxiliary_layers()
        self._update_preview_display()
        for layer_id, vis_map in self._layer_window_visibility.items():
            for window_id, visible in vis_map.items():
                canvas = self._canvas_for_window(window_id)
                if canvas is None or not canvas.layer_manager.layer(layer_id):
                    continue
                global_visible = bool(self.project.layer_visibility.get(layer_id, True))
                canvas.set_layer_visible(layer_id, bool(visible and global_visible))
        if self.project.coordinate_mode == "pixel":
            self.status_label.setText("像素坐标模式：仅主窗口保证标注精确显示")

    def _refresh_auxiliary_layers(self) -> None:
        for canvas in self._all_canvases():
            for layer in self._auxiliary_layers:
                layer_id = layer.get("id")
                if not layer_id:
                    continue
                if layer.get("type") == "raster":
                    self._refresh_aux_raster_layer(layer, canvas=canvas)
                elif layer.get("type") == "vector":
                    style = dict(layer.get("vector_style") or {})
                    canvas.set_vector_overlay(
                        layer_id,
                        layer.get("annotations", []),
                        {
                            "color": style.get("color", "#22c55e"),
                            "line_width": style.get("line_width", 2),
                            "fill_alpha": style.get("fill_alpha", 50),
                        },
                        name=layer.get("name", layer_id),
                    )

    def _refresh_aux_raster_layer(self, layer: dict, *, canvas=None) -> None:
        canvas = canvas or self.canvas
        source = layer.get("source")
        bbox = layer.get("bbox")
        if source is None or bbox is None:
            canvas.set_raster_overlay(layer.get("id"), None, None, name=layer.get("name"))
            return
        meta = source.metadata()
        sample_w = min(int(meta.width), 2048)
        sample_h = min(int(meta.height), 2048)
        if sample_w <= 0 or sample_h <= 0:
            canvas.set_raster_overlay(layer.get("id"), None, None, name=layer.get("name"))
            return
        from src.rendering.models import RenderRequest
        layer_request = RenderRequest(
            x=0.0,
            y=0.0,
            width=float(meta.width),
            height=float(meta.height),
            screen_width=max(1, sample_w),
            screen_height=max(1, sample_h),
        )
        layer_state = canvas.layer_manager.layer(str(layer.get("id")))
        current_layer = None if layer_state is None else layer_state.layer
        render_style = None if current_layer is None else current_layer.render_style
        display_settings = None if current_layer is None else current_layer.display_settings
        render_cfg = (
            style_to_legacy_config(render_style, display_settings)
            if render_style is not None and display_settings is not None
            else layer.get("render_config") or default_raster_render_config(meta.band_count, bool(meta.has_color_table))
        )
        try:
            style = render_style or legacy_config_to_style(render_cfg, meta)
            resolved_display_settings = display_settings or default_display_settings(nodata_value=layer.get("nodata_override", meta.nodata))
            resolved_display_settings = replace(
                resolved_display_settings,
                visible=bool(self.project.layer_visibility.get(str(layer.get("id")), True)),
            )
            canvas.layer_manager.update_raster_layer(
                str(layer.get("id")),
                source=source,
                metadata=meta,
                render_style=style,
                display_settings=resolved_display_settings,
                custom_properties={"auxiliary_layer": True},
            )
        except Exception:
            pass
        result = source.render(layer_request, render_cfg)
        rgb = np.asarray(result.display_rgb)
        if rgb.ndim == 2:
            rgb = np.repeat(rgb[:, :, None], 3, axis=2)
            intrinsic_alpha = None
        elif rgb.ndim == 3 and rgb.shape[2] == 1:
            rgb = np.repeat(rgb, 3, axis=2)
            intrinsic_alpha = None
        elif rgb.ndim == 3 and rgb.shape[2] >= 4:
            intrinsic_alpha = np.asarray(rgb[:, :, 3], dtype=np.uint8)
            rgb = rgb[:, :, :3]
        elif rgb.ndim == 3 and rgb.shape[2] >= 3:
            intrinsic_alpha = None
            rgb = rgb[:, :, :3]
        else:
            canvas.set_raster_overlay(layer.get("id"), None, None, name=layer.get("name"))
            return
        if rgb.dtype != np.uint8:
            rgb = np.clip(rgb, 0, 255).astype(np.uint8, copy=False)
        raw = result.raw_array
        nodata_value = layer.get("nodata_override", meta.nodata)
        alpha = np.full((rgb.shape[0], rgb.shape[1], 1), 255, dtype=np.uint8)
        if intrinsic_alpha is not None:
            alpha[:, :, 0] = intrinsic_alpha
        if nodata_value is not None and raw is not None:
            arr = np.asarray(raw)
            if arr.ndim == 3:
                arr = arr[:, :, 0]
            try:
                mask = np.isnan(arr) if np.isnan(nodata_value) else (arr == nodata_value)
            except Exception:
                mask = arr == nodata_value
            if mask.shape == alpha[:, :, 0].shape:
                alpha[mask] = 0
        rgba = np.concatenate([np.asarray(rgb, dtype=np.uint8), alpha], axis=2)
        canvas.set_raster_overlay(
            layer.get("id"),
            rgba,
            tuple(float(v) for v in bbox),
            name=layer.get("name", layer.get("id")),
            opacity=1.0,
        )

    def _autosave_if_needed(self) -> None:
        if not self._dirty:
            return
        if not self.current_project_path or (self._autosave_thread is not None and self._autosave_thread.isRunning()):
            return
        if self._last_edit_timestamp and (time.monotonic() - self._last_edit_timestamp) < 60.0:
            self.autosave_timer.start()
            return
        project_snapshot = copy.deepcopy(self.project)
        self._autosave_thread = QThread(self)
        self._autosave_worker = AutosaveWorker(self.project_manager, project_snapshot, self.current_project_path)
        self._autosave_worker.moveToThread(self._autosave_thread)
        self._autosave_thread.started.connect(self._autosave_worker.run)
        self._autosave_worker.finished.connect(lambda *_: self._autosave_thread.quit())
        self._autosave_worker.finished.connect(self._on_autosave_finished)
        self._autosave_worker.finished.connect(self._autosave_worker.deleteLater)
        self._autosave_thread.finished.connect(self._autosave_thread.deleteLater)
        self._autosave_thread.start()

    def _on_autosave_finished(self, _success: bool, _message: str) -> None:
        self._autosave_worker = None
        self._autosave_thread = None

    def _current_raster_overlay(self, label_lookup):
        if not self.project.image_asset or not self.canvas.last_render or not self.project.layer_visibility.get("mask", True):
            return None, None
        x0, y0, width, height = self.canvas.last_render.source_window
        if width <= 0 or height <= 0:
            return None, None
        source_mask = self.project.mask_data
        if source_mask is None:
            self._ensure_project_mask_shape()
            source_mask = self.project.mask_data
        label_signature = tuple(
            (label.id, label.color, bool(label.visible))
            for label in self.project.labels
        )
        cache_key = (
            (x0, y0, width, height),
            self._mask_overlay_revision,
            label_signature,
        )
        if not self._project_mask_has_foreground():
            self._raster_overlay_cache_key = cache_key
            self._raster_overlay_cache_value = (None, None)
            return None, None
        if cache_key == self._raster_overlay_cache_key:
            cached_rgba, cached_bbox = self._raster_overlay_cache_value
            if cached_rgba is None:
                return None, None
            return cached_rgba, cached_bbox
        x1 = min(x0 + width, source_mask.shape[1])
        y1 = min(y0 + height, source_mask.shape[0])
        clipped_mask = source_mask[y0:y1, x0:x1]
        if clipped_mask.size == 0 or not np.any(clipped_mask):
            self._raster_overlay_cache_key = cache_key
            self._raster_overlay_cache_value = (None, None)
            return None, None
        raster_rgba = GeometryService.colorize_mask(clipped_mask.astype(np.uint16), label_lookup)
        self._raster_overlay_cache_key = cache_key
        self._raster_overlay_cache_value = (raster_rgba, (x0, y0, clipped_mask.shape[1], clipped_mask.shape[0]))
        return raster_rgba, (x0, y0, clipped_mask.shape[1], clipped_mask.shape[0])

    def _on_view_state_changed(self, state) -> None:
        self.tool_controller.set_view_state(state)
        if self.project.image_asset is None:
            return
        if self._updating_view_overlays:
            return
        self.project.display_state = DisplayState(
            zoom=self.project.display_state.zoom,
            center=(float(state.center_x), float(state.center_y)),
            center_x=float(state.center_x),
            center_y=float(state.center_y),
            scale_x=float(state.scale_x),
            scale_y=float(state.scale_y),
            viewport_width=float(state.viewport_width),
            viewport_height=float(state.viewport_height),
            show_image=self.project.display_state.show_image,
            show_annotations=self.project.display_state.show_annotations,
            show_raster=self.project.display_state.show_raster,
            show_preview=self.project.display_state.show_preview,
            show_preview_vector=self.project.display_state.show_preview_vector,
            show_preview_mask=self.project.display_state.show_preview_mask,
        )
        # 此信号由 SegmentationCanvas.refresh_view() 在底图瓦片替换完成后发出。
        # Mask 仅保存该瓦片对应 source_window 内的像素；若再延迟更新，就会在
        # 新底图与旧 Mask 裁剪窗口之间出现一帧（或数百毫秒）的截断区域。
        # 因此这里必须与底图同轮同步替换 Mask，而不是合并为延迟刷新。
        self._apply_view_overlay_update()

    def _apply_view_overlay_update(self) -> None:
        if self.project.image_asset is None:
            return
        if self._updating_view_overlays:
            return
        self._updating_view_overlays = True
        try:
            label_lookup = {label.id: label for label in self.project.labels}
            raster_rgba, raster_bbox = self._current_raster_overlay(label_lookup)
            self.canvas.update_raster_mask(raster_rgba, raster_bbox)
            self._update_preview_display()
        finally:
            self._updating_view_overlays = False

    def _ensure_window2_ready(self) -> None:
        viewer1 = self._canvas_for_window("viewer_1")
        viewer2 = self._canvas_for_window("viewer_2")
        if viewer1 is None or viewer2 is None:
            return
        viewer2.restore_view_state(viewer1.capture_view_state())
        viewer1.refresh_view()
        viewer2.refresh_view()

    def _restore_workspace_view_state_after_toggle(self, state: dict, window_count: int) -> None:
        # 切换 splitter 后会经历一次异步布局，做“立即 + 稍后”两次恢复，防止视图范围被二次挤压。
        QTimer.singleShot(0, lambda s=state, count=window_count: self._restore_workspace_view_state_after_layout(s, count))
        QTimer.singleShot(90, lambda s=state, count=window_count: self._restore_workspace_view_state_after_layout(s, count))
        QTimer.singleShot(220, lambda s=state, count=window_count: self._restore_workspace_view_state_after_layout(s, count))
        QTimer.singleShot(420, lambda s=state, count=window_count: self._restore_workspace_view_state_after_layout(s, count))

    def _clear_synced_pointers(self) -> None:
        if hasattr(self, "workspace"):
            self.workspace.clear_synced_pointers()

    def _restore_workspace_view_state_after_layout(self, state: dict, window_count: int) -> None:
        if not isinstance(state, dict):
            return
        viewer1 = self._canvas_for_window("viewer_1")
        if viewer1 is not None:
            viewer1.restore_view_state(self._layout_adjusted_view_state(state, viewer1))
        if int(window_count) >= 2:
            viewer2 = self._canvas_for_window("viewer_2")
            if viewer2 is not None:
                viewer2.restore_view_state(self._layout_adjusted_view_state(state, viewer2))
        self._layout_view_states[int(window_count)] = dict(state)

    def _layout_adjusted_view_state(self, state: dict, canvas) -> dict:
        adjusted = dict(state)
        preserve_axis = adjusted.pop("_preserve_axis", None)
        if preserve_axis != "y" or "y_range" not in adjusted:
            return adjusted
        viewport = canvas.graphics.viewport()
        if viewport is None:
            return adjusted
        rect = viewport.rect()
        viewport_width = max(float(rect.width()), 1.0)
        viewport_height = max(float(rect.height()), 1.0)
        y0, y1 = adjusted["y_range"]
        half_h = max((float(y1) - float(y0)) / 2.0, 0.5)
        center_y = float((float(y0) + float(y1)) / 2.0)
        center_x = float(adjusted.get("scene_center_x", (float(adjusted["x_range"][0]) + float(adjusted["x_range"][1])) / 2.0))
        half_w = max(half_h * viewport_width / viewport_height, 0.5)
        adjusted["x_range"] = (center_x - half_w, center_x + half_w)
        adjusted["y_range"] = (center_y - half_h, center_y + half_h)
        adjusted["scene_center_x"] = center_x
        adjusted["scene_center_y"] = center_y
        return adjusted

    def _sync_display_state_from_canvas(self) -> None:
        if self.project.image_asset is None:
            return
        self._on_view_state_changed(self.canvas.current_view_state())
        self._apply_view_overlay_update()

    def _save_canvas_view_state(self) -> None:
        if not isinstance(self.project.export_prefs, dict):
            self.project.export_prefs = {}
        self.project.export_prefs["canvas_view_state"] = self.canvas.capture_view_state()

    def _restore_canvas_view_state(self) -> None:
        view_state = None
        if isinstance(self.project.export_prefs, dict):
            maybe = self.project.export_prefs.get("canvas_view_state")
            if isinstance(maybe, dict) and "x_range" in maybe and "y_range" in maybe:
                view_state = maybe
        if view_state is not None:
            self.canvas.restore_view_state(view_state)
            return
        self.canvas.restore_view_state(self.project.display_state)

    def _build_preview_from_mask(self, mask, bbox, label_id: int | None = None, source_tool: str = "magic_wand_preview", force: bool = False):
        # 运行时矢量预览入口暂时关闭，仅保留代码以便后续恢复。
        return None

    def _handle_pending_magic_session(self) -> bool:
        if self._preview_mask is None:
            return True
        reply = QMessageBox.question(
            self,
            "未确认的魔法棒结果",
            "当前存在未确认的魔法棒结果。是否先应用？\n选择“No”将取消当前结果。",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.Cancel:
            return False
        if reply == QMessageBox.Yes:
            self._confirm_magic_preview()
        else:
            self._clear_magic_preview()
        return True

    def _map_preview_to_image(self, preview, last_render, rendered_width, rendered_height, win_x, win_y, win_w, win_h):
        bx, by, bw, bh = preview.bbox
        if bw <= 0 or bh <= 0:
            return None, None
        scale_x = win_w / max(rendered_width, 1)
        scale_y = win_h / max(rendered_height, 1)
        mapped_bbox = (
            int(round(win_x + bx * scale_x)),
            int(round(win_y + by * scale_y)),
            max(int(round(bw * scale_x)), 1),
            max(int(round(bh * scale_y)), 1),
        )
        local_mask = preview.mask[by:by + bh, bx:bx + bw]
        if mapped_bbox[2] != local_mask.shape[1] or mapped_bbox[3] != local_mask.shape[0]:
            import cv2
            local_mask = cv2.resize(local_mask, (mapped_bbox[2], mapped_bbox[3]), interpolation=cv2.INTER_NEAREST)
        return local_mask.astype(np.uint8), mapped_bbox

    def _build_magic_commit_commands(self, new_annotations):
        # 运行时矢量提交入口暂时关闭，仅保留代码以便后续恢复。
        commands = []
        if not new_annotations:
            return commands, None
        clipped_annotations = []

        for annotation in new_annotations:
            polygon = GeometryService.annotation_to_polygon(annotation)
            if polygon is None or polygon.is_empty:
                continue
            intersecting_polygon = None
            for existing_annotation in self.project.annotations:
                if not GeometryService.bbox_intersects(existing_annotation.bbox, GeometryService.affected_bbox_from_annotations(annotation)):
                    continue
                existing_polygon = GeometryService.annotation_to_polygon(existing_annotation)
                if existing_polygon is None or existing_polygon.is_empty:
                    continue
                if existing_polygon.intersects(polygon):
                    intersecting_polygon = existing_polygon
                    break
            result_geometry = polygon.difference(intersecting_polygon) if intersecting_polygon is not None else polygon
            if result_geometry.is_empty:
                continue
            clipped_annotations.extend(
                GeometryService.polygon_to_annotation_objects(result_geometry, annotation.label_id, annotation.source_tool)
            )

        if not clipped_annotations:
            return [], None

        for annotation in clipped_annotations:
            commands.append(AddAnnotationCommand(annotation))
        return commands, GeometryService.affected_bbox_from_annotations(clipped_annotations)

    def _update_mouse_position(self, payload) -> None:
        if bool(payload.buttons & (Qt.LeftButton | Qt.MiddleButton | Qt.RightButton)):
            return
        if not self.project.image_asset:
            self.mouse_pos_label.setText("行: -, 列: - | 渲染RGB: - | 原值: - | Mask 标签: 无标签 | Mask 值: -")
            return
        row = int(np.floor(payload.y))
        col = int(np.floor(payload.x))
        if 0 <= row < self.project.image_asset.height and 0 <= col < self.project.image_asset.width:
            original_value = self.current_source.read_pixel(col, row) if self.current_source else None
            rendered_rgb = self.canvas.rendered_rgb_at(col, row) or self._rendered_rgb_from_original(original_value)
            mask_label_name, mask_label_value = self._mask_label_info_at(col, row)
            rgb_text = (
                f"({rendered_rgb[0]}, {rendered_rgb[1]}, {rendered_rgb[2]})"
                if rendered_rgb is not None else "-"
            )
            if isinstance(original_value, list):
                original_text = str(tuple(original_value))
            else:
                original_text = "-" if original_value is None else str(original_value)
            geo_text = ""
            if self.project.image_asset.geotransform:
                lon, lat = pixel_to_lonlat(col, row, self.project.image_asset.geotransform, self.project.image_asset.crs_wkt, use_pixel_center=True)
                if lon is not None and lat is not None:
                    geo_text = f" | 地理坐标: ({lon:.6f}, {lat:.6f})"
            self.mouse_pos_label.setText(
                f"行: {row}, 列: {col} | 渲染RGB: {rgb_text} | 原值: {original_text}"
                f" | Mask 标签: {mask_label_name} | Mask 值: {mask_label_value}{geo_text}"
            )
        else:
            self.mouse_pos_label.setText("行: -, 列: - | 渲染RGB: - | 原值: - | Mask 标签: 无标签 | Mask 值: -")

    def _mask_label_info_at(self, col: int, row: int) -> tuple[str, str]:
        if self.project.mask_data is None:
            return "无标签", "-"
        if not (0 <= row < self.project.mask_data.shape[0] and 0 <= col < self.project.mask_data.shape[1]):
            return "无标签", "-"
        label_id = int(self.project.mask_data[row, col])
        if label_id <= 0:
            return "无标签", "0"
        for label in self.project.labels:
            if label.id == label_id:
                return label.name, str(label_id)
        return "未定义标签", str(label_id)

    def _rendered_rgb_from_original(self, original_value):
        if original_value is None:
            return None
        if isinstance(original_value, list):
            raw = np.asarray(original_value).reshape(1, 1, -1)
        else:
            raw = np.asarray([[original_value]])
        rgb = render_raster_rgb(
            raw,
            self.render_config,
            nodata_value=self.project.image_asset.nodata if self.project.image_asset else None,
            color_table=getattr(self.project.image_asset, "color_table", None) if self.project.image_asset else None,
        )
        if rgb.ndim != 3 or rgb.shape[2] < 3:
            return None
        return [int(rgb[0, 0, 0]), int(rgb[0, 0, 1]), int(rgb[0, 0, 2])]

    def clear_all_annotations(self) -> None:
        if self.project.mask_data is None or not np.any(self.project.mask_data):
            return
        reply = QMessageBox.question(
            self,
            "清空绘制",
            "确定要清空当前 Mask 吗？此操作可撤销。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        if not self._finish_node_edit_session():
            return
        if self.project.image_asset is not None:
            bbox = (0, 0, self.project.image_asset.width, self.project.image_asset.height)
            after_patch = np.zeros((bbox[3], bbox[2]), dtype=np.uint16)
            self._push_mask_only_patch(bbox, after_patch)
            self._clear_mask_selection()
            self.tool_controller.set_annotations(self.project.annotations)
            self.tool_controller.selected_annotation_id = None
            self._refresh_canvas()
            self._set_dirty(True)

    def _select_all_annotations(self) -> None:
        self.tool_controller.select_all()

    def _active_label_color(self) -> str:
        for label in self.project.labels:
            if label.id == self.project.active_label_id:
                return label.color
        return "#ffd43b"

    def _update_preview_display(self) -> None:
        color = self._active_label_color()
        display_mask = self._preview_mask
        if self.magic_panel.only_show_new_region_enabled():
            display_mask = self._preview_mask_without_overlap(self._preview_mask, self._preview_bbox)
        preview_visible = display_mask is not None and self._preview_bbox is not None
        if preview_visible:
            self._preview_mask_outline_timer.start()
        else:
            self._preview_mask_outline_timer.stop()
            self._preview_mask_dash_offset = 0.0
        for canvas in self._all_canvases():
            if preview_visible:
                canvas.update_preview_mask(display_mask, self._preview_bbox, color)
                canvas.set_preview_mask_dash_offset(self._preview_mask_dash_offset)
            else:
                canvas.update_preview_mask(None, None, color)
            canvas.update_preview_polygons([], color)

    def _advance_preview_mask_outline_animation(self) -> None:
        if self._preview_mask is None or self._preview_bbox is None:
            self._preview_mask_outline_timer.stop()
            return
        self._preview_mask_dash_offset += 1.0
        for canvas in self._all_canvases():
            canvas.set_preview_mask_dash_offset(self._preview_mask_dash_offset)

    def closeEvent(self, event) -> None:
        if not self._finish_node_edit_session():
            event.ignore()
            return
        if not self._handle_pending_magic_session():
            event.ignore()
            return
        if not self._prompt_save_project_if_needed():
            event.ignore()
            return
        self._save_render_preferences()
        super().closeEvent(event)
