"""
Pascal VOC 导出。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ..models import SegmentationProject


def export_voc(project: SegmentationProject, output_path: str) -> None:
    """export_voc。

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
    labels = {label.id: label.name for label in project.labels}
    root = ET.Element("annotation")
    ET.SubElement(root, "filename").text = project.image_asset.path
    size = ET.SubElement(root, "size")
    ET.SubElement(size, "width").text = str(project.image_asset.width)
    ET.SubElement(size, "height").text = str(project.image_asset.height)
    ET.SubElement(size, "depth").text = str(project.image_asset.band_count)
    for annotation in project.annotations:
        bbox = annotation.bbox or [0, 0, 0, 0]
        obj = ET.SubElement(root, "object")
        ET.SubElement(obj, "name").text = labels.get(annotation.label_id, str(annotation.label_id))
        box = ET.SubElement(obj, "bndbox")
        ET.SubElement(box, "xmin").text = str(int(bbox[0]))
        ET.SubElement(box, "ymin").text = str(int(bbox[1]))
        ET.SubElement(box, "xmax").text = str(int(bbox[2]))
        ET.SubElement(box, "ymax").text = str(int(bbox[3]))
    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)
