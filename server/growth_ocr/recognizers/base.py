# -*- coding: utf-8 -*-
"""辨識後端介面。

契約：`recognize(cells) -> [(char|None, conf)]`，char 是**單一** 0–9 字元或 None，
conf 落在 0–1。看不懂、不是數字、或一格看起來有多個字 → (None, conf)，
由 parse 依 cells.ink 判斷這格到底是「空白」還是「看不懂」。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

DIGITS = set("0123456789")


class Recognizer(ABC):
    name: str = "base"
    #: True 代表這個後端要「整張校正影像送一次」（例如雲端 OCR 以張計費）
    page_level: bool = False

    @abstractmethod
    def recognize(self, cells: list[np.ndarray]) -> list[tuple[str | None, float]]:
        """逐格辨識。輸入為白底灰階影像清單，輸出長度必須與輸入相同。"""

    def recognize_page(
        self, corrected: np.ndarray, boxes: list[tuple[str, int, int, int, int]]
    ) -> list[tuple[str | None, float]]:
        """整頁辨識（page_level=True 的後端才需實作）。boxes 為 (path, x0, y0, x1, y1) 像素座標。"""
        raise NotImplementedError(f"{self.name} 不支援整頁辨識")

    def warmup(self) -> None:
        """先載模型／建 session，讓第一張照片不要特別慢。"""

    def close(self) -> None:
        pass


def normalize_char(text: str | None) -> str | None:
    """把辨識文字收斂成單一數字；其餘一律 None（寧可讓人補，不要猜）。"""
    if not text:
        return None
    s = "".join(ch for ch in str(text).strip() if not ch.isspace())
    # 常見誤讀對照：OCR 把手寫數字讀成形近的英文字母時，仍當作看不懂處理較安全，
    # 只修掉「全形數字」這種純編碼差異。
    s = s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if len(s) != 1 or s not in DIGITS:
        return None
    return s


def get_recognizer(name: str | None = None, **kwargs) -> Recognizer:
    """依名稱建立後端。名稱與 OCR_BACKEND 環境變數相同。"""
    key = (name or "rapidocr").strip().lower().replace("_", "-")
    if key in ("rapidocr", "rapid"):
        from .rapidocr_backend import RapidOcrRecognizer

        return RapidOcrRecognizer(**kwargs)
    if key in ("google-vision", "vision", "google"):
        from .vision_backend import VisionRecognizer

        return VisionRecognizer(**kwargs)
    if key in ("digit-cnn", "cnn"):
        from .digit_cnn import DigitCnnRecognizer

        return DigitCnnRecognizer(**kwargs)
    if key in ("stub", "null"):
        return StubRecognizer(**kwargs)
    raise ValueError(f"未知的辨識後端：{name}（可用：rapidocr / google-vision / digit-cnn / stub）")


class StubRecognizer(Recognizer):
    """測試用：可預先塞入 path→char 對照，或一律回 None。"""

    name = "stub"

    def __init__(self, answers: dict[str, tuple[str | None, float]] | None = None, order: list[str] | None = None):
        self.answers = answers or {}
        self.order = order or []

    def recognize(self, cells: list[np.ndarray]) -> list[tuple[str | None, float]]:
        out: list[tuple[str | None, float]] = []
        for i, _ in enumerate(cells):
            path = self.order[i] if i < len(self.order) else None
            out.append(self.answers.get(path, (None, 0.0)) if path else (None, 0.0))
        return out
