"""
图像分割工具核心模块。
"""

from .models import (
    AnnotationObject,
    DisplayState,
    LabelClass,
    MagicWandParams,
    PreviewSelection,
    SegmentationProject,
)
from .commands import (
    AddAnnotationCommand,
    BatchCommand,
    CommandStack,
    DeleteAnnotationCommand,
    UpdateGeometryCommand,
    UpdateLabelAssignmentCommand,
    UpdateMaskPatchCommand,
)
from .label_store import LabelStore
from .project_manager import SegmentationProjectManager

__all__ = [
    "AnnotationObject",
    "DisplayState",
    "LabelClass",
    "MagicWandParams",
    "PreviewSelection",
    "SegmentationProject",
    "AddAnnotationCommand",
    "BatchCommand",
    "CommandStack",
    "DeleteAnnotationCommand",
    "UpdateGeometryCommand",
    "UpdateLabelAssignmentCommand",
    "UpdateMaskPatchCommand",
    "LabelStore",
    "SegmentationProjectManager",
]
