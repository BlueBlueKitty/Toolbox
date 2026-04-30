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
        raise NotImplementedError
