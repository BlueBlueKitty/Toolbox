import numpy as np
import pytest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.dialogs.image_segmentation_dialog import ImageSegmentationDialog
from src.rendering.layer_panel_controller import LayerPanelController
from src.rendering.models import LayerSpec
from src.widgets.layer_panel_widget import LayerPanelWidget
from src.widgets.segmentation_canvas import SegmentationCanvas


INTERNAL_SEGMENTATION_LAYERS = {
    "annotations", "draft", "snap", "preview_vector",
}


def _layer_ids(canvas):
    return [state.spec.id for state in canvas.layer_manager.layers()]


def test_flat_layer_panel_reorders_managed_layers_as_one_stack():
    app = QApplication.instance() or QApplication([])
    canvas = SegmentationCanvas()
    canvas.update_raster_mask(np.full((2, 2, 4), 255, dtype=np.uint8), (0, 0, 2, 2))
    canvas.set_raster_overlay("aux_raster", np.full((2, 2, 4), 255, dtype=np.uint8), (0, 0, 2, 2))
    panel = LayerPanelWidget()
    controller = LayerPanelController(panel, canvas, exclude_layer_ids=INTERNAL_SEGMENTATION_LAYERS)
    controller.rebuild_panel_items()

    # The panel is high -> low.  Mask must therefore render over the base and
    # the auxiliary raster must stay at the bottom.
    controller._handle_order_changed(["preview_mask", "mask", "base_raster", "aux_raster"])
    app.processEvents()

    assert _layer_ids(canvas) == [
        "aux_raster", "base_raster", "mask", "preview_mask",
        "annotations", "preview_vector", "draft", "snap",
    ]
    assert panel.layer_order() == ["preview_mask", "mask", "base_raster", "aux_raster"]
    assert panel.layer_tree.itemWidget(panel.layer_tree.topLevelItem(0), 1) is not None
    assert not bool(panel.layer_tree.topLevelItem(0).flags() & Qt.ItemIsDropEnabled)


def test_flat_tree_drag_moves_only_top_level_rows_and_emits_final_order():
    app = QApplication.instance() or QApplication([])
    panel = LayerPanelWidget()
    panel.set_layers([
        LayerSpec("mask", "Mask", "raster"),
        LayerSpec("base_raster", "图像", "raster", locked=True),
        LayerSpec("aux", "辅助", "raster"),
    ])
    emitted_orders = []
    panel.order_changed.connect(emitted_orders.append)

    panel.layer_tree._move_top_level_item(panel.layer_tree.topLevelItem(2), 0)
    app.processEvents()

    assert panel.layer_order() == ["aux", "mask", "base_raster"]
    assert emitted_orders == [["aux", "mask", "base_raster"]]
    assert panel.layer_tree.topLevelItemCount() == 3
    assert panel.layer_tree.topLevelItem(0).childCount() == 0


def test_flat_tree_shows_and_clears_drag_insertion_indicator():
    app = QApplication.instance() or QApplication([])
    panel = LayerPanelWidget()
    panel.set_layers([LayerSpec("a", "A", "raster"), LayerSpec("b", "B", "raster")])
    panel.resize(360, 180)
    panel.show()
    app.processEvents()

    panel.layer_tree._show_drop_indicator(1)
    assert panel.layer_tree._drop_indicator.isVisible()
    expected_y = panel.layer_tree.visualItemRect(panel.layer_tree.topLevelItem(1)).top() - 1
    assert panel.layer_tree._drop_indicator.y() == expected_y
    panel.layer_tree._drop_indicator.hide()
    assert not panel.layer_tree._drop_indicator.isVisible()


def test_global_visibility_temporarily_disables_per_window_controls():
    app = QApplication.instance() or QApplication([])
    dialog = ImageSegmentationDialog()
    dialog._layer_window_visibility["mask"] = {"viewer_1": True, "viewer_2": False}
    dialog.project.layer_visibility["mask"] = True
    dialog._rebuild_layer_panel_items()

    dialog._layer_visibility_callback("mask", False)
    viewer1_check = dialog.layer_panel._window_checkboxes[("mask", "viewer_1")]
    viewer2_check = dialog.layer_panel._window_checkboxes[("mask", "viewer_2")]
    assert not viewer1_check.isChecked() and not viewer2_check.isChecked()
    assert not viewer1_check.isEnabled() and not viewer2_check.isEnabled()
    assert not dialog._canvas_for_window("viewer_1").layer_manager.layer("mask").spec.visible
    assert not dialog._canvas_for_window("viewer_2").layer_manager.layer("mask").spec.visible

    dialog._layer_visibility_callback("mask", True)
    assert viewer1_check.isChecked() and not viewer2_check.isChecked()
    assert viewer1_check.isEnabled() and viewer2_check.isEnabled()
    assert dialog._canvas_for_window("viewer_1").layer_manager.layer("mask").spec.visible
    assert not dialog._canvas_for_window("viewer_2").layer_manager.layer("mask").spec.visible


