# -*- coding: utf-8 -*-
"""第二階段後端：用核對後的真實格子影像訓練的小型數字 CNN（尚未實作）。

訓練資料來源＝`python -m growth_ocr.bench --dump-cells`，把護理師核對過的值當標籤。
"""
from __future__ import annotations

import numpy as np

from .base import Recognizer


class DigitCnnRecognizer(Recognizer):
    name = "digit-cnn"

    def __init__(self, model_path: str | None = None):
        self.model_path = model_path

    def recognize(self, cells: list[np.ndarray]) -> list[tuple[str | None, float]]:
        raise NotImplementedError(
            "digit-cnn 尚未實作：需先用 bench --dump-cells 蒐集台灣家長筆跡的格子影像並標註後訓練。"
        )
