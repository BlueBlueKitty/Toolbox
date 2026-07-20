"""图像分割工具的普通栅格格式回归测试。"""

import numpy as np
from PIL import Image

from src.rendering.raster_source_utils import (
    SEGMENTATION_RASTER_EXTENSIONS,
    is_segmentation_raster_file,
    open_raster_source,
)


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
