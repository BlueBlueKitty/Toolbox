"""
图像分割工具主窗口。
"""

from __future__ import annotations

import os
from pathlib import Path
import numpy as np

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
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
)
from src.segmentation.algorithms import MagicWandSegmenter
from src.segmentation.exporters import (
    export_coco,
    export_mask_file,
    export_vector_file,
    export_voc,
    export_yolo,
)
from src.segmentation.geometry_service import GeometryService
from src.segmentation.image_sources import GeoTiffImageSource, StandardImageSource
from src.segmentation.rendering import default_render_config
from src.widgets.layer_panel_widget import LayerPanelWidget
from src.widgets.label_panel_widget import LabelPanelWidget
from src.widgets.magic_wand_panel import MagicWandPanel
from src.widgets.segmentation_pg_view import SegmentationPgView
from src.widgets.segmentation_tool_controller import SegmentationToolController


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
        self._vertex_update_pending: dict[str, AnnotationObject] = {}
        self._magic_preview_timer = QTimer(self)
        self._magic_preview_timer.setInterval(60)
        self._magic_preview_timer.setSingleShot(True)
        self._magic_preview_timer.timeout.connect(self._trigger_pending_magic_preview)

        self._create_ui()
        self._bind_signals()
        self._setup_shortcuts()

        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(30000)
        self.autosave_timer.timeout.connect(self._autosave_if_needed)
        self.autosave_timer.start()

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
        self.fit_action = QAction(self.style().standardIcon(QStyle.SP_DesktopIcon), "适应窗口", self)
        self.actual_size_action = QAction(self.style().standardIcon(QStyle.SP_TitleBarMaxButton), "1:1", self)
        for action in [
            self.open_action,
            self.open_project_action,
            self.save_project_action,
            self.export_action,
            self.undo_action,
            self.redo_action,
            self.fit_action,
            self.actual_size_action,
        ]:
            self.toolbar.addAction(action)

        self.toolbar.addSeparator()
        self.toolbar.addWidget(QLabel("工具"))
        self.tool_action_group = QActionGroup(self)
        self.tool_action_group.setExclusive(True)
        self.browse_tool_action = self._create_tool_action("浏览", SegmentationToolController.TOOL_BROWSE, self._make_tool_icon("browse"))
        self.rectangle_tool_action = self._create_tool_action("矩形框", SegmentationToolController.TOOL_RECTANGLE, self._make_tool_icon("rectangle"))
        self.polygon_tool_action = self._create_tool_action("多边形", SegmentationToolController.TOOL_POLYGON, self._make_tool_icon("polygon"))
        self.magic_tool_action = self._create_tool_action("魔法棒", SegmentationToolController.TOOL_MAGIC_WAND, self._make_tool_icon("magic"))
        self.browse_tool_action.setChecked(True)
        for action in [
            self.browse_tool_action,
            self.rectangle_tool_action,
            self.polygon_tool_action,
            self.magic_tool_action,
        ]:
            self.tool_action_group.addAction(action)
            self.toolbar.addAction(action)

        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        self.canvas = SegmentationPgView()
        splitter.addWidget(self.canvas)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        self.label_panel = LabelPanelWidget()
        self.layer_panel = LayerPanelWidget()
        self.magic_panel = MagicWandPanel()
        self.assign_label_combo = QComboBox()
        assign_row = QWidget()
        assign_layout = QHBoxLayout(assign_row)
        assign_layout.setContentsMargins(0, 0, 0, 0)
        assign_layout.addWidget(QLabel("选中对象标签"))
        assign_layout.addWidget(self.assign_label_combo)
        right_layout.addWidget(self.label_panel)
        right_layout.addWidget(assign_row)
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

    def _bind_signals(self) -> None:
        self.open_action.triggered.connect(self.open_image)
        self.open_project_action.triggered.connect(self.open_project)
        self.save_project_action.triggered.connect(self.save_project)
        self.export_action.triggered.connect(self.export_data)
        self.undo_action.triggered.connect(self.undo)
        self.redo_action.triggered.connect(self.redo)
        self.fit_action.triggered.connect(self.canvas.fit_image)
        self.actual_size_action.triggered.connect(self.canvas.set_one_to_one)
        self.tool_action_group.triggered.connect(self._on_tool_action_triggered)

        self.canvas.mouse_pressed.connect(self._handle_mouse_press)
        self.canvas.mouse_moved.connect(self._update_mouse_position)
        self.canvas.mouse_moved.connect(self.tool_controller.handle_move)
        self.canvas.mouse_released.connect(self._handle_mouse_release)

        self.tool_controller.polygon_finished.connect(self._add_polygon_annotation)
        self.tool_controller.rectangle_finished.connect(self._add_rectangle_annotation)
        self.tool_controller.magic_wand_requested.connect(self._run_magic_wand_preview)
        self.tool_controller.selection_changed.connect(self._on_selection_changed)
        self.tool_controller.geometry_changed.connect(self._on_geometry_changed)
        self.tool_controller.draft_changed.connect(self._on_draft_changed)

        self.label_panel.active_label_changed.connect(self._set_active_label)
        self.label_panel.labels_changed.connect(self._replace_labels)
        self.layer_panel.visibility_changed.connect(self._on_layer_visibility_changed)
        self.magic_panel.params_changed.connect(self._schedule_magic_preview)
        self.magic_panel.merge_preview_changed.connect(self._on_merge_preview_changed)
        self.magic_panel.confirm_requested.connect(self._confirm_magic_preview)
        self.magic_panel.cancel_requested.connect(self._clear_magic_preview)
        self.assign_label_combo.currentIndexChanged.connect(self._assign_selected_label)

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self.redo)
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
        return action

    def _make_tool_icon(self, tool_name: str) -> QIcon:
        pixmap = QPixmap(18, 18)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor("#1f2937"), 2))
        if tool_name == "browse":
            painter.drawLine(9, 2, 9, 16)
            painter.drawLine(2, 9, 16, 9)
        elif tool_name == "rectangle":
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(3, 4, 12, 10)
        elif tool_name == "polygon":
            painter.drawPolygon(QPolygonF([QPointF(3, 12), QPointF(7, 4), QPointF(14, 6), QPointF(12, 14)]))
        else:
            painter.drawLine(9, 2, 9, 16)
            painter.drawLine(2, 9, 16, 9)
            painter.setPen(QPen(QColor("#f59f00"), 2))
            painter.drawEllipse(QPointF(9, 9), 5, 5)
        painter.end()
        return QIcon(pixmap)

    def _set_dirty(self, value: bool = True) -> None:
        self._dirty = value

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
        selected = self._selected_annotation()
        if selected is not None and selected.label_id != label_id:
            self.command_stack.push(UpdateLabelAssignmentCommand(selected.id, selected.label_id, int(label_id)))
            self.tool_controller.set_annotations(self.project.annotations)
            self._set_dirty(True)
            self._refresh_canvas()
        self.project.active_label_id = int(label_id)
        self._refresh_label_ui()

    def _refresh_label_ui(self) -> None:
        self.label_panel.blockSignals(True)
        self.label_panel.set_labels(self.project.labels, self.project.active_label_id)
        self.label_panel.blockSignals(False)
        self.assign_label_combo.blockSignals(True)
        self.assign_label_combo.clear()
        for label in self.project.labels:
            self.assign_label_combo.addItem(f"{label.name} ({label.shortcut})", label.id)
        selected = self._selected_annotation()
        if selected:
            index = self.assign_label_combo.findData(selected.label_id)
            if index >= 0:
                self.assign_label_combo.setCurrentIndex(index)
        self.assign_label_combo.blockSignals(False)

    def open_image(self) -> None:
        if not self._handle_pending_magic_session():
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "打开图像",
            "",
            "Images (*.jpg *.jpeg *.png *.tif *.tiff)",
        )
        if not file_path:
            return
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
                    source.build_overviews()
        else:
            source = StandardImageSource(file_path)
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
        self.command_stack = CommandStack(self.project)
        self.canvas.set_render_config(self.render_config)
        self.canvas.set_image_source(source)
        self.canvas.set_interaction_mode(self.tool_controller.active_tool)
        self.tool_controller.set_annotations(self.project.annotations)
        self.status_label.setText(f"{os.path.basename(meta.path)} | {meta.width} x {meta.height}")
        self._replace_labels(self.project.labels)
        self._clear_magic_preview()
        self._refresh_canvas()

    def open_project(self) -> None:
        if not self._handle_pending_magic_session():
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "打开项目",
            "",
            f"Segmentation Project (*{self.project_manager.PROJECT_SUFFIX});;JSON (*.json)",
        )
        if not file_path:
            return
        project = self.project_manager.load_project(file_path)
        self.project = project
        self.label_store.set_labels(project.labels)
        self.current_project_path = file_path
        image_path = project.image_asset.path if project.image_asset else None
        if not image_path:
            QMessageBox.warning(self, "错误", "项目文件中缺少图像路径。")
            return
        source = GeoTiffImageSource(image_path) if image_path.lower().endswith((".tif", ".tiff")) else StandardImageSource(image_path)
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
        self.layer_panel.preview_check.setChecked(self.project.layer_visibility.get("preview", True))
        self._refresh_label_ui()
        self._refresh_canvas()
        self._set_dirty(False)

    def save_project(self) -> None:
        if self.project.image_asset is None:
            QMessageBox.warning(self, "提示", "请先打开图像。")
            return
        if not self.current_project_path:
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "保存项目",
                str(Path(self.project.image_asset.path).with_suffix(self.project_manager.PROJECT_SUFFIX)),
                f"Segmentation Project (*{self.project_manager.PROJECT_SUFFIX})",
            )
            if not file_path:
                return
            self.current_project_path = file_path
        self.project_manager.save_project(self.project, self.current_project_path)
        self._set_dirty(False)
        QMessageBox.information(self, "保存成功", f"项目已保存到:\n{self.current_project_path}")

    def export_data(self) -> None:
        if self.project.image_asset is None:
            return
        export_type, ok = QFileDialog.getSaveFileName(
            self,
            "导出数据",
            "",
            "GeoJSON (*.geojson);;Shapefile (*.shp);;GPKG (*.gpkg);;COCO (*.json);;YOLO (*.txt);;Pascal VOC (*.xml);;PNG Mask (*.png);;TIFF Mask (*.tif)",
        )
        if not export_type:
            return
        lower = export_type.lower()
        if lower.endswith(".geojson"):
            export_vector_file(self.project, export_type, "GeoJSON")
        elif lower.endswith(".shp"):
            export_vector_file(self.project, export_type, "ESRI Shapefile")
        elif lower.endswith(".gpkg"):
            export_vector_file(self.project, export_type, "GPKG")
        elif lower.endswith(".json"):
            export_coco(self.project, export_type)
        elif lower.endswith(".txt"):
            export_yolo(self.project, export_type)
        elif lower.endswith(".xml"):
            export_voc(self.project, export_type)
        else:
            export_mask_file(self.project, export_type)
        QMessageBox.information(self, "导出成功", f"已导出到:\n{export_type}")

    def undo(self) -> None:
        if self.command_stack.undo():
            self.tool_controller.set_annotations(self.project.annotations)
            self._refresh_canvas()

    def redo(self) -> None:
        if self.command_stack.redo():
            self.tool_controller.set_annotations(self.project.annotations)
            self._refresh_canvas()

    def delete_selected(self) -> None:
        selected = self._selected_annotation()
        if selected is None:
            return
        self.command_stack.push(DeleteAnnotationCommand(selected))
        self.tool_controller.set_annotations(self.project.annotations)
        self.tool_controller.selected_annotation_id = None
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
            if self.tool_controller.remove_selected_vertex():
                self._set_dirty(True)

    def _enter_action(self) -> None:
        if self.tool_controller.active_tool == SegmentationToolController.TOOL_MAGIC_WAND:
            self._confirm_magic_preview()
        else:
            self.tool_controller.finish_polygon()

    def _escape_action(self) -> None:
        if self.tool_controller.active_tool == SegmentationToolController.TOOL_MAGIC_WAND and self._preview_mask is not None:
            self._clear_magic_preview()
        else:
            self.canvas.update_draft(None)

    def _handle_mouse_press(self, payload) -> None:
        self.tool_controller.handle_press(payload)

    def _handle_mouse_release(self, payload) -> None:
        self.tool_controller.handle_release(payload)
        if self._vertex_update_pending:
            for annotation_id, updated in self._vertex_update_pending.items():
                original = next((item for item in self.project.annotations if item.id == annotation_id), None)
                if original is not None:
                    self.command_stack.push(UpdateGeometryCommand(annotation_id, original, updated))
            self._vertex_update_pending.clear()
            self.tool_controller.set_annotations(self.project.annotations)
            self._refresh_canvas()
            self._set_dirty(True)

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
        self.command_stack.push(AddAnnotationCommand(annotation))
        self.tool_controller.set_annotations(self.project.annotations)
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
        self.command_stack.push(AddAnnotationCommand(annotation))
        self.tool_controller.set_annotations(self.project.annotations)
        self.tool_controller.selected_annotation_id = annotation.id
        self._refresh_canvas()
        self._set_dirty(True)

    def _on_selection_changed(self, annotation_id: str | None) -> None:
        self.tool_controller.selected_annotation_id = annotation_id
        self._refresh_canvas()
        self._refresh_label_ui()

    def _on_geometry_changed(self, annotation_id: str, updated: AnnotationObject) -> None:
        self._vertex_update_pending[annotation_id] = updated.clone()
        self.project.annotations = [
            updated.clone() if item.id == annotation_id else item
            for item in self.project.annotations
        ]
        self.tool_controller.set_annotations(self.project.annotations)
        self._refresh_canvas()

    def _on_draft_changed(self, draft_type: str, points) -> None:
        if draft_type == "clear":
            self.canvas.update_draft(None)
            return
        self.canvas.update_draft(points)

    def _run_magic_wand_preview(self, x: int, y: int) -> None:
        rendered = self.canvas.viewport_image()
        last_render = self.canvas.last_render
        if rendered is None or last_render is None:
            return
        win_x, win_y, win_w, win_h = last_render.source_window
        if not (win_x <= x <= win_x + win_w and win_y <= y <= win_y + win_h):
            return
        seed_x = int((x - win_x) * rendered.shape[1] / max(win_w, 1))
        seed_y = int((y - win_y) * rendered.shape[0] / max(win_h, 1))
        preview = self.segmenter.run(rendered, (seed_x, seed_y), self.magic_panel.params())
        mapped_mask, mapped_bbox = self._map_preview_to_image(preview, last_render, rendered.shape[1], rendered.shape[0], win_x, win_y, win_w, win_h)
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
            self.canvas.update_preview_mask(self.preview_selection.mask, self.preview_selection.bbox)

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
            self.canvas.update_preview_mask(None, None)

    def _confirm_magic_preview(self) -> None:
        if not self.preview_selection or self.project.active_label_id is None or self._preview_mask is None or self._preview_bbox is None:
            return
        preview = self._build_preview_from_mask(
            self._preview_mask,
            self._preview_bbox,
            label_id=self.project.active_label_id,
            source_tool="magic_wand",
        )
        if not preview or not preview.polygon_preview:
            self._clear_magic_preview()
            return
        commands = self._build_magic_commit_commands(preview.polygon_preview)
        if commands:
            self.command_stack.push(BatchCommand(commands))
        self.tool_controller.set_annotations(self.project.annotations)
        if preview.polygon_preview:
            self.tool_controller.selected_annotation_id = preview.polygon_preview[-1].id
        self._clear_magic_preview()
        self._refresh_canvas()
        self._set_dirty(True)

    def _clear_magic_preview(self) -> None:
        self.preview_selection = None
        self._last_magic_seed = None
        self._preview_mask = None
        self._preview_bbox = None
        self.canvas.update_preview_mask(None, None)
        self._refresh_canvas()

    def _on_layer_visibility_changed(self, layer_name: str, visible: bool) -> None:
        self.project.layer_visibility[layer_name] = visible
        if layer_name == "image":
            self.canvas.image_item.setVisible(visible)
        elif layer_name == "raster":
            self.canvas.raster_item.setVisible(visible)
        elif layer_name == "preview":
            self.canvas.preview_item.setVisible(visible)
        else:
            self._refresh_canvas()

    def _assign_selected_label(self, _index: int) -> None:
        annotation = self._selected_annotation()
        if annotation is None:
            return
        new_label_id = self.assign_label_combo.currentData()
        if new_label_id is None or new_label_id == annotation.label_id:
            return
        self.command_stack.push(UpdateLabelAssignmentCommand(annotation.id, annotation.label_id, int(new_label_id)))
        self.tool_controller.set_annotations(self.project.annotations)
        self._refresh_canvas()
        self._set_dirty(True)

    def _selected_annotation(self) -> AnnotationObject | None:
        selected_id = self.tool_controller.selected_annotation_id
        if selected_id is None:
            return None
        for annotation in self.project.annotations:
            if annotation.id == selected_id:
                return annotation
        return None

    def _refresh_canvas(self) -> None:
        label_lookup = {label.id: label for label in self.project.labels}
        annotations = self.project.annotations if self.project.layer_visibility.get("annotations", True) else []
        self.canvas.update_annotations(annotations, label_lookup, self.tool_controller.selected_annotation_id)
        raster_rgba, raster_bbox = self._current_raster_overlay(label_lookup)
        self.canvas.update_raster_mask(raster_rgba, raster_bbox)

    def _autosave_if_needed(self) -> None:
        if not self._dirty:
            return
        if self.current_project_path:
            self.project_manager.save_autosave(self.project, self.current_project_path)

    def _current_raster_overlay(self, label_lookup):
        if not self.project.image_asset or not self.canvas.last_render or not self.project.layer_visibility.get("raster", True):
            return None, None
        x0, y0, width, height = self.canvas.last_render.source_window
        if width <= 0 or height <= 0:
            return None, None
        clipped = []
        for annotation in self.project.annotations:
            if annotation.bbox is None:
                continue
            min_x, min_y, max_x, max_y = annotation.bbox
            if max_x < x0 or max_y < y0 or min_x > x0 + width or min_y > y0 + height:
                continue
            shifted = annotation.clone()
            shifted.exterior = [[pt[0] - x0, pt[1] - y0] for pt in shifted.exterior]
            shifted.holes = [[[pt[0] - x0, pt[1] - y0] for pt in hole] for hole in shifted.holes]
            clipped.append(shifted)
        if not clipped:
            return None, None
        raster_mask = GeometryService.rasterize_annotations(clipped, width, height)
        return GeometryService.colorize_mask(raster_mask, label_lookup), (x0, y0, width, height)

    def _build_preview_from_mask(self, mask, bbox, label_id: int | None = None, source_tool: str = "magic_wand_preview"):
        if mask is None or bbox is None:
            return None
        target_label = label_id or (self.project.active_label_id or 1)
        polygons = GeometryService.mask_to_annotations(
            mask,
            bbox,
            label_id=target_label,
            simplify=self.magic_panel.params().simplify_polygon,
            vector_smoothness=self.magic_panel.params().vector_smoothness,
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
            "当前存在未确认的魔法棒结果。是否先应用？\n选择“否”将取消当前结果。",
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
            return commands
        minx, miny, maxx, maxy = new_union.bounds
        bounds_bbox = (minx, miny, maxx - minx, maxy - miny)
        for annotation in self.project.annotations:
            if not GeometryService.bbox_intersects(annotation.bbox, bounds_bbox):
                continue
            polygon = GeometryService.annotation_to_polygon(annotation)
            if polygon is None or polygon.is_empty or not polygon.intersects(new_union):
                continue
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
        return commands

    def _update_mouse_position(self, payload) -> None:
        if not self.project.image_asset:
            self.mouse_pos_label.setText("行: -, 列: - | 渲染RGB: - | 原值: -")
            return
        row = int(round(payload.y))
        col = int(round(payload.x))
        if 0 <= row < self.project.image_asset.height and 0 <= col < self.project.image_asset.width:
            rendered_rgb = self.canvas.rendered_rgb_at(col, row)
            original_value = self.current_source.read_pixel(col, row) if self.current_source else None
            rgb_text = (
                f"({rendered_rgb[0]}, {rendered_rgb[1]}, {rendered_rgb[2]})"
                if rendered_rgb is not None else "-"
            )
            if isinstance(original_value, list):
                original_text = str(tuple(original_value))
            else:
                original_text = "-" if original_value is None else str(original_value)
            self.mouse_pos_label.setText(
                f"行: {row}, 列: {col} | 渲染RGB: {rgb_text} | 原值: {original_text}"
            )
        else:
            self.mouse_pos_label.setText("行: -, 列: - | 渲染RGB: - | 原值: -")
