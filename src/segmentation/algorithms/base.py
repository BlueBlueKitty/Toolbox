"""
半自动分割算法接口。
"""

from __future__ import annotations

import numpy as np

from ..models import MagicWandParams, PreviewSelection


class BaseSegmenter:
    def run(
        self,
        image: np.ndarray,
        seed_point: tuple[int, int],
        params: MagicWandParams,
    ) -> PreviewSelection:
        """run。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            image (np.ndarray): 输入参数。
            seed_point (tuple[int, int]): 输入参数。
            params (MagicWandParams): 输入参数。
        返回:
            PreviewSelection: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        raise NotImplementedError
