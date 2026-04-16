"""
图像分割工具主窗口。
"""

from __future__ import annotations

import copy
import os
import time
from pathlib import Path
import numpy as np

from PySide6.QtCore import QObject, QPointF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QFontDatabase, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut
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
from src.segmentation.algorithms import MagicWandSegmenter
from src.segmentation.exporters import (
    export_mask_file,
    export_vector_file,
)
from src.segmentation.geometry_service import GeometryService
from src.segmentation.image_sources import GeoTiffImageSource, StandardImageSource
from src.segmentation.rendering import default_render_config, render_base_rgb
from src.utils.image_io import pixel_to_lonlat
from src.dialogs.segmentation_export_dialog import SegmentationExportDialog
from src.widgets.colormap_combobox import ColormapComboBox
from src.widgets.layer_panel_widget import LayerPanelWidget
from src.widgets.label_panel_widget import LabelPanelWidget
from src.widgets.magic_wand_panel import MagicWandPanel
from src.widgets.render_settings_widget import RenderSettingsWidget
from src.widgets.operation_progress_widget import OperationProgressWidget
from src.widgets.segmentation_pg_view import SegmentationPgView
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("图像分割工具")
        self.resize(1600, 900)

        self.project_manager = SegmentationProjectManager()
        self.project = SegmentationProject(project_version="1.0", image_asset=None)
        self.label_store = LabelStore(self.project.labels)
        self.command_stack = CommandStack(self.project)
        self.tool_controller = SegmentationToolController(self)
        self.segmenter = MagicWandSegmenter()
        self.render_config = default_render_config()
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
        self._preview_vector_user_enabled = False
        self._mask_overlay_revision = 0
        self._raster_overlay_cache_key = None
        self._raster_overlay_cache_value = (None, None)
        self._last_edit_timestamp = 0.0
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
        self._geotiff_full_render_cache_limit_mb = self.project_manager.settings.value(
            "segmentation/geotiff_full_render_cache_limit_mb",
            512,
            type=int,
        )

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
        self.magic_tool_action = self._create_tool_action("魔法棒", SegmentationToolController.TOOL_MAGIC_WAND, self._make_tool_icon("auto_fix_high"))
        self.browse_tool_action.setChecked(True)
        for action in [
            self.browse_tool_action,
            self.rectangle_tool_action,
            self.polygon_tool_action,
            self.magic_tool_action,
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

        self.canvas = SegmentationPgView()
        splitter.addWidget(self.canvas)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        self.label_panel = LabelPanelWidget()
        self.layer_panel = LayerPanelWidget()
        self.magic_panel = MagicWandPanel()
        right_layout.addWidget(self.label_panel)
        right_layout.addWidget(self.layer_panel)
        right_layout.addWidget(self.magic_panel)
        right_layout.addStretch(1)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        bottom_layout = QHBoxLayout()
        self.mouse_pos_label = QLabel("行: -, 列: - | 渲染RGB: - | 原值: -")
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
        self.magic_panel.params_changed.connect(self._schedule_magic_preview)
        self.magic_panel.merge_preview_changed.connect(self._on_merge_preview_changed)
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

    def _make_tool_icon(self, icon_name: str) -> QIcon:
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
            return QIcon(pixmap)
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
        return QIcon(pixmap)

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

    def _geo_should_cache_full_render(self) -> bool:
        if self.project.image_asset is None or self.current_source is None:
            return False
        if not isinstance(self.current_source, GeoTiffImageSource):
            return False
        try:
            file_size_mb = Path(self.project.image_asset.path).stat().st_size / (1024 * 1024)
        except Exception:
            return False
        return file_size_mb <= max(64, self._geotiff_full_render_cache_limit_mb)

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
            self._analysis_full_rgb_cache = render_base_rgb(
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
        rendered = render_base_rgb(raw, self.render_config, nodata_value=self.project.image_asset.nodata)
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
        if metadata.band_count >= 3:
            self._set_display_mode("RGB")
            self.render_settings.set_stretch_mode(self.render_settings.STRETCH_NONE)
            self.render_settings.auto_range_check.setChecked(True)
            self.colormap_combo.setCurrentText("gray")
        else:
            self._set_display_mode("灰度")

    def _current_global_range(self, settings: dict) -> tuple[float, float] | None:
        if self.current_source is None:
            return None
        if settings["display_mode"] == "RGB":
            band_indices = settings["rgb_bands"]
            min_values = []
            max_values = []
            for band_index in band_indices:
                min_max = self.current_source.band_minmax(band_index)
                if min_max is None:
                    continue
                min_values.append(min_max[0])
                max_values.append(min_max[1])
            if not min_values:
                return None
            return float(min(min_values)), float(max(max_values))
        return self.current_source.band_minmax(settings["gray_band"])

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

    def _sync_project_mask_from_annotations(self) -> None:
        if self.project.image_asset is None:
            self._set_project_mask(None)
            return
        mask = GeometryService.rasterize_annotations(
            self.project.annotations,
            self.project.image_asset.width,
            self.project.image_asset.height,
        )
        self._set_project_mask(mask)

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
    ) -> None:
        if not commands:
            return
        batch_commands = commands[:]
        if update_mask:
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
        if update_mask:
            self._mark_mask_overlay_dirty()
        self.tool_controller.set_annotations(self.project.annotations)

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
        if self.project.active_label_id is None and labels:
            self.project.active_label_id = labels[0].id
        self._refresh_label_ui()
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
        self.render_config.global_value_range = self._current_global_range(settings)
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
        value_range = self._current_global_range(settings)
        if value_range is None:
            return
        self.render_settings.set_image_stats(*value_range)

    def open_image(self) -> None:
        if not self._finish_node_edit_session():
            return
        if not self._handle_pending_magic_session():
            return
        if not self._prompt_save_project_if_needed():
            return
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
            source = GeoTiffImageSource(file_path)
            metadata = source.metadata()
            if max(metadata.width, metadata.height) > 4096 and not metadata.overview_levels:
                reply = QMessageBox.question(
                    self,
                    "构建 overviews",
                    "当前 GeoTIFF 没有可用金字塔，是否现在构建 overviews 以改善浏览性能？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if reply == QMessageBox.Yes:
                    self._start_progress("正在创建 GeoTIFF 金字塔...")
                    try:
                        success, _levels = source.build_overviews(
                            progress_callback=lambda value, message: self._update_progress(value, message, maximum=100)
                        )
                        if success:
                            self._finish_progress("GeoTIFF 金字塔创建完成")
                        else:
                            self._fail_progress("GeoTIFF 金字塔创建失败")
                    except Exception:
                        self._fail_progress("GeoTIFF 金字塔创建失败")
                        raise
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
        self.canvas.set_image_source(source)
        self.canvas.set_interaction_mode(self.tool_controller.active_tool)
        self.tool_controller.set_annotations(self.project.annotations)
        self.status_label.setText(f"{os.path.basename(meta.path)} | {meta.width} x {meta.height}")
        self._update_render_settings_bands()
        self._update_image_stats_to_render_settings()
        self._apply_render_settings_update()
        self._replace_labels(self.project.labels)
        self._clear_magic_preview()
        self._refresh_canvas()
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
        self.project = project
        self.label_store.set_labels(project.labels)
        self.current_project_path = file_path
        image_path = project.image_asset.path if project.image_asset else None
        if not image_path:
            QMessageBox.warning(self, "错误", "项目文件中缺少图像路径。")
            return
        source = GeoTiffImageSource(image_path) if image_path.lower().endswith((".tif", ".tiff")) else StandardImageSource(image_path)
        self._configure_default_render_for_source(source)
        self._apply_source(
            source,
            reset_project=False,
            annotations=project.annotations,
            labels=project.labels,
            active_label_id=project.active_label_id,
        )
        self.project.display_state = project.display_state
        self.project.layer_visibility = project.layer_visibility
        self.project.export_prefs = project.export_prefs
        self.layer_panel.image_check.setChecked(self.project.layer_visibility.get("image", True))
        self.layer_panel.annotation_check.setChecked(self.project.layer_visibility.get("annotations", True))
        self.layer_panel.raster_check.setChecked(self.project.layer_visibility.get("raster", True))
        self.layer_panel.preview_vector_check.setChecked(self.project.layer_visibility.get("preview_vector", False))
        self.layer_panel.preview_mask_check.setChecked(self.project.layer_visibility.get("preview_mask", True))
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
        self._start_progress("正在准备导出...", maximum=max(total_steps * 100, 100))
        try:
            if settings["export_vector"]:
                vector_driver = {
                    "GeoJSON": "GeoJSON",
                    "Shapefile": "ESRI Shapefile",
                    "GPKG": "GPKG",
                }[settings["vector_format"]]
                vector_path = output_dir / f"{settings['base_name']}{settings['vector_extension']}"
                self._update_progress(step_index * 100 + 10, f"正在导出矢量: {vector_path.name}", maximum=max(total_steps * 100, 100))
                export_vector_file(
                    self.project,
                    str(vector_path),
                    vector_driver,
                    coordinate_mode=settings["vector_coord_mode"],
                )
                exported_paths.append(str(vector_path))
                step_index += 1
                self._update_progress(step_index * 100, f"矢量导出完成: {vector_path.name}", maximum=max(total_steps * 100, 100))
            if settings["export_mask"]:
                mask_path = output_dir / f"{settings['base_name']}{settings['mask_extension']}"
                self._update_progress(step_index * 100 + 10, f"正在导出 Mask: {mask_path.name}", maximum=max(total_steps * 100, 100))
                export_mask_file(
                    self.project,
                    str(mask_path),
                    colored=settings["mask_colored"] and settings["mask_extension"] == ".tif",
                )
                exported_paths.append(str(mask_path))
                step_index += 1
                self._update_progress(step_index * 100, f"Mask 导出完成: {mask_path.name}", maximum=max(total_steps * 100, 100))
            self.project.export_prefs = dict(settings)
            self._finish_progress("导出完成")
            QMessageBox.information(self, "导出成功", "已导出到:\n" + "\n".join(exported_paths))
        except Exception as exc:
            self._fail_progress("导出失败")
            QMessageBox.warning(self, "导出失败", str(exc))

    def undo(self) -> None:
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
        self.tool_controller.handle_press(payload)

    def _handle_mouse_release(self, payload) -> None:
        self.tool_controller.handle_release(payload)
        self.tool_controller.set_annotations(self.project.annotations)
        self._refresh_canvas()

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

    def _add_polygon_annotation(self, polygon_points) -> None:
        if self.project.active_label_id is None:
            return
        annotation = AnnotationObject.from_polygon(
            label_id=self.project.active_label_id,
            exterior=polygon_points,
            source_tool="polygon",
        )
        affected_bbox = GeometryService.affected_bbox_from_annotations(annotation)
        self._push_commands_with_mask_patch([AddAnnotationCommand(annotation)], affected_bbox)
        self.tool_controller.selected_annotation_id = annotation.id
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
        affected_bbox = GeometryService.affected_bbox_from_annotations(annotation)
        self._push_commands_with_mask_patch([AddAnnotationCommand(annotation)], affected_bbox)
        self.tool_controller.selected_annotation_id = annotation.id
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
        self.layer_panel.preview_vector_check.blockSignals(True)
        self.layer_panel.preview_vector_check.setChecked(visible)
        self.layer_panel.preview_vector_check.blockSignals(False)
        self.project.layer_visibility["preview_vector"] = visible
        if user_initiated:
            self._preview_vector_user_enabled = visible

    def _preview_result_touches_roi_boundary(self, preview, roi_width: int, roi_height: int) -> bool:
        bx, by, bw, bh = preview.bbox
        if bw <= 0 or bh <= 0:
            return False
        return bx <= 0 or by <= 0 or bx + bw >= roi_width or by + bh >= roi_height

    def _build_magic_preview_result(self, x: int, y: int):
        if self.current_source is None or self.project.image_asset is None:
            return None, None
        full_rgb = self._ensure_full_analysis_rgb()
        if full_rgb is not None:
            preview = self.segmenter.run(full_rgb, (x, y), self.magic_panel.params())
            if preview.bbox[2] <= 0 or preview.bbox[3] <= 0:
                return None, None
            return preview.mask.astype(np.uint8), preview.bbox

        radius = 512
        max_side = self._analysis_max_roi_side()
        image_width = self.project.image_asset.width
        image_height = self.project.image_asset.height
        while True:
            x0 = max(0, x - radius)
            y0 = max(0, y - radius)
            x1 = min(image_width, x + radius)
            y1 = min(image_height, y + radius)
            roi_rgb = self._get_analysis_rgb_roi(x0, y0, x1 - x0, y1 - y0)
            preview = self.segmenter.run(roi_rgb, (x - x0, y - y0), self.magic_panel.params())
            if preview.bbox[2] <= 0 or preview.bbox[3] <= 0:
                return None, None
            if (
                self._preview_result_touches_roi_boundary(preview, roi_rgb.shape[1], roi_rgb.shape[0])
                and max(x1 - x0, y1 - y0) < max_side
                and (x0 > 0 or y0 > 0 or x1 < image_width or y1 < image_height)
            ):
                radius = min(radius * 2, max_side)
                continue
            bx, by, bw, bh = preview.bbox
            return preview.mask.astype(np.uint8), (x0 + bx, y0 + by, bw, bh)

    def _ensure_preview_polygons(self, source_tool: str = "magic_wand_preview", force: bool = False):
        if (
            self.preview_selection is None
            or self._preview_mask is None
            or self._preview_bbox is None
        ):
            return []
        if self.preview_selection.polygon_preview:
            return self.preview_selection.polygon_preview
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

    def _run_magic_wand_preview(self, x: int, y: int) -> None:
        if self.current_source is None or self.project.image_asset is None:
            return
        full_width = self.project.image_asset.width
        full_height = self.project.image_asset.height
        if not (0 <= x < full_width and 0 <= y < full_height):
            return
        if self._last_magic_seed != (x, y):
            self._set_preview_vector_visibility(False, user_initiated=False)
        mapped_mask, mapped_bbox = self._build_magic_preview_result(int(np.floor(x)), int(np.floor(y)))
        if mapped_mask is None or mapped_bbox is None:
            return
        if self.magic_panel.merge_preview_enabled() and self._preview_mask is not None and self._preview_bbox is not None:
            self._preview_mask, self._preview_bbox = GeometryService.merge_mask_bbox(
                self._preview_mask,
                self._preview_bbox,
                mapped_mask,
                mapped_bbox,
                "add",
            )
        else:
            self._preview_mask = mapped_mask
            self._preview_bbox = mapped_bbox
        from src.segmentation.models import PreviewSelection
        self.preview_selection = PreviewSelection(
            seed_point=self._last_magic_seed or (x, y),
            params=self.magic_panel.params(),
            bbox=self._preview_bbox,
            mask=self._preview_mask,
            contours=[],
            polygon_preview=[],
        )
        self._last_magic_seed = (x, y)
        if self.preview_selection:
            self._update_preview_display()

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
            self._preview_mask = None
            self._preview_bbox = None
            self.preview_selection = None
            self._update_preview_display()

    def _confirm_magic_preview(self) -> None:
        if not self.preview_selection or self.project.active_label_id is None or self._preview_mask is None or self._preview_bbox is None:
            return
        polygon_preview = self.preview_selection.polygon_preview or self._ensure_preview_polygons("magic_wand", force=True)
        if not polygon_preview:
            self._clear_magic_preview()
            return
        commands, affected_bbox = self._build_magic_commit_commands(polygon_preview)
        if commands:
            self._push_commands_with_mask_patch(commands, affected_bbox)
        if polygon_preview:
            self.tool_controller.selected_annotation_id = polygon_preview[-1].id
        self._clear_magic_preview()
        self._refresh_canvas()
        self._set_dirty(True)

    def _clear_magic_preview(self) -> None:
        self.preview_selection = None
        self._last_magic_seed = None
        self._preview_mask = None
        self._preview_bbox = None
        self._update_preview_display()
        self._refresh_canvas()

    def _on_layer_visibility_changed(self, layer_name: str, visible: bool) -> None:
        self.project.layer_visibility[layer_name] = visible
        if layer_name == "image":
            self.canvas.image_item.setVisible(visible)
        elif layer_name == "preview":
            self.canvas.preview_item.setVisible(visible)
        elif layer_name == "preview_vector":
            self._preview_vector_user_enabled = visible
            if visible and self.preview_selection is not None and not self.preview_selection.polygon_preview:
                self._ensure_preview_polygons("magic_wand_preview")
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
        if not self.project.image_asset or not self.canvas.last_render or not self.project.layer_visibility.get("raster", True):
            return None, None
        x0, y0, width, height = self.canvas.last_render.source_window
        rect_x, rect_y, rect_w, rect_h = self.canvas.last_render.image_rect
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
            cached_rgba, _cached_bbox = self._raster_overlay_cache_value
            if cached_rgba is None:
                return None, None
            return cached_rgba, (rect_x, rect_y, rect_w, rect_h)
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
        return raster_rgba, (rect_x, rect_y, rect_w, rect_h)

    def _on_view_state_changed(self, state) -> None:
        self.tool_controller.set_view_state(state)
        if self.project.image_asset is None:
            return
        self._update_image_stats_to_render_settings()
        label_lookup = {label.id: label for label in self.project.labels}
        raster_rgba, raster_bbox = self._current_raster_overlay(label_lookup)
        self.canvas.update_raster_mask(raster_rgba, raster_bbox)
        self._update_preview_display()

    def _build_preview_from_mask(self, mask, bbox, label_id: int | None = None, source_tool: str = "magic_wand_preview", force: bool = False):
        if mask is None or bbox is None:
            return None
        if not force and not self.project.layer_visibility.get("preview_vector", True):
            return None
        target_label = label_id or (self.project.active_label_id or 1)
        polygons = GeometryService.mask_to_annotations(
            mask,
            bbox,
            label_id=target_label,
            simplify=self.magic_panel.params().simplify_polygon,
            vector_smoothness=self.magic_panel.params().vector_smoothness,
            connectivity=self.magic_panel.params().connectivity,
            source_tool=source_tool,
        )
        from src.segmentation.models import PreviewSelection
        return PreviewSelection(
            seed_point=self._last_magic_seed or (0, 0),
            params=self.magic_panel.params(),
            bbox=bbox,
            mask=mask,
            contours=[item.exterior for item in polygons],
            polygon_preview=polygons,
        )

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
        commands = []
        new_union = GeometryService.annotations_union(new_annotations)
        if new_union is None or new_union.is_empty:
            return commands, None
        minx, miny, maxx, maxy = new_union.bounds
        bounds_bbox = (minx, miny, maxx - minx, maxy - miny)
        affected_annotations = list(new_annotations)
        for annotation in self.project.annotations:
            if not GeometryService.bbox_intersects(annotation.bbox, bounds_bbox):
                continue
            polygon = GeometryService.annotation_to_polygon(annotation)
            if polygon is None or polygon.is_empty or not polygon.intersects(new_union):
                continue
            affected_annotations.append(annotation)
            diff = polygon.difference(new_union)
            if diff.is_empty:
                commands.append(DeleteAnnotationCommand(annotation))
                continue
            updated_objects = GeometryService.polygon_to_annotation_objects(diff, annotation.label_id, annotation.source_tool)
            if not updated_objects:
                commands.append(DeleteAnnotationCommand(annotation))
                continue
            first = updated_objects[0]
            first.id = annotation.id
            commands.append(UpdateGeometryCommand(annotation.id, annotation, first))
            for extra in updated_objects[1:]:
                extra.label_id = annotation.label_id
                commands.append(AddAnnotationCommand(extra))
        for annotation in new_annotations:
            commands.append(AddAnnotationCommand(annotation))
        return commands, GeometryService.affected_bbox_from_annotations(affected_annotations)

    def _update_mouse_position(self, payload) -> None:
        if not self.project.image_asset:
            self.mouse_pos_label.setText("行: -, 列: - | 渲染RGB: - | 原值: -")
            return
        row = int(np.floor(payload.y))
        col = int(np.floor(payload.x))
        if 0 <= row < self.project.image_asset.height and 0 <= col < self.project.image_asset.width:
            original_value = self.current_source.read_pixel(col, row) if self.current_source else None
            rendered_rgb = self.canvas.rendered_rgb_at(col, row) or self._rendered_rgb_from_original(original_value)
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
                f"行: {row}, 列: {col} | 渲染RGB: {rgb_text} | 原值: {original_text}{geo_text}"
            )
        else:
            self.mouse_pos_label.setText("行: -, 列: - | 渲染RGB: - | 原值: -")

    def _rendered_rgb_from_original(self, original_value):
        if original_value is None:
            return None
        if isinstance(original_value, list):
            raw = np.asarray(original_value).reshape(1, 1, -1)
        else:
            raw = np.asarray([[original_value]])
        rgb = render_base_rgb(raw, self.render_config, nodata_value=self.project.image_asset.nodata if self.project.image_asset else None)
        if rgb.ndim != 3 or rgb.shape[2] < 3:
            return None
        return [int(rgb[0, 0, 0]), int(rgb[0, 0, 1]), int(rgb[0, 0, 2])]

    def clear_all_annotations(self) -> None:
        if not self.project.annotations:
            return
        reply = QMessageBox.question(
            self,
            "清空绘制",
            "确定要删除当前所有绘制的矢量对象吗？此操作可撤销。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        if not self._finish_node_edit_session():
            return
        commands = [DeleteAnnotationCommand(annotation) for annotation in self.project.annotations]
        if commands:
            affected_bbox = None
            if self.project.image_asset is not None:
                affected_bbox = (0, 0, self.project.image_asset.width, self.project.image_asset.height)
            self._push_commands_with_mask_patch(commands, affected_bbox=affected_bbox, update_mask=True)
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
        if self.project.layer_visibility.get("preview_vector", True) and self.preview_selection is not None:
            self.canvas.update_preview_polygons(self.preview_selection.polygon_preview, color)
        else:
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
