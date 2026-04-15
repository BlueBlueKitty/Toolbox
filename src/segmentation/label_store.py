"""
标签类别管理。
"""

from __future__ import annotations

from dataclasses import replace

from .models import LabelClass


DEFAULT_LABELS = [
    ("类别 1", "#ff6b6b", "1"),
]


class LabelStore:
    def __init__(self, labels: list[LabelClass] | None = None):
        self._labels = labels[:] if labels else [
            LabelClass(id=index + 1, name=name, color=color, shortcut=shortcut)
            for index, (name, color, shortcut) in enumerate(DEFAULT_LABELS)
        ]

    def labels(self) -> list[LabelClass]:
        return [replace(label) for label in self._labels]

    def get(self, label_id: int) -> LabelClass | None:
        for label in self._labels:
            if label.id == label_id:
                return label
        return None

    def set_labels(self, labels: list[LabelClass]) -> None:
        self._labels = labels[:]

    def add_label(self, label: LabelClass) -> None:
        self._labels.append(label)

    def update_label(self, label: LabelClass) -> None:
        for index, item in enumerate(self._labels):
            if item.id == label.id:
                self._labels[index] = label
                return
        self._labels.append(label)

    def remove_label(self, label_id: int) -> None:
        self._labels = [label for label in self._labels if label.id != label_id]
