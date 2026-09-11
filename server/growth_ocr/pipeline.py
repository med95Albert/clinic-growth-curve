# -*- coding: utf-8 -*-
"""把 geometry → cells → recognizer → parse 串起來的唯一入口。

api.py 與 bench.py 都走這裡，確保「線上跑的」跟「benchmark 量的」是同一條路。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from .cells import CellImage, dump_cells, extract_cells
from .geometry import Corrected, GeometryError, decode_image, rectify
from .parse import DEFAULT_THRESHOLD, build_cell_results, parse_cells
from .recognizers.base import Recognizer
from .template import PX_PER_MM, Template


@dataclass
class OcrOutcome:
    data: dict
    backend: str
    ms: int
    debug: dict
    corrected: Corrected
    cell_images: list[CellImage]
    recognized: dict[str, tuple[str | None, float]]


def recognize_cells(
    recognizer: Recognizer, corrected: np.ndarray, cell_images: list[CellImage], tpl: Template
) -> dict[str, tuple[str | None, float]]:
    """依後端能力挑辨識方式：整頁型（雲端計費）送一次整張，逐格型只送有墨水的格子。"""
    targets = [ci for ci in cell_images if ci.cell.kind == "digit" and not ci.cell.skip]
    if recognizer.page_level:
        boxes = [(ci.cell.path, *ci.cell.rect_px(0.0, PX_PER_MM)) for ci in targets]
        results = recognizer.recognize_page(corrected, boxes)
        return {t.cell.path: r for t, r in zip(targets, results)}

    inked = [ci for ci in targets if not ci.blank]  # 空白格不必送模型，省時間也少誤讀
    if not inked:
        return {}
    results = recognizer.recognize([ci.image for ci in inked])
    return {ci.cell.path: r for ci, r in zip(inked, results)}


def run_image(
    image: bytes | np.ndarray,
    tpl: Template,
    recognizer: Recognizer,
    today: str,
    threshold: float = DEFAULT_THRESHOLD,
    include_debug: bool = True,
    dump_dir: str | None = None,
    dump_prefix: str = "cell",
) -> OcrOutcome:
    started = time.perf_counter()
    img = decode_image(image) if isinstance(image, (bytes, bytearray)) else image
    corrected = rectify(img, tpl)
    cell_images = extract_cells(corrected.image, tpl)
    if dump_dir:
        dump_cells(cell_images, dump_dir, dump_prefix)

    recognized = recognize_cells(recognizer, corrected.image, cell_images, tpl)
    results = build_cell_results(cell_images, recognized)
    data = parse_cells(results, tpl, today=today, threshold=threshold, extra_notes=corrected.quality.warnings)

    ms = int((time.perf_counter() - started) * 1000)
    debug: dict = {}
    if include_debug:
        debug = {
            "cells": [r.as_debug() for r in results if r.kind == "digit"],
            "quality": corrected.quality.as_dict(),
        }
    return OcrOutcome(
        data=data,
        backend=recognizer.name,
        ms=ms,
        debug=debug,
        corrected=corrected,
        cell_images=cell_images,
        recognized=recognized,
    )


__all__ = ["GeometryError", "OcrOutcome", "recognize_cells", "run_image"]
