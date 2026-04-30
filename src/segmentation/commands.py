"""
统一撤销/重做命令栈。
"""

from __future__ import annotations

from typing import Protocol
import numpy as np

from .models import AnnotationObject


class ProjectLike(Protocol):
    annotations: list[AnnotationObject]
    mask_data: np.ndarray | None


class BaseCommand:
    text = ""

    def redo(self, project: ProjectLike) -> None:
        """redo。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (ProjectLike): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        raise NotImplementedError

    def undo(self, project: ProjectLike) -> None:
        """undo。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (ProjectLike): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        raise NotImplementedError


class AddAnnotationCommand(BaseCommand):
    text = "添加标注"

    def __init__(self, annotation: AnnotationObject):
        """__init__。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotation (AnnotationObject): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self.annotation = annotation.clone()

    def redo(self, project: ProjectLike) -> None:
        """redo。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (ProjectLike): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        project.annotations.append(self.annotation.clone())

    def undo(self, project: ProjectLike) -> None:
        """undo。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (ProjectLike): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        project.annotations = [item for item in project.annotations if item.id != self.annotation.id]


class DeleteAnnotationCommand(BaseCommand):
    text = "删除标注"

    def __init__(self, annotation: AnnotationObject):
        """__init__。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotation (AnnotationObject): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self.annotation = annotation.clone()

    def redo(self, project: ProjectLike) -> None:
        """redo。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (ProjectLike): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        project.annotations = [item for item in project.annotations if item.id != self.annotation.id]

    def undo(self, project: ProjectLike) -> None:
        """undo。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (ProjectLike): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        project.annotations.append(self.annotation.clone())


class UpdateGeometryCommand(BaseCommand):
    text = "更新几何"

    def __init__(self, annotation_id: str, before: AnnotationObject, after: AnnotationObject):
        """__init__。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotation_id (str): 输入参数。
            before (AnnotationObject): 输入参数。
            after (AnnotationObject): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self.annotation_id = annotation_id
        self.before = before.clone()
        self.after = after.clone()

    def redo(self, project: ProjectLike) -> None:
        """redo。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (ProjectLike): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self._replace(project, self.after)

    def undo(self, project: ProjectLike) -> None:
        """undo。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (ProjectLike): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self._replace(project, self.before)

    def _replace(self, project: ProjectLike, value: AnnotationObject) -> None:
        """_replace。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (ProjectLike): 输入参数。
            value (AnnotationObject): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        project.annotations = [
            value.clone() if item.id == self.annotation_id else item
            for item in project.annotations
        ]


class UpdateLabelAssignmentCommand(BaseCommand):
    text = "更新标签"

    def __init__(self, annotation_id: str, before_label_id: int, after_label_id: int):
        """__init__。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            annotation_id (str): 输入参数。
            before_label_id (int): 输入参数。
            after_label_id (int): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self.annotation_id = annotation_id
        self.before_label_id = before_label_id
        self.after_label_id = after_label_id

    def redo(self, project: ProjectLike) -> None:
        """redo。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (ProjectLike): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self._apply(project, self.after_label_id)

    def undo(self, project: ProjectLike) -> None:
        """undo。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (ProjectLike): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self._apply(project, self.before_label_id)

    def _apply(self, project: ProjectLike, label_id: int) -> None:
        """_apply。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (ProjectLike): 输入参数。
            label_id (int): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
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
        """__init__。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            commands (list[BaseCommand]): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self.commands = commands[:]

    def redo(self, project: ProjectLike) -> None:
        """redo。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (ProjectLike): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        for command in self.commands:
            command.redo(project)

    def undo(self, project: ProjectLike) -> None:
        """undo。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (ProjectLike): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        for command in reversed(self.commands):
            command.undo(project)


class UpdateMaskPatchCommand(BaseCommand):
    text = "更新Mask补丁"

    def __init__(
        self,
        bbox: tuple[int, int, int, int],
        before_patch: np.ndarray | None,
        after_patch: np.ndarray | None,
    ):
        """__init__。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            bbox (tuple[int, int, int, int]): 输入参数。
            before_patch (np.ndarray | None): 输入参数。
            after_patch (np.ndarray | None): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self.bbox = tuple(int(value) for value in bbox)
        self.before_patch = None if before_patch is None else before_patch.copy()
        self.after_patch = None if after_patch is None else after_patch.copy()

    def redo(self, project: ProjectLike) -> None:
        """redo。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (ProjectLike): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self._apply_patch(project, self.after_patch)

    def undo(self, project: ProjectLike) -> None:
        """undo。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (ProjectLike): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self._apply_patch(project, self.before_patch)

    def _apply_patch(self, project: ProjectLike, patch: np.ndarray | None) -> None:
        """_apply_patch。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (ProjectLike): 输入参数。
            patch (np.ndarray | None): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        x, y, width, height = self.bbox
        if width <= 0 or height <= 0:
            return
        if project.mask_data is None:
            if patch is None:
                return
            project.mask_data = np.zeros((y + height, x + width), dtype=patch.dtype)
        mask = project.mask_data
        needed_height = max(mask.shape[0], y + height)
        needed_width = max(mask.shape[1], x + width)
        if needed_height != mask.shape[0] or needed_width != mask.shape[1]:
            expanded = np.zeros((needed_height, needed_width), dtype=mask.dtype)
            expanded[:mask.shape[0], :mask.shape[1]] = mask
            project.mask_data = expanded
            mask = project.mask_data
        if patch is None:
            project.mask_data[y:y + height, x:x + width] = 0
        else:
            project.mask_data[y:y + height, x:x + width] = patch.copy()


class CommandStack:
    def __init__(self, project: ProjectLike):
        """__init__。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            project (ProjectLike): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self.project = project
        self._undo: list[BaseCommand] = []
        self._redo: list[BaseCommand] = []

    def push(self, command: BaseCommand) -> None:
        """push。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            command (BaseCommand): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        command.redo(self.project)
        self._undo.append(command)
        self._redo.clear()

    def undo(self) -> bool:
        """undo。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            bool: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if not self._undo:
            return False
        command = self._undo.pop()
        command.undo(self.project)
        self._redo.append(command)
        return True

    def redo(self) -> bool:
        """redo。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            bool: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if not self._redo:
            return False
        command = self._redo.pop()
        command.redo(self.project)
        self._undo.append(command)
        return True

    def can_undo(self) -> bool:
        """can_undo。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            bool: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        return bool(self._undo)

    def can_redo(self) -> bool:
        """can_redo。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            bool: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        return bool(self._redo)

    def clear_redo(self) -> None:
        """clear_redo。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self._redo.clear()

    def undo_depth(self) -> int:
        """undo_depth。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            int: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        return len(self._undo)
