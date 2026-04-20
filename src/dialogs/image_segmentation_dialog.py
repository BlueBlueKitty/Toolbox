"""
图像分割工具主窗口。
"""

from __future__ import annotations

import copy
import os
import time
from collections import OrderedDict
from pathlib import Path
import numpy as np

from PySide6.QtCore import QObject, QPointF, Qt, QThread, QTimer, Signal, QEvent
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QFontDatabase, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut, QTransform
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
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
from src.segmentation.models import DisplayState, PreviewSelection
from src.segmentation.algorithms import MagicWandSegmenter
from src.segmentation.exporters import (
    export_coco,
    export_mask_file,
    export_vector_file,
    export_voc,
    export_yolo,
)
from src.segmentation.geometry_service import GeometryService
from src.rendering.sources import GdalRasterSource, StandardImageSource
from src.rendering.config import default_raster_render_config, render_raster_rgb
from src.utils.display_pyramid import DEFAULT_PYRAMID_THRESHOLD_MB
from src.utils.image_io import pixel_to_lonlat
from src.dialogs.segmentation_export_dialog import SegmentationExportDialog
from src.widgets.colormap_combobox import ColormapComboBox
from src.widgets.layer_panel_widget import LayerPanelWidget
from src.widgets.label_panel_widget import LabelPanelWidget
from src.widgets.magic_wand_panel import MagicWandPanel
from src.widgets.render_settings_widget import RenderSettingsWidget
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
        self._last_magic_seed = None
        self._preview_mask = None
        self._preview_bbox = None
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
        self._merge_preview_entries: list[dict] = []
        self._preview_undo_stack: list[dict] = []
        self._preview_redo_stack: list[dict] = []
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
        self._material_icon_family = self._load_material_icon_font()
        self._last_image_dir = self.project_manager.settings.value("last_image_dir", "", type=str)
        self._last_project_dir = self.project_manager.settings.value("last_project_dir", "", type=str)
        self._geotiff_full_render_cache_limit_mb = self.pyramid_threshold_mb

        self._create_ui()
        self._bind_signals()
        self._load_render_preferences()
        self._setup_shortcuts()

        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(60000)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.timeout.connect(self._autosave_if_needed)

    def _create_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        self.toolbar = QToolBar()
        main_layout.addWidget(self.toolbar)

        self.open_action = QAction(self.style().standardIcon(QStyle.SP_DialogOpenButton), "打开图像", self)
        self.open_project_action = QAction(self.style().standardIcon(QStyle.SP_FileDialogContentsView), "打开项目", self)
        self.save_project_action = QAction(self.style().standardIcon(QStyle.SP_DialogSaveButton), "保存项目", self)
        self.export_action = QAction(self.style().standardIcon(QStyle.SP_DialogApplyButton), "导出...", self)
        self.undo_action = QAction(self.style().standardIcon(QStyle.SP_ArrowBack), "撤销", self)
        self.redo_action = QAction(self.style().standardIcon(QStyle.SP_ArrowForward), "重做", self)
        self.clear_annotations_action = QAction(self._make_tool_icon("delete_sweep"), "清空绘制", self)
        self.open_action.setIcon(self._make_tool_icon("image"))
        self.open_project_action.setIcon(self._make_tool_icon("folder_open"))
        self.save_project_action.setIcon(self._make_tool_icon("save"))
        self.export_action.setIcon(self._make_tool_icon("ios_share"))
        self.undo_action.setIcon(self._make_tool_icon("undo"))
        self.redo_action.setIcon(self._make_tool_icon("redo"))
        self.actual_size_action = QAction(self._make_tool_icon("zoom_in_map"), "1:1", self)
        for action in [
            self.open_action,
            self.open_project_action,
            self.save_project_action,
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
        render_controls = QWidget()
        render_layout = QHBoxLayout(render_controls)
        render_layout.setContentsMargins(0, 0, 0, 0)
        render_layout.setSpacing(6)
        self.render_settings = RenderSettingsWidget(compact=True)
        self._remove_hillshade_mode()
        self.render_settings.set_smooth_display(False)
        render_layout.addWidget(self.render_settings.band_widget)
        render_layout.addWidget(self.render_settings.reverse_check)
        render_layout.addWidget(self.render_settings.stretch_combo)
        render_layout.addWidget(self.render_settings.stretch_param_widget)
        render_layout.addWidget(self.render_settings.auto_range_check)
        render_layout.addWidget(self.render_settings.min_spin)
        render_layout.addWidget(self.render_settings.range_dash_label)
        render_layout.addWidget(self.render_settings.max_spin)
        render_layout.addWidget(QLabel("Gamma:"))
        render_layout.addWidget(self.render_settings.gamma_spin)
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFrameShadow(QFrame.Sunken)
        render_layout.addWidget(sep)
        render_layout.addWidget(QLabel("Colormap:"))
        self.colormap_combo = ColormapComboBox()
        render_layout.addWidget(self.colormap_combo)
        render_layout.addStretch(1)
        main_layout.addWidget(render_controls)

        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        self.canvas = SegmentationCanvas()
        self.canvas.set_tool_icons({
            SegmentationToolController.TOOL_MAGIC_WAND: self._make_tool_icon("auto_fix_high"),
            SegmentationToolController.TOOL_BRUSH: self._make_tool_icon("brush"),
            SegmentationToolController.TOOL_ERASER: self._make_tool_icon("ink_eraser"),
        })
        self.canvas.files_dropped.connect(self._on_canvas_files_dropped)
        self.canvas.set_tool_color(self._active_label_color())
        splitter.addWidget(self.canvas)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 0, 4, 0)
        right_layout.setSpacing(0)
        self.label_panel = LabelPanelWidget()
        self.layer_panel = LayerPanelWidget()
        self.magic_panel = MagicWandPanel()
        self.label_panel.setMinimumHeight(260)
        self.layer_panel.setMinimumHeight(92)
        self.layer_panel.setMaximumHeight(120)
        self.layer_panel.set_layers([
            state.spec
            for state in self.canvas.layer_manager.layers()
            if state.spec.id not in {"draft", "snap", "preview_vector", "annotations"}
        ])
        right_layout.addWidget(self.label_panel, 1)
        right_layout.addWidget(self.layer_panel, 0)
        right_layout.addWidget(self.magic_panel, 0)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        bottom_layout = QHBoxLayout()
        self.mouse_pos_label = QLabel("行: -, 列: - | 渲染RGB: - | 原值: - | Mask 标签: 无标签")
        self.status_label = QLabel("未打开图像")
        bottom_layout.addWidget(self.mouse_pos_label)
        bottom_layout.addStretch(1)
        bottom_layout.addWidget(self.status_label)
        main_layout.addLayout(bottom_layout)
        self.operation_progress = OperationProgressWidget()
        main_layout.addWidget(self.operation_progress)

    def _bind_signals(self) -> None:
        self.open_action.triggered.connect(self.open_image)
        self.open_project_action.triggered.connect(self.open_project)
        self.save_project_action.triggered.connect(self.save_project)
        self.export_action.triggered.connect(self.export_data)
        self.undo_action.triggered.connect(self.undo)
        self.redo_action.triggered.connect(self.redo)
        self.clear_annotations_action.triggered.connect(self.clear_all_annotations)
        self.actual_size_action.triggered.connect(self.canvas.set_one_to_one)
        self.tool_action_group.triggered.connect(self._on_tool_action_triggered)

        self.canvas.mouse_pressed.connect(self._handle_mouse_press)
        self.canvas.mouse_moved.connect(self._update_mouse_position)
        self.canvas.mouse_moved.connect(self._handle_mouse_move)
        self.canvas.mouse_moved.connect(self.tool_controller.handle_move)
        self.canvas.mouse_released.connect(self._handle_mouse_release)
        self.canvas.view_state_changed.connect(self._on_view_state_changed)

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
        self.label_panel.labels_changed.connect(self._replace_labels)
        self.layer_panel.visibility_changed.connect(self._on_layer_visibility_changed)
        self.layer_panel.order_changed.connect(self._on_layer_order_changed)
        self.magic_panel.params_changed.connect(self._schedule_magic_preview)
        self.magic_panel.merge_preview_changed.connect(self._on_merge_preview_changed)
        self.magic_panel.slider_config_changed.connect(self._on_magic_slider_config_changed)
        self.magic_panel.brush_size_changed.connect(self._on_brush_size_changed)
        self.canvas.tool_wheel_adjust_requested.connect(self._adjust_active_tool_slider)
        self.magic_panel.confirm_requested.connect(self._confirm_magic_preview)
        self.magic_panel.cancel_requested.connect(self._clear_magic_preview)
        self.render_settings.settings_changed.connect(self.on_render_settings_changed)
        self.render_settings.suggest_colormap.connect(self.on_suggest_colormap)
        self.colormap_combo.currentTextChanged.connect(self.on_colormap_changed)
    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self.redo)
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
        self.export_action.setIcon(self._make_tool_icon("ios_share"))
        self.undo_action.setIcon(self._make_tool_icon("undo"))
        self.redo_action.setIcon(self._make_tool_icon("redo"))
        self.clear_annotations_action.setIcon(self._make_tool_icon("delete_sweep"))
        self.actual_size_action.setIcon(self._make_tool_icon("zoom_in_map"))
        self.browse_tool_action.setIcon(self._make_tool_icon("pan_tool"))
        self.rectangle_tool_action.setIcon(self._make_tool_icon("crop_square"))
        self.polygon_tool_action.setIcon(self._make_tool_icon("gesture"))
        self.magic_tool_action.setIcon(self._make_tool_icon("auto_fix_high", -90))
        self.brush_tool_action.setIcon(self._make_tool_icon("brush", 90))
        self.eraser_tool_action.setIcon(self._make_tool_icon("ink_eraser", 0))
        self.canvas.set_tool_icons({
            SegmentationToolController.TOOL_MAGIC_WAND: self._make_tool_icon("auto_fix_high"),
            SegmentationToolController.TOOL_BRUSH: self._make_tool_icon("brush"),
            SegmentationToolController.TOOL_ERASER: self._make_tool_icon("ink_eraser"),
        })

    def _on_magic_slider_config_changed(self, _key: str, configs: dict) -> None:
        self.project.magic_panel_settings = dict(configs or {})
        self._set_dirty(True)

    def _on_canvas_files_dropped(self, paths: list[str]) -> None:
        if not paths:
            return
        file_path = next((item for item in paths if os.path.isfile(item)), None)
        if file_path is None:
            QMessageBox.warning(self, "拖拽打开失败", "请拖入图像文件。")
            return
        if not file_path.lower().endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff")):
            QMessageBox.warning(self, "拖拽打开失败", "图像分割工具仅支持 JPG/PNG/TIF 图像。")
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
            self.autosave_timer.start()
        else:
            self.autosave_timer.stop()

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
        rendered = render_raster_rgb(raw, self.render_config, nodata_value=self.project.image_asset.nodata)
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

    def _configure_default_render_for_source(self, source) -> None:
        metadata = source.metadata()
        self.render_settings.reset_to_defaults(metadata.band_count)
        self.colormap_combo.setCurrentText("gray")
        if metadata.band_count >= 3:
            self._set_display_mode("RGB")
            self.render_settings.set_stretch_mode(self.render_settings.STRETCH_NONE)
            self.render_settings.auto_range_check.setChecked(False)
            self.colormap_combo.setCurrentText("gray")
        else:
            self._set_display_mode("灰度")

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
            after_annotations = self._annotations_after_commands(commands)
            after_patch = self._rasterize_annotations_patch(after_annotations, bbox)
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
        self.canvas.set_tool_color(self._active_label_color())
        self._refresh_canvas()
        self._set_dirty(True)

    def _set_active_label(self, label_id: int) -> None:
        self._apply_label_choice(label_id)

    def _apply_label_choice(self, label_id: int) -> None:
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
        self.canvas.set_tool_color(self._active_label_color())

    def _on_brush_size_changed(self, size: int) -> None:
        self.canvas.set_brush_radius(max(1, int(round(size / 2.0))))

    def _refresh_label_ui(self) -> None:
        self.label_panel.blockSignals(True)
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
            self.canvas.set_render_config(self.render_config)
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
                "Images (*.jpg *.jpeg *.png *.tif *.tiff)",
            )
        if not file_path:
            return
        self._last_image_dir = os.path.dirname(file_path)
        self.project_manager.settings.setValue("last_image_dir", self._last_image_dir)
        self._load_image(file_path)

    def _load_image(self, file_path: str) -> None:
        lower = file_path.lower()
        if lower.endswith((".tif", ".tiff")):
            source = GdalRasterSource(file_path, pyramid_threshold_mb=self.pyramid_threshold_mb)
        else:
            source = StandardImageSource(file_path)
        self._configure_default_render_for_source(source)
        self._apply_source(
            source,
            reset_project=True,
            annotations=[],
            labels=self.label_store.labels(),
            active_label_id=self.project.active_label_id or 1,
        )
        self.current_project_path = None
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
        if reset_project:
            self.project = SegmentationProject(
                project_version="1.0",
                image_asset=meta,
                labels=labels,
                annotations=annotations,
                active_label_id=active_label_id,
                magic_panel_settings=self.magic_panel.get_slider_configs(),
            )
        else:
            self.project.image_asset = meta
            self.project.labels = labels
            self.project.annotations = annotations
            self.project.active_label_id = active_label_id
        if self.project.mask_data is None:
            self._sync_project_mask_from_annotations()
        self.command_stack = CommandStack(self.project)
        self._clear_node_edit_session_state(clear_override=True)
        self._clear_analysis_cache()
        self.canvas.set_render_config(self.render_config)
        self.canvas.set_raster_source(source)
        self.canvas.set_interaction_mode(self.tool_controller.active_tool)
        self.tool_controller.set_annotations(self.project.annotations)
        self.status_label.setText(f"{os.path.basename(meta.path)} | {meta.width} x {meta.height}")
        self._update_render_settings_bands()
        self._update_image_stats_to_render_settings()
        self._apply_render_settings_update()
        self._replace_labels(self.project.labels)
        if not self.project.magic_panel_settings:
            self.project.magic_panel_settings = self.magic_panel.get_slider_configs()
        self.magic_panel.apply_slider_configs(self.project.magic_panel_settings)
        self.magic_panel.refresh_icons()
        self._clear_magic_preview()
        self._refresh_canvas()
        if reset_project:
            self.canvas.set_one_to_one()

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
        source = GdalRasterSource(image_path, pyramid_threshold_mb=self.pyramid_threshold_mb) if image_path.lower().endswith((".tif", ".tiff")) else StandardImageSource(image_path)
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
        self.canvas.restore_view_state(self.project.display_state)
        for layer_id, visible in self.project.layer_visibility.items():
            self.layer_panel.set_layer_checked(layer_id, visible)
            if self.canvas.layer_manager.layer(layer_id):
                self.canvas.set_layer_visible(layer_id, visible)
        self._refresh_label_ui()
        self._refresh_canvas()
        self._set_dirty(False)

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
        self._start_progress("正在保存项目...")
        try:
            self._update_progress(30, "正在写入项目文件...", maximum=100)
            self.project_manager.save_project(self.project, self.current_project_path)
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

    def export_data(self) -> None:
        if not self._finish_node_edit_session():
            return
        if self.project.image_asset is None:
            return
        default_name = f"{Path(self.project.image_asset.path).stem}_mask"
        default_dir = self._last_project_dir or self._last_image_dir or str(Path(self.project.image_asset.path).parent)
        settings = SegmentationExportDialog.get_settings(
            default_name=default_name,
            default_dir=default_dir,
            has_geo=bool(self.project.image_asset.has_georef),
            prefer_tif_mask=self.project.image_asset.path.lower().endswith((".tif", ".tiff")),
            parent=self,
        )
        if not settings:
            return
        output_dir = Path(settings["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        exported_paths = []
        total_steps = int(settings["export_vector"]) + int(settings["export_mask"])
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
                        colored=settings["mask_colored"] and settings["mask_extension"] == ".tif",
                    )
                    exported_paths.append(str(mask_path))
                    self._update_progress(step_index * 100 + 90, f"Mask 导出完成: {mask_path.name}", maximum=max(total_steps * 100, 100))
                except Exception as exc:
                    failed_exports.append(f"Mask 导出失败（{mask_path.name}）：{self._format_exception_message(exc, '导出失败。')}")
                step_index += 1
                self._update_progress(step_index * 100, maximum=max(total_steps * 100, 100))
            self.project.export_prefs = dict(settings)
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
        elif self.tool_controller.selected_annotation_ids:
            self._set_controller_selection(set())
        else:
            self.canvas.update_draft(None)

    def _handle_mouse_press(self, payload) -> None:
        if self._handle_mask_paint_payload(payload, begin=True):
            return
        self.tool_controller.handle_press(payload)

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
        self.canvas.set_interaction_mode(target)
        if target != SegmentationToolController.TOOL_MAGIC_WAND:
            self._clear_magic_preview()

    def _adjust_active_tool_slider(self, steps: int) -> None:
        tool = self.tool_controller.active_tool
        if tool == SegmentationToolController.TOOL_MAGIC_WAND:
            slider = self.magic_panel.tolerance_slider
        elif tool in {SegmentationToolController.TOOL_BRUSH, SegmentationToolController.TOOL_ERASER}:
            slider = self.magic_panel.brush_size_slider
        else:
            return
        slider.setValue(max(slider.minimum(), min(slider.maximum(), slider.value() + int(steps))))

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
        radius = max(1, int(round(self.magic_panel.brush_size() / 2.0)))
        if previous is None:
            self._paint_mask_at(x, y, erase=erase)
            self._last_mask_paint_point = (x, y)
            return

        px, py = previous
        distance = float(np.hypot(x - px, y - py))
        spacing = max(1.0, radius * 0.45)
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
        radius = max(1, int(round(self.magic_panel.brush_size() / 2.0)))
        cx = int(round(x))
        cy = int(round(y))
        height, width = self.project.mask_data.shape
        x0 = max(0, cx - radius)
        y0 = max(0, cy - radius)
        x1 = min(width, cx + radius + 1)
        y1 = min(height, cy + radius + 1)
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
        disk = (xx - cx) ** 2 + (yy - cy) ** 2 <= radius ** 2
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
        self.project.annotations = [
            updated.clone() if item.id == annotation_id else item
            for item in self.project.annotations
        ]
        self.tool_controller.set_annotations(self.project.annotations)
        self._refresh_canvas()

    def _on_geometry_committed(self, annotation_id: str, before: AnnotationObject, after: AnnotationObject) -> None:
        self._ensure_node_edit_session(annotation_id)
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
        if full_rgb is not None:
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
            radius = min(radius, max_side)
            x0 = max(0, x - radius)
            y0 = max(0, y - radius)
            x1 = min(image_width, x + radius)
            y1 = min(image_height, y + radius)
            width = x1 - x0
            height = y1 - y0
            roi_rgb = self._get_analysis_rgb_roi(x0, y0, width, height)
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
        full_width = self.project.image_asset.width
        full_height = self.project.image_asset.height
        if not (0 <= x < full_width and 0 <= y < full_height):
            return
        self._start_progress("正在识别魔法棒选区...", maximum=100)
        self._ensure_preview_mask_layer_visible_for_magic()
        if self._last_magic_seed != (x, y):
            self._set_preview_vector_visibility(False, user_initiated=False)
        try:
            self._update_progress(20, "正在计算局部识别区域...", maximum=100)
            mapped_mask, mapped_bbox, preview_info = self._build_magic_preview_result(int(np.floor(x)), int(np.floor(y)))
            if mapped_mask is None or mapped_bbox is None:
                if preview_info and preview_info.get("filtered_by_min_area"):
                    area = int(preview_info.get("pixel_area", 0))
                    min_area = int(preview_info.get("min_area", self.magic_panel.params().min_area))
                    self._finish_progress(f"识别区域像素数 {area} 小于最小面积阈值 {min_area}，已忽略")
                else:
                    self._finish_progress("没有识别到有效选区，可能是阈值太小或者最小面积参数太大了")
                return
            self._update_progress(70, "正在合并预览 Mask...", maximum=100)
            if self.magic_panel.merge_preview_enabled():
                self._push_preview_history()
                self._upsert_merge_preview_entry((x, y), mapped_mask, mapped_bbox)
                self._rebuild_merge_preview_from_entries()
            else:
                self._clear_preview_history()
                self._merge_preview_entries = []
                self._preview_mask = mapped_mask
                self._preview_bbox = mapped_bbox
            self.preview_selection = PreviewSelection(
                seed_point=self._last_magic_seed or (x, y),
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
            self._finish_progress("魔法棒识别完成")
        except Exception:
            self._fail_progress("魔法棒识别失败")
            raise

    def _schedule_magic_preview(self, _params) -> None:
        if self._last_magic_seed is None or self.tool_controller.active_tool != SegmentationToolController.TOOL_MAGIC_WAND:
            return
        self._magic_preview_timer.start()

    def _trigger_pending_magic_preview(self) -> None:
        if self._last_magic_seed is None:
            return
        self._run_magic_wand_preview(*self._last_magic_seed)

    def _on_merge_preview_changed(self, enabled: bool) -> None:
        if not enabled:
            self._merge_preview_entries = []
            self._clear_preview_history()
            self._preview_mask = None
            self._preview_bbox = None
            self.preview_selection = None
            self._update_preview_display()

    def _ensure_preview_mask_layer_visible_for_magic(self) -> None:
        if self.project.layer_visibility.get("preview_mask", True):
            return
        self.project.layer_visibility["preview_mask"] = True
        self.layer_panel.set_layer_checked("preview_mask", True)
        if self.canvas.layer_manager.layer("preview_mask"):
            self.canvas.set_layer_visible("preview_mask", True)

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
            self._apply_binary_preview_mask(self._preview_mask, self._preview_bbox, self.project.active_label_id)
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
        self._last_magic_seed = None
        self._preview_mask = None
        self._preview_bbox = None
        self._update_preview_display()
        self._refresh_canvas()

    def _on_layer_visibility_changed(self, layer_name: str, visible: bool) -> None:
        self.project.layer_visibility[layer_name] = visible
        if self.canvas.layer_manager.layer(layer_name):
            self.canvas.set_layer_visible(layer_name, visible)
        if layer_name == "preview_vector" and visible:
            self._refresh_preview_vector(force=True)
            return
        self._refresh_canvas()

    def _on_layer_order_changed(self, layer_name: str, index: int) -> None:
        if self.canvas.layer_manager.layer(layer_name):
            self.canvas.move_layer(layer_name, index)
            self._refresh_canvas()

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
        self.canvas.update_annotations(
            annotations,
            label_lookup,
            self.tool_controller.selected_annotation_ids,
            editable_annotation_id=self.tool_controller.editable_annotation_id(),
            active_vertex=self.tool_controller.selected_vertex_index,
        )
        raster_rgba, raster_bbox = self._current_raster_overlay(label_lookup)
        self.canvas.update_raster_mask(raster_rgba, raster_bbox)
        self._update_preview_display()

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
        self._update_image_stats_to_render_settings()
        label_lookup = {label.id: label for label in self.project.labels}
        raster_rgba, raster_bbox = self._current_raster_overlay(label_lookup)
        self.canvas.update_raster_mask(raster_rgba, raster_bbox)
        self._update_preview_display()

    def _sync_display_state_from_canvas(self) -> None:
        if self.project.image_asset is None:
            return
        self._on_view_state_changed(self.canvas.current_view_state())

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
        if not self.project.image_asset:
            self.mouse_pos_label.setText("行: -, 列: - | 渲染RGB: - | 原值: - | Mask 标签: 无标签")
            return
        row = int(np.floor(payload.y))
        col = int(np.floor(payload.x))
        if 0 <= row < self.project.image_asset.height and 0 <= col < self.project.image_asset.width:
            original_value = self.current_source.read_pixel(col, row) if self.current_source else None
            rendered_rgb = self.canvas.rendered_rgb_at(col, row) or self._rendered_rgb_from_original(original_value)
            mask_label_name = self._mask_label_name_at(col, row)
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
                f"行: {row}, 列: {col} | 渲染RGB: {rgb_text} | 原值: {original_text} | Mask 标签: {mask_label_name}{geo_text}"
            )
        else:
            self.mouse_pos_label.setText("行: -, 列: - | 渲染RGB: - | 原值: - | Mask 标签: 无标签")

    def _mask_label_name_at(self, col: int, row: int) -> str:
        if self.project.mask_data is None:
            return "无标签"
        if not (0 <= row < self.project.mask_data.shape[0] and 0 <= col < self.project.mask_data.shape[1]):
            return "无标签"
        label_id = int(self.project.mask_data[row, col])
        if label_id <= 0:
            return "无标签"
        for label in self.project.labels:
            if label.id == label_id:
                return label.name
        return "无标签"

    def _rendered_rgb_from_original(self, original_value):
        if original_value is None:
            return None
        if isinstance(original_value, list):
            raw = np.asarray(original_value).reshape(1, 1, -1)
        else:
            raw = np.asarray([[original_value]])
        rgb = render_raster_rgb(raw, self.render_config, nodata_value=self.project.image_asset.nodata if self.project.image_asset else None)
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
        if self.project.layer_visibility.get("preview_mask", True) and self._preview_mask is not None and self._preview_bbox is not None:
            self.canvas.update_preview_mask(self._preview_mask, self._preview_bbox, color)
        else:
            self.canvas.update_preview_mask(None, None, color)
        self.canvas.update_preview_polygons([], color)

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
        super().closeEvent(event)
