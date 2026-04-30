"""
COCO instance segmentation 导出。
"""

from __future__ import annotations

import json

from ..models import SegmentationProject


def export_coco(project: SegmentationProject, output_path: str) -> None:
    """export_coco。

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
    payload = {
        "images": [
            {
                "id": 1,
                "file_name": project.image_asset.path,
                "width": project.image_asset.width,
                "height": project.image_asset.height,
            }
        ],
        "categories": [
            {"id": label.id, "name": label.name}
            for label in project.labels
        ],
        "annotations": [],
    }
    for index, annotation in enumerate(project.annotations, start=1):
        segmentation = [[coord for point in annotation.exterior[:-1] for coord in point]]
        bbox = annotation.bbox or [0, 0, 0, 0]
        payload["annotations"].append(
            {
                "id": index,
                "image_id": 1,
                "category_id": annotation.label_id,
                "segmentation": segmentation,
                "bbox": [
                    bbox[0],
                    bbox[1],
                    max(bbox[2] - bbox[0], 0),
                    max(bbox[3] - bbox[1], 0),
                ],
                "iscrowd": 0,
                "area": max((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), 0),
            }
        )
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
