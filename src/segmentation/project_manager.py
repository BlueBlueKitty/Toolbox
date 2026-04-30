"""
项目文件与自动保存管理。
"""

from __future__ import annotations

import json
from pathlib import Path
import numpy as np

from PySide6.QtCore import QSettings

from .models import AnnotationObject, SegmentationProject


def get_settings() -> QSettings:
    """get_settings。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        无。
    返回:
        QSettings: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    config_dir = Path.home() / ".toolbox"
    config_dir.mkdir(parents=True, exist_ok=True)
    return QSettings(str(config_dir / "image_segmentation.ini"), QSettings.IniFormat)


class SegmentationProjectManager:
    PROJECT_SUFFIX = ".seg_proj"
    LEGACY_PROJECT_SUFFIX = ".toolbox-seg.json"

    def __init__(self):
        """__init__。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self.settings = get_settings()

    def serialize_project(self, project: SegmentationProject, project_path: str | None = None) -> dict:
        """serialize_project。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (SegmentationProject): 输入参数。
            project_path (str | None): 输入参数。
        返回:
            dict: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        payload = project.to_dict()
        image_asset = payload.get("image_asset")
        if image_asset and image_asset.get("path"):
            # 为了兼容旧版本读取，输出精简后的基础字段集合。
            legacy_image_asset = {
                "id": image_asset.get("id"),
                "path": image_asset.get("path"),
                "path_mode": image_asset.get("path_mode", "absolute"),
                "width": image_asset.get("width"),
                "height": image_asset.get("height"),
                "band_count": image_asset.get("band_count"),
                "dtype": image_asset.get("dtype"),
                "nodata": image_asset.get("nodata"),
                "crs_wkt": image_asset.get("crs_wkt"),
                "geotransform": image_asset.get("geotransform"),
                "resolution": image_asset.get("resolution"),
                "has_georef": image_asset.get("has_georef", False),
                "overview_levels": image_asset.get("overview_levels", []),
            }
            image_asset["path_mode"] = "absolute"
            image_asset["path"] = str(Path(image_asset["path"]).resolve())
            legacy_image_asset["path_mode"] = "absolute"
            legacy_image_asset["path"] = image_asset["path"]
            payload["image_asset"] = legacy_image_asset
        if project_path:
            # 运行时矢量自动保存入口暂时关闭，仅保留代码路径以便后续恢复。
            # vector_path = self.vector_sidecar_path(project_path)
            # Path(vector_path).write_text(
            #     json.dumps([annotation.to_dict() for annotation in project.annotations], ensure_ascii=False, indent=2),
            #     encoding="utf-8",
            # )
            # payload["annotations_asset"] = {
            #     "path_mode": "relative",
            #     "path": Path(vector_path).name,
            #     "format": "json",
            # }
            payload["annotations_asset"] = {}
            if project.mask_data is not None:
                mask_path = self.mask_sidecar_path(project_path)
                np.savez_compressed(mask_path, mask=project.mask_data)
                payload["mask_asset"] = {
                    "path_mode": "relative",
                    "path": Path(mask_path).name,
                    "dtype": str(project.mask_data.dtype),
                }
            else:
                payload["mask_asset"] = {}
        return payload

    def save_project(self, project: SegmentationProject, project_path: str) -> None:
        """save_project。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (SegmentationProject): 输入参数。
            project_path (str): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        payload = self.serialize_project(project, project_path)
        Path(project_path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.add_recent_project(project_path)

    def load_project(self, project_path: str) -> SegmentationProject:
        """load_project。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project_path (str): 输入参数。
        返回:
            SegmentationProject: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        payload = json.loads(Path(project_path).read_text(encoding="utf-8"))
        project = SegmentationProject.from_dict(payload)
        if project.image_asset and project.image_asset.path:
            project.image_asset.path = self.resolve_image_path(
                project.image_asset.path,
                project.image_asset.path_mode,
                project_path,
            )
        annotations_asset = project.annotations_asset or {}
        annotations_path = annotations_asset.get("path")
        if annotations_path:
            resolved_annotations_path = self.resolve_image_path(
                annotations_path,
                annotations_asset.get("path_mode", "absolute"),
                project_path,
            )
            try:
                annotations_payload = json.loads(Path(resolved_annotations_path).read_text(encoding="utf-8"))
                project.annotations = [AnnotationObject.from_dict(item) for item in annotations_payload]
                project.annotations_asset["path"] = resolved_annotations_path
                project.annotations_asset["path_mode"] = "absolute"
            except Exception:
                project.annotations = []
        mask_asset = project.mask_asset or {}
        mask_path = mask_asset.get("path")
        if mask_path:
            resolved_mask_path = self.resolve_image_path(mask_path, mask_asset.get("path_mode", "absolute"), project_path)
            try:
                with np.load(resolved_mask_path) as loaded:
                    project.mask_data = loaded["mask"]
                project.mask_asset["path"] = resolved_mask_path
                project.mask_asset["path_mode"] = "absolute"
            except Exception:
                project.mask_data = None
        self.add_recent_project(project_path)
        return project

    def autosave_path(self, project_path: str) -> str:
        """autosave_path。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project_path (str): 输入参数。
        返回:
            str: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        return f"{project_path}.autosave"

    def mask_sidecar_path(self, project_path: str) -> str:
        """mask_sidecar_path。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project_path (str): 输入参数。
        返回:
            str: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        return f"{project_path}.mask.npz"

    def vector_sidecar_path(self, project_path: str) -> str:
        """vector_sidecar_path。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project_path (str): 输入参数。
        返回:
            str: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        return f"{project_path}.annotations.json"

    def save_autosave(self, project: SegmentationProject, project_path: str) -> str:
        """save_autosave。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (SegmentationProject): 输入参数。
            project_path (str): 输入参数。
        返回:
            str: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        autosave_path = self.autosave_path(project_path)
        self.save_project(project, autosave_path)
        return autosave_path

    def resolve_image_path(self, raw_path: str, path_mode: str, project_path: str) -> str:
        """resolve_image_path。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            raw_path (str): 输入参数。
            path_mode (str): 输入参数。
            project_path (str): 输入参数。
        返回:
            str: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        复杂度:
            时间和空间复杂度与输入规模线性或近线性相关。
        """
        if path_mode == "relative":
            candidate = (Path(project_path).parent / raw_path).resolve()
            if candidate.exists():
                return str(candidate)
        return str(Path(raw_path).resolve())

    def _project_path_value(self, image_path: str, project_path: str) -> tuple[str, str]:
        """_project_path_value。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            image_path (str): 输入参数。
            project_path (str): 输入参数。
        返回:
            tuple[str, str]: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        image = Path(image_path).resolve()
        project_dir = Path(project_path).resolve().parent
        try:
            rel = image.relative_to(project_dir)
            return "relative", rel.as_posix()
        except Exception:
            return "absolute", str(image)

    def add_recent_project(self, project_path: str) -> None:
        """add_recent_project。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project_path (str): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        projects = self.recent_projects()
        project_path = str(Path(project_path).resolve())
        projects = [item for item in projects if item != project_path]
        projects.insert(0, project_path)
        self.settings.setValue("recent_projects", projects[:10])

    def recent_projects(self) -> list[str]:
        """recent_projects。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            list[str]: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        value = self.settings.value("recent_projects", [], type=list)
        return [item for item in value if Path(item).exists()]
