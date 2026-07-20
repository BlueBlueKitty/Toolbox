"""图像分割工具的普通栅格格式回归测试。"""

import numpy as np
import pytest
from osgeo import gdal
from PIL import Image
from PySide6.QtWidgets import QApplication

from src.dialogs.image_segmentation_dialog import ImageSegmentationDialog
from src.dialogs.segmentation_export_dialog import SegmentationExportDialog
from src.rendering.models import ImageSourceMetadata
from src.rendering.raster_source_utils import (
    SEGMENTATION_RASTER_EXTENSIONS,
    is_segmentation_raster_file,
    open_raster_source,
)
from src.segmentation.exporters.mask_exporter import export_mask_file
from src.segmentation.models import LabelClass, SegmentationProject


def test_segmentation_raster_extensions_match_local_image_viewer_formats():
    assert SEGMENTATION_RASTER_EXTENSIONS == (
        ".tif", ".tiff", ".grd", ".png", ".jpg", ".jpeg", ".bmp",
    )
    assert is_segmentation_raster_file("example.BMP")
    assert is_segmentation_raster_file("example.GrD")
    assert not is_segmentation_raster_file("example.img")


def test_open_raster_source_reads_bmp_image(tmp_path):
    image_path = tmp_path / "sample.BMP"
    expected = np.array([[[10, 20, 30], [40, 50, 60]]], dtype=np.uint8)
    Image.fromarray(expected, mode="RGB").save(image_path)

    source = open_raster_source(str(image_path), pyramid_threshold_mb=1024)

    metadata = source.metadata()
    assert (metadata.width, metadata.height, metadata.band_count) == (2, 1, 3)
    np.testing.assert_array_equal(source.read_window_native(0, 0, 2, 1), expected)


def _mask_project(labels: list[LabelClass], mask: np.ndarray) -> SegmentationProject:
    project = SegmentationProject(
        project_version="1.0",
        image_asset=ImageSourceMetadata(
            id="demo",
            path="demo.tif",
            path_mode="absolute",
            width=mask.shape[1],
            height=mask.shape[0],
            band_count=1,
            dtype="UInt16",
            nodata=None,
            crs_wkt="LOCAL_CS[\"test\"]",
            geotransform=(10.0, 2.0, 0.0, 20.0, 0.0, -2.0),
            resolution=(2.0, -2.0),
            has_georef=True,
            overview_levels=[],
        ),
        labels=labels,
    )
    project.mask_data = mask
    return project


@pytest.mark.parametrize("suffix", [".png", ".bmp", ".tif"])
def test_indexed_mask_exports_contiguous_single_band_values(tmp_path, suffix):
    project = _mask_project(
        [
            LabelClass(id=9, name="First", color="#ff0000", shortcut="1"),
            LabelClass(id=3, name="Second", color="#00ff00", shortcut="2"),
        ],
        np.array([[0, 9, 3], [3, 0, 9]], dtype=np.uint16),
    )
    output_path = tmp_path / f"mask{suffix}"

    export_mask_file(project, str(output_path), encoding="indexed")

    expected = np.array([[0, 1, 2], [2, 0, 1]], dtype=np.uint8)
    if suffix == ".tif":
        dataset = gdal.Open(str(output_path))
        assert dataset.RasterCount == 1
        np.testing.assert_array_equal(dataset.GetRasterBand(1).ReadAsArray(), expected)
        assert dataset.GetGeoTransform() == project.image_asset.geotransform
        dataset = None
    else:
        image = Image.open(output_path)
        assert np.asarray(image).ndim == 2
        np.testing.assert_array_equal(np.asarray(image), expected)


