"""
图像分割工具主窗口。
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from src.segmentation import (
    AddAnnotationCommand,
    AnnotationObject,
    CommandStack,
    DeleteAnnotationCommand,
    ImageAsset,
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
        self.current_source = None
        self.current_project_path: str | None = None
        self.preview_selection = None
        self._last_magic_seed = None
        self._dirty = False
        self._vertex_update_pending: dict[str, AnnotationObject] = {}

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

        self.open_action = QAction("打开图像", self)
        self.open_project_action = QAction("打开项目", self)
        self.save_project_action = QAction("保存项目", self)
        self.export_action = QAction("导出...", self)
        self.undo_action = QAction("撤销", self)
        self.redo_action = QAction("重做", self)
        self.fit_action = QAction("适应窗口", self)
        self.actual_size_action = QAction("1:1", self)
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

        self.tool_combo = QComboBox()
        self.tool_combo.addItem("浏览", SegmentationToolController.TOOL_BROWSE)
        self.tool_combo.addItem("矩形框", SegmentationToolController.TOOL_RECTANGLE)
        self.tool_combo.addItem("多边形", SegmentationToolController.TOOL_POLYGON)
        self.tool_combo.addItem("魔法棒", SegmentationToolController.TOOL_MAGIC_WAND)
        self.toolbar.addWidget(QLabel(" 工具: "))
        self.toolbar.addWidget(self.tool_combo)

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

        self.status_label = QLabel("未打开图像")
        main_layout.addWidget(self.status_label)

    def _bind_signals(self) -> None:
        self.open_action.triggered.connect(self.open_image)
        self.open_project_action.triggered.connect(self.open_project)
        self.save_project_action.triggered.connect(self.save_project)
        self.export_action.triggered.connect(self.export_data)
        self.undo_action.triggered.connect(self.undo)
        self.redo_action.triggered.connect(self.redo)
        self.fit_action.triggered.connect(self.canvas.fit_image)
        self.actual_size_action.triggered.connect(self.canvas.set_one_to_one)
        self.tool_combo.currentIndexChanged.connect(self._on_tool_changed)

        self.canvas.mouse_pressed.connect(self._handle_mouse_press)
        self.canvas.mouse_moved.connect(self.tool_controller.handle_move)
        self.canvas.mouse_released.connect(self._handle_mouse_release)

        self.tool_controller.polygon_finished.connect(self._add_polygon_annotation)
        self.tool_controller.rectangle_finished.connect(self._add_rectangle_annotation)
        self.tool_controller.magic_wand_requested.connect(self._run_magic_wand_preview)
        self.tool_controller.selection_changed.connect(self._on_selection_changed)
        self.tool_controller.geometry_changed.connect(self._on_geometry_changed)

        self.label_panel.active_label_changed.connect(self._set_active_label)
        self.label_panel.labels_changed.connect(self._replace_labels)
        self.layer_panel.visibility_changed.connect(self._on_layer_visibility_changed)
        self.magic_panel.params_changed.connect(self._rerun_magic_preview)
        self.magic_panel.confirm_requested.connect(self._confirm_magic_preview)
        self.magic_panel.cancel_requested.connect(self._clear_magic_preview)
        self.assign_label_combo.currentIndexChanged.connect(self._assign_selected_label)

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self.redo)
        QShortcut(QKeySequence(Qt.Key_Delete), self, activated=self.delete_selected)
        QShortcut(QKeySequence(Qt.Key_Backspace), self, activated=self._backspace_action)
        QShortcut(QKeySequence(Qt.Key_Return), self, activated=self.tool_controller.finish_polygon)
        QShortcut(QKeySequence(Qt.Key_Enter), self, activated=self.tool_controller.finish_polygon)
        for idx in range(1, 10):
            QShortcut(QKeySequence(str(idx)), self, activated=lambda value=idx: self._activate_label_shortcut(str(value)))

    def _activate_label_shortcut(self, shortcut: str) -> None:
        for label in self.project.labels:
            if label.shortcut == shortcut:
                self.project.active_label_id = label.id
                self.label_panel.set_labels(self.project.labels, label.id)
                break

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
        self.project.active_label_id = label_id

    def _refresh_label_ui(self) -> None:
        self.label_panel.set_labels(self.project.labels, self.project.active_label_id)
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
        self.canvas.set_image_source(source)
        self.tool_controller.set_annotations(self.project.annotations)
        self.status_label.setText(f"{os.path.basename(meta.path)} | {meta.width} x {meta.height}")
        self._replace_labels(self.project.labels)
        self._clear_magic_preview()
        self._refresh_canvas()

    def open_project(self) -> None:
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
        else:
            if self.tool_controller.remove_selected_vertex():
                self._set_dirty(True)

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

    def _on_tool_changed(self, index: int) -> None:
        self.tool_controller.set_tool(self.tool_combo.itemData(index))

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
        scale_x = win_w / max(rendered.shape[1], 1)
        scale_y = win_h / max(rendered.shape[0], 1)
        mapped_annotations = []
        for annotation in preview.polygon_preview:
            mapped = annotation.clone()
            mapped.exterior = [
                [win_x + point[0] * scale_x, win_y + point[1] * scale_y]
                for point in mapped.exterior
            ]
            mapped.holes = [
                [[win_x + point[0] * scale_x, win_y + point[1] * scale_y] for point in hole]
                for hole in mapped.holes
            ]
            xs = [pt[0] for pt in mapped.exterior]
            ys = [pt[1] for pt in mapped.exterior]
            mapped.bbox = [min(xs), min(ys), max(xs), max(ys)]
            mapped_annotations.append(mapped)
        bx, by, bw, bh = preview.bbox
        preview.bbox = (
            int(win_x + bx * scale_x),
            int(win_y + by * scale_y),
            max(int(bw * scale_x), 1),
            max(int(bh * scale_y), 1),
        )
        preview.polygon_preview = mapped_annotations
        self.preview_selection = preview
        self._last_magic_seed = (x, y)
        self.canvas.update_preview_mask(preview.mask, preview.bbox)

    def _rerun_magic_preview(self, _params) -> None:
        if self._last_magic_seed is None or self.tool_controller.active_tool != SegmentationToolController.TOOL_MAGIC_WAND:
            return
        self._run_magic_wand_preview(*self._last_magic_seed)

    def _confirm_magic_preview(self) -> None:
        if not self.preview_selection or self.project.active_label_id is None:
            return
        added = []
        for annotation in self.preview_selection.polygon_preview:
            annotation.label_id = self.project.active_label_id
            annotation.source_tool = "magic_wand"
            added.append(AddAnnotationCommand(annotation))
        for command in added:
            self.command_stack.push(command)
        self.tool_controller.set_annotations(self.project.annotations)
        self._clear_magic_preview()
        self._refresh_canvas()
        self._set_dirty(True)

    def _clear_magic_preview(self) -> None:
        self.preview_selection = None
        self._last_magic_seed = None
        self.canvas.update_preview_mask(None, None)

    def _on_layer_visibility_changed(self, layer_name: str, visible: bool) -> None:
        self.project.layer_visibility[layer_name] = visible
        if layer_name == "image":
            self.canvas.image_item.setVisible(visible)
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

    def _autosave_if_needed(self) -> None:
        if not self._dirty:
            return
        if self.current_project_path:
            self.project_manager.save_autosave(self.project, self.current_project_path)
