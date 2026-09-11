# -*- coding: utf-8 -*-
"""RapidOCR（ONNX、純 CPU）逐格辨識後端。

實測要點（Mac / rapidocr 3.9.1）：
- 只跑辨識：`engine(img, use_det=False, use_cls=False)` 可行，回傳 TextRecOutput（txts/scores）；
  但一格一次呼叫約 19ms。改用 `engine.text_rec(TextRecInput(img=[...]))` 一次送整批較快。
- 決定速度的其實是 `Rec.rec_img_shape` 的寬度：預設 (3,48,320) 會把每個單字格都補到 320px 寬，
  100 格要 4.3 秒；改成 (3,48,96) 後同樣全對、只要 1 秒。
"""
from __future__ import annotations

import os
import threading

import cv2
import numpy as np

from .base import Recognizer, normalize_char

# 單格放大後的高度：PP-OCR rec 內部會縮到 48px 高，先放大到 96 能保住細筆畫。
TARGET_H = 96
# 白邊比例。PP-OCR 會把整張（含白邊）壓成 48px 高，所以白邊越多、數字在模型輸入裡就越小。
# 表單格高從 11mm 改成 10mm 後，原本的 0.30 讓數字太小：壓力情境（長邊 1200、blur 1.4、JPEG 70）
# 下 rapidocr 開始把「1」讀成「9」且信心 0.85–0.95（＝沉默錯誤）。實測 20 張：
# 0.30 → 沉默錯誤 12、0.15 → 1、0.08 → 2 且慢 1.5 倍；預設拍攝參數下三者都是 100%。
PAD_RATIO = 0.15


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


class RapidOcrRecognizer(Recognizer):
    name = "rapidocr"

    def __init__(
        self,
        ocr_version: str | None = None,
        model_type: str | None = None,
        rec_width: int | None = None,
        batch_num: int | None = None,
    ):
        self.ocr_version = (ocr_version or os.environ.get("RAPIDOCR_VERSION") or "PPOCRV6").upper()
        self.model_type = (model_type or os.environ.get("RAPIDOCR_MODEL_TYPE") or "").upper()
        self.rec_width = rec_width or _env_int("RAPIDOCR_REC_WIDTH", 96)
        self.batch_num = batch_num or _env_int("RAPIDOCR_BATCH", 16)
        self._engine = None
        # RLock 而非 Lock：recognize() 持鎖期間會經由 engine property 再取一次鎖（首次建模型）
        self._lock = threading.RLock()  # onnxruntime session 不保證多執行緒安全

    # --- 引擎 ---------------------------------------------------
    def _build(self):
        try:
            from rapidocr import ModelType, OCRVersion, RapidOCR
        except ImportError as exc:  # pragma: no cover - 取決於執行環境
            raise RuntimeError("rapidocr 未安裝，請先 pip install -r requirements.txt") from exc

        version_map = {
            "PPOCRV5": (OCRVersion.PPOCRV5, ModelType.MOBILE),
            "PPOCRV6": (OCRVersion.PPOCRV6, ModelType.MEDIUM),
        }
        version, default_model = version_map.get(self.ocr_version, (OCRVersion.PPOCRV6, ModelType.MEDIUM))
        model = {
            "MOBILE": ModelType.MOBILE, "MEDIUM": ModelType.MEDIUM,
            "SMALL": ModelType.SMALL, "TINY": ModelType.TINY,   # v6 的輕量版；NAS 這類弱 CPU 用
        }.get(self.model_type, default_model)
        # 執行緒數：onnxruntime 預設開「核心數」條並忙等待，在 2 核心 NAS 上與 OpenCV 互搶會慢百倍；
        # OCR_THREADS 未設時取 min(4, 核心數)，容器裡建議明確設成核心數。
        threads = int(os.environ.get("OCR_THREADS") or min(4, os.cpu_count() or 1))
        try:
            import cv2
            cv2.setNumThreads(threads)
        except Exception:  # pragma: no cover
            pass
        return RapidOCR(
            params={
                "Det.ocr_version": version,
                "Det.model_type": model,
                "Rec.ocr_version": version,
                "Rec.model_type": model,
                "Rec.rec_img_shape": [3, 48, int(self.rec_width)],
                "Rec.rec_batch_num": int(self.batch_num),
                "EngineConfig.onnxruntime.intra_op_num_threads": threads,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            }
        )

    @property
    def engine(self):
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    self._engine = self._build()
        return self._engine

    def warmup(self) -> None:
        blank = np.full((TARGET_H, TARGET_H), 255, dtype=np.uint8)
        self.recognize([blank])

    # --- 前處理 -------------------------------------------------
    @staticmethod
    def prepare(cell: np.ndarray) -> np.ndarray:
        """墊白邊 → 放大到 TARGET_H 高 → 轉三通道。"""
        img = cell if cell.ndim == 2 else cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
        pad = max(4, int(max(img.shape[:2]) * PAD_RATIO))
        img = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
        scale = TARGET_H / img.shape[0]
        if scale > 1:
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        else:
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    # --- 辨識 ---------------------------------------------------
    def recognize(self, cells: list[np.ndarray]) -> list[tuple[str | None, float]]:
        if not cells:
            return []
        from rapidocr.ch_ppocr_rec import TextRecInput

        batch = [self.prepare(c) for c in cells]
        with self._lock:
            res = self.engine.text_rec(TextRecInput(img=batch))
        txts = list(res.txts or [])
        scores = list(res.scores or [])
        out: list[tuple[str | None, float]] = []
        for i in range(len(cells)):
            txt = txts[i] if i < len(txts) else None
            score = float(scores[i]) if i < len(scores) else 0.0
            char = normalize_char(txt)
            # 認不出來時信心一律歸零：留著模型對「錯字」的高信心只會製造沉默錯誤
            out.append((char, score if char else 0.0))
        return out
