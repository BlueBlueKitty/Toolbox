"""
标签类别管理。
"""

from __future__ import annotations

from dataclasses import replace

from .models import LabelClass


DEFAULT_LABELS = [
    ("类别 1", "#1d4ed8", "1"),
]


class LabelStore:
    def __init__(self, labels: list[LabelClass] | None = None):
        """__init__。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            labels (list[LabelClass] | None): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self._labels = labels[:] if labels else [
            LabelClass(id=index + 1, name=name, color=color, shortcut=shortcut)
            for index, (name, color, shortcut) in enumerate(DEFAULT_LABELS)
        ]

    def labels(self) -> list[LabelClass]:
        """labels。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            list[LabelClass]: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        return [replace(label) for label in self._labels]

    def get(self, label_id: int) -> LabelClass | None:
        """get。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            label_id (int): 输入参数。
        返回:
            LabelClass | None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        for label in self._labels:
            if label.id == label_id:
                return label
        return None

    def set_labels(self, labels: list[LabelClass]) -> None:
        """set_labels。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            labels (list[LabelClass]): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self._labels = labels[:]

    def add_label(self, label: LabelClass) -> None:
        """add_label。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            label (LabelClass): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self._labels.append(label)

    def update_label(self, label: LabelClass) -> None:
        """update_label。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            label (LabelClass): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        for index, item in enumerate(self._labels):
            if item.id == label.id:
                self._labels[index] = label
                return
        self._labels.append(label)

    def remove_label(self, label_id: int) -> None:
        """remove_label。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            label_id (int): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self._labels = [label for label in self._labels if label.id != label_id]
