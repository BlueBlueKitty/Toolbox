"""
统一撤销/重做命令栈。
"""

from __future__ import annotations

from typing import Protocol

from .models import AnnotationObject


class ProjectLike(Protocol):
    annotations: list[AnnotationObject]


class BaseCommand:
    text = ""

    def redo(self, project: ProjectLike) -> None:
        raise NotImplementedError

    def undo(self, project: ProjectLike) -> None:
        raise NotImplementedError


class AddAnnotationCommand(BaseCommand):
    text = "添加标注"

    def __init__(self, annotation: AnnotationObject):
        self.annotation = annotation.clone()

    def redo(self, project: ProjectLike) -> None:
        project.annotations.append(self.annotation.clone())

    def undo(self, project: ProjectLike) -> None:
        project.annotations = [item for item in project.annotations if item.id != self.annotation.id]


class DeleteAnnotationCommand(BaseCommand):
    text = "删除标注"

    def __init__(self, annotation: AnnotationObject):
        self.annotation = annotation.clone()

    def redo(self, project: ProjectLike) -> None:
        project.annotations = [item for item in project.annotations if item.id != self.annotation.id]

    def undo(self, project: ProjectLike) -> None:
        project.annotations.append(self.annotation.clone())


class UpdateGeometryCommand(BaseCommand):
    text = "更新几何"

    def __init__(self, annotation_id: str, before: AnnotationObject, after: AnnotationObject):
        self.annotation_id = annotation_id
        self.before = before.clone()
        self.after = after.clone()

    def redo(self, project: ProjectLike) -> None:
        self._replace(project, self.after)

    def undo(self, project: ProjectLike) -> None:
        self._replace(project, self.before)

    def _replace(self, project: ProjectLike, value: AnnotationObject) -> None:
        project.annotations = [
            value.clone() if item.id == self.annotation_id else item
            for item in project.annotations
        ]


class UpdateLabelAssignmentCommand(BaseCommand):
    text = "更新标签"

    def __init__(self, annotation_id: str, before_label_id: int, after_label_id: int):
        self.annotation_id = annotation_id
        self.before_label_id = before_label_id
        self.after_label_id = after_label_id

    def redo(self, project: ProjectLike) -> None:
        self._apply(project, self.after_label_id)

    def undo(self, project: ProjectLike) -> None:
        self._apply(project, self.before_label_id)

    def _apply(self, project: ProjectLike, label_id: int) -> None:
        updated = []
        for item in project.annotations:
            if item.id == self.annotation_id:
                clone = item.clone()
                clone.label_id = label_id
                updated.append(clone)
            else:
                updated.append(item)
        project.annotations = updated


class BatchCommand(BaseCommand):
    text = "批量编辑"

    def __init__(self, commands: list[BaseCommand]):
        self.commands = commands[:]

    def redo(self, project: ProjectLike) -> None:
        for command in self.commands:
            command.redo(project)

    def undo(self, project: ProjectLike) -> None:
        for command in reversed(self.commands):
            command.undo(project)


class CommandStack:
    def __init__(self, project: ProjectLike):
        self.project = project
        self._undo: list[BaseCommand] = []
        self._redo: list[BaseCommand] = []

    def push(self, command: BaseCommand) -> None:
        command.redo(self.project)
        self._undo.append(command)
        self._redo.clear()

    def undo(self) -> bool:
        if not self._undo:
            return False
        command = self._undo.pop()
        command.undo(self.project)
        self._redo.append(command)
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        command = self._redo.pop()
        command.redo(self.project)
        self._undo.append(command)
        return True

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)
