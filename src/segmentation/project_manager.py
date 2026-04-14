"""
项目文件与自动保存管理。
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSettings

from .models import SegmentationProject


def get_settings() -> QSettings:
    config_dir = Path.home() / ".toolbox"
    config_dir.mkdir(parents=True, exist_ok=True)
    return QSettings(str(config_dir / "image_segmentation.ini"), QSettings.IniFormat)


class SegmentationProjectManager:
    PROJECT_SUFFIX = ".toolbox-seg.json"

    def __init__(self):
        self.settings = get_settings()

    def serialize_project(self, project: SegmentationProject, project_path: str | None = None) -> dict:
        payload = project.to_dict()
        image_asset = payload.get("image_asset")
        if image_asset and image_asset.get("path") and project_path:
            image_asset["path_mode"], image_asset["path"] = self._project_path_value(
                image_asset["path"], project_path
            )
        return payload

    def save_project(self, project: SegmentationProject, project_path: str) -> None:
        payload = self.serialize_project(project, project_path)
        Path(project_path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.add_recent_project(project_path)

    def load_project(self, project_path: str) -> SegmentationProject:
        payload = json.loads(Path(project_path).read_text(encoding="utf-8"))
        project = SegmentationProject.from_dict(payload)
        if project.image_asset and project.image_asset.path:
            project.image_asset.path = self.resolve_image_path(
                project.image_asset.path,
                project.image_asset.path_mode,
                project_path,
            )
        self.add_recent_project(project_path)
        return project

    def autosave_path(self, project_path: str) -> str:
        return f"{project_path}.autosave"

    def save_autosave(self, project: SegmentationProject, project_path: str) -> str:
        autosave_path = self.autosave_path(project_path)
        self.save_project(project, autosave_path)
        return autosave_path

    def resolve_image_path(self, raw_path: str, path_mode: str, project_path: str) -> str:
        if path_mode == "relative":
            candidate = (Path(project_path).parent / raw_path).resolve()
            if candidate.exists():
                return str(candidate)
        return str(Path(raw_path).resolve())

    def _project_path_value(self, image_path: str, project_path: str) -> tuple[str, str]:
        image = Path(image_path).resolve()
        project_dir = Path(project_path).resolve().parent
        try:
            rel = image.relative_to(project_dir)
            return "relative", rel.as_posix()
        except Exception:
            return "absolute", str(image)

    def add_recent_project(self, project_path: str) -> None:
        projects = self.recent_projects()
        project_path = str(Path(project_path).resolve())
        projects = [item for item in projects if item != project_path]
        projects.insert(0, project_path)
        self.settings.setValue("recent_projects", projects[:10])

    def recent_projects(self) -> list[str]:
        value = self.settings.value("recent_projects", [], type=list)
        return [item for item in value if Path(item).exists()]