@pytest.mark.parametrize("suffix", [".png", ".bmp", ".tif"])
def test_single_label_mask_exports_as_binary_mask(tmp_path, suffix):
    project = _mask_project(
        [
            LabelClass(id=9, name="First", color="#ff0000", shortcut="1"),
            LabelClass(id=3, name="Second", color="#00ff00", shortcut="2"),
        ],
        np.array([[0, 9, 3], [3, 0, 9]], dtype=np.uint16),
    )
    output_path = tmp_path / f"mask2{suffix}"

    export_mask_file(project, str(output_path), binary_label_id=3, encoding="indexed")

    expected = np.array([[0, 0, 1], [1, 0, 0]], dtype=np.uint8)
    if suffix == ".tif":
        dataset = gdal.Open(str(output_path))
        np.testing.assert_array_equal(dataset.GetRasterBand(1).ReadAsArray(), expected)
        dataset = None
    else:
        np.testing.assert_array_equal(np.asarray(Image.open(output_path)), expected)


def test_single_label_colored_mask_excludes_other_labels(tmp_path):
    project = _mask_project(
        [
            LabelClass(id=1, name="Red", color="#ff0000", shortcut="1"),
            LabelClass(id=2, name="Green", color="#00ff00", shortcut="2"),
        ],
        np.array([[1, 2]], dtype=np.uint16),
    )
    output_path = tmp_path / "mask1.png"

    export_mask_file(project, str(output_path), binary_label_id=1, encoding="colored")

    np.testing.assert_array_equal(
        np.asarray(Image.open(output_path)),
        np.array([[[255, 0, 0], [0, 0, 0]]], dtype=np.uint8),
    )


@pytest.mark.parametrize("suffix", [".png", ".bmp"])
def test_colored_mask_exports_rgb_for_common_image_formats(tmp_path, suffix):
    project = _mask_project(
        [LabelClass(id=1, name="A", color="#ff0000", shortcut="1")],
        np.array([[0, 1]], dtype=np.uint16),
    )
    output_path = tmp_path / f"colored{suffix}"

    export_mask_file(project, str(output_path), encoding="colored")

    image = Image.open(output_path)
    assert np.asarray(image).shape == (1, 2, 3)
    assert np.asarray(image)[0, 1].tolist() == [255, 0, 0]


def test_indexed_mask_rejects_unknown_label_ids(tmp_path):
    project = _mask_project(
        [LabelClass(id=1, name="A", color="#ff0000", shortcut="1")],
        np.array([[0, 2]], dtype=np.uint16),
    )

    with pytest.raises(ValueError, match="未定义的标签 ID：2"):
        export_mask_file(project, str(tmp_path / "invalid.png"), encoding="indexed")


def test_indexed_bmp_rejects_more_than_255_labels(tmp_path):
    labels = [LabelClass(id=index, name=f"L{index}", color="#000000", shortcut="") for index in range(1, 257)]
    project = _mask_project(labels, np.array([[256]], dtype=np.uint16))

    with pytest.raises(ValueError, match="最多支持 255 个标签"):
        export_mask_file(project, str(tmp_path / "too-many.bmp"), encoding="indexed")


def test_export_dialog_defaults_to_indexed_masks_and_lists_common_formats():
    app = QApplication.instance() or QApplication([])
    dialog = SegmentationExportDialog("mask", ".", has_geo=False, prefer_tif_mask=False)

    settings = dialog._current_settings()
    assert set(SegmentationExportDialog.MASK_FORMATS) == {"PNG", "BMP", "GeoTIFF"}
    assert settings["mask_extension"] == ".png"
    assert settings["mask_encoding"] == "indexed"
    assert settings["export_split_masks"] is False
    dialog.close()


def test_export_directory_is_read_only_from_saved_project_preferences():
    project = _mask_project([], np.zeros((1, 1), dtype=np.uint8))
    assert ImageSegmentationDialog._project_export_output_dir(project) == ""

    project.export_prefs = {"output_dir": "D:/exports"}
    assert ImageSegmentationDialog._project_export_output_dir(project) == "D:/exports"

    project.export_prefs = {"output_dir": None}
    assert ImageSegmentationDialog._project_export_output_dir(project) == ""