def test_layer_order_and_window_visibility_preferences_round_trip():
    app = QApplication.instance() or QApplication([])
    dialog = ImageSegmentationDialog()
    dialog._layer_window_visibility = {"mask": {"viewer_1": False, "viewer_2": True}}
    dialog._save_layer_order_to_project()
    dialog._save_layer_window_visibility_to_project()

    assert dialog.project.export_prefs["layer_order"] == list(reversed(dialog.layer_panel.layer_order()))
    assert dialog.project.export_prefs["layer_window_visibility"]["mask"] == {
        "viewer_1": False,
        "viewer_2": True,
    }

    dialog._layer_window_visibility = {}
    dialog._restore_layer_window_visibility_from_project()
    assert dialog._layer_window_visibility["mask"] == {"viewer_1": False, "viewer_2": True}
    app.processEvents()


def test_rebuilding_panel_keeps_active_layer_selected():
    app = QApplication.instance() or QApplication([])
    canvas = SegmentationCanvas()
    panel = LayerPanelWidget()
    controller = LayerPanelController(panel, canvas, exclude_layer_ids=INTERNAL_SEGMENTATION_LAYERS)
    controller.bind()
    canvas.layer_manager.set_active_layer("mask")

    controller.rebuild_panel_items()
    app.processEvents()

    assert canvas.layer_manager.active_layer_id() == "mask"
    assert panel.layer_tree.currentItem().data(0, Qt.UserRole) == "mask"


def test_preview_mask_is_opaque_and_visible_in_layer_panel():
    app = QApplication.instance() or QApplication([])
    dialog = ImageSegmentationDialog()
    dialog.canvas.update_preview_mask_layer(
        np.array([[0, 1], [1, 0]], dtype=np.uint8),
        (3, 4, 2, 2),
        "#123456",
    )
    preview_state = dialog.canvas.layer_manager.layer("preview_mask")

    assert "preview_mask" in dialog.layer_panel.layer_order()
    assert preview_state.spec.opacity == 1.0
    assert preview_state.item.image[0, 1].tolist() == [18, 52, 86, 255]


def test_magic_wand_reenables_preview_mask_layer():
    app = QApplication.instance() or QApplication([])
    dialog = ImageSegmentationDialog()
    dialog._layer_visibility_callback("preview_mask", False)

    dialog._ensure_preview_mask_layer_visible_for_magic()

    assert dialog.project.layer_visibility["preview_mask"] is True
    assert dialog.canvas.layer_manager.layer("preview_mask").spec.visible
    assert dialog.layer_panel._item_for_layer("preview_mask").checkState(0) == Qt.Checked


def test_default_preview_display_does_not_start_marching_ants(monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog = ImageSegmentationDialog()
    dialog._preview_mask = np.ones((1, 1), dtype=np.uint8)
    dialog._preview_bbox = (0, 0, 1, 1)
    queued = []
    monkeypatch.setattr(dialog, "_queue_preview_outline", lambda *args: queued.append(args))

    dialog._update_preview_display()

    assert queued == []
    assert not dialog._preview_mask_outline_timer.isActive()
    assert dialog._preview_outline_path is None


def test_partial_auxiliary_import_syncs_successes_and_reports_failures(monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog = ImageSegmentationDialog()
    imported = []
    lifecycle = []

    def import_raster(path):
        imported.append(path)
        if path.endswith("broken.tif"):
            raise RuntimeError("损坏文件")

    monkeypatch.setattr(dialog, "_import_aux_raster", import_raster)
    monkeypatch.setattr(dialog, "_rebuild_layer_panel_items", lambda: lifecycle.append("rebuild"))
    monkeypatch.setattr(dialog, "_refresh_canvas", lambda: lifecycle.append("refresh"))
    monkeypatch.setattr(dialog, "_set_dirty", lambda value: lifecycle.append(("dirty", value)))

    with pytest.raises(RuntimeError, match="broken.tif.*损坏文件"):
        dialog._import_auxiliary_paths(["good.tif", "broken.tif"])

    assert imported == ["good.tif", "broken.tif"]
    assert lifecycle == ["rebuild", "refresh", ("dirty", True)]
    app.processEvents()


def test_hidden_auxiliary_layers_are_not_refreshed(monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog = ImageSegmentationDialog()
    dialog._auxiliary_layers = [{"id": "aux", "type": "raster"}]
    for canvas in dialog._all_canvases():
        canvas.set_raster_overlay("aux", None, None, name="辅助栅格")
    rendered_in = []
    monkeypatch.setattr(dialog, "_refresh_aux_raster_layer", lambda _layer, *, canvas: rendered_in.append(canvas))

    dialog.project.layer_visibility["aux"] = False
    dialog._refresh_auxiliary_layers()
    assert rendered_in == []

    dialog.project.layer_visibility["aux"] = True
    dialog._layer_window_visibility["aux"] = {"viewer_1": False, "viewer_2": False}
    dialog._refresh_auxiliary_layers()
    assert rendered_in == []

    dialog._layer_window_visibility["aux"]["viewer_1"] = True
    dialog._refresh_auxiliary_layers()
    assert rendered_in == [dialog._canvas_for_window("viewer_1")]
    app.processEvents()
