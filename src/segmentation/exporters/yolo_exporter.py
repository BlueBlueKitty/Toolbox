"""
YOLO segmentation 导出。
"""

from __future__ import annotations

from pathlib import Path

from ..models import SegmentationProject


def export_yolo(project: SegmentationProject, output_path: str) -> None:
    """export_yolo。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        project (SegmentationProject): 输入参数。
        output_path (str): 输入参数。
    返回:
        None: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    if project.image_asset is None:
        raise ValueError("缺少图像元信息")
    width = project.image_asset.width
    height = project.image_asset.height
    lines = []
    for annotation in project.annotations:
        points = []
        for x, y in annotation.exterior[:-1]:
            points.append(f"{x / width:.6f}")
            points.append(f"{y / height:.6f}")
        lines.append(" ".join([str(annotation.label_id - 1)] + points))
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
