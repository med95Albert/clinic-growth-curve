# -*- coding: utf-8 -*-
"""Google Vision 後端的「symbol → 格子」對位邏輯，用假回應離線驗證（不需金鑰、不連網）。"""
from __future__ import annotations

import numpy as np
import pytest

from growth_ocr.recognizers.vision_backend import (
    VisionRecognizer,
    VisionUnavailableError,
    extract_symbols,
    map_symbols_to_cells,
)
from growth_ocr.template import PX_PER_MM


def symbol(text: str, cx: float, cy: float, half: float = 8.0, conf: float = 0.97) -> dict:
    return {
        "text": text,
        "confidence": conf,
        "vertices": [(cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half), (cx - half, cy + half)],
    }


def fake_response(symbols: list[dict]) -> dict:
    return {
        "responses": [
            {
                "fullTextAnnotation": {
                    "pages": [
                        {
                            "blocks": [
                                {
                                    "paragraphs": [
                                        {
                                            "words": [
                                                {
                                                    "symbols": [
                                                        {
                                                            "text": s["text"],
                                                            "confidence": s["confidence"],
                                                            "boundingBox": {
                                                                "vertices": [
                                                                    {"x": x, "y": y} for x, y in s["vertices"]
                                                                ]
                                                            },
                                                        }
                                                        for s in symbols
                                                    ]
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            }
        ]
    }


def boxes_for(tpl, paths):
    return [(p, *tpl.by_path(p).rect_px(0.0, PX_PER_MM)) for p in paths]


def test_symbols_map_to_the_cell_that_contains_their_center(tpl):
    paths = ["birth.y[0]", "birth.y[1]", "birth.y[2]", "birth.m[0]", "birth.m[1]"]
    boxes = boxes_for(tpl, paths)
    syms = []
    for path, ch in zip(paths, "10803"):
        cx, cy = tpl.by_path(path).center_px(PX_PER_MM)
        syms.append(symbol(ch, cx, cy))
    mapping = map_symbols_to_cells(syms, boxes)
    assert [mapping[p][0] for p in paths] == list("10803")
    assert all(mapping[p][1] > 0.9 for p in paths)


def test_symbol_outside_every_cell_is_dropped(tpl):
    boxes = boxes_for(tpl, ["birth.y[0]"])
    mapping = map_symbols_to_cells([symbol("9", 5, 5)], boxes)
    assert mapping["birth.y[0]"] == (None, 0.0)


def test_two_symbols_in_one_cell_is_unreadable(tpl):
    boxes = boxes_for(tpl, ["birth.y[0]"])
    cx, cy = tpl.by_path("birth.y[0]").center_px(PX_PER_MM)
    mapping = map_symbols_to_cells([symbol("1", cx - 10, cy), symbol("7", cx + 10, cy)], boxes)
    assert mapping["birth.y[0]"] == (None, 0.0)


def test_non_digit_symbol_is_unreadable(tpl):
    boxes = boxes_for(tpl, ["birth.y[0]"])
    cx, cy = tpl.by_path("birth.y[0]").center_px(PX_PER_MM)
    mapping = map_symbols_to_cells([symbol("A", cx, cy)], boxes)
    assert mapping["birth.y[0]"] == (None, 0.0)


def test_extract_symbols_walks_the_annotation_tree(tpl):
    cx, cy = tpl.by_path("father[0]").center_px(PX_PER_MM)
    syms = extract_symbols(fake_response([symbol("1", cx, cy)]))
    assert len(syms) == 1 and syms[0]["text"] == "1"
    assert syms[0]["vertices"][0] == (cx - 8, cy - 8)


def test_api_error_in_response_raises(tpl):
    with pytest.raises(VisionUnavailableError):
        extract_symbols({"responses": [{"error": {"message": "quota"}}]})


def test_recognize_page_uses_one_request(tpl, monkeypatch):
    paths = [c.path for c in tpl.recognizable_cells[:6]]
    boxes = boxes_for(tpl, paths)
    calls = []

    rec = VisionRecognizer(api_key="fake")
    syms = [symbol(ch, *tpl.by_path(p).center_px(PX_PER_MM)) for p, ch in zip(paths, "123456")]

    def fake_annotate(corrected):
        calls.append(corrected.shape)
        return fake_response(syms)

    monkeypatch.setattr(rec, "annotate", fake_annotate)
    out = rec.recognize_page(np.full((2376, 1680), 255, dtype=np.uint8), boxes)
    assert len(calls) == 1  # 一張表單只送一次，免費額度以單位計
    assert [c for c, _ in out] == list("123456")


def test_missing_key_errors_only_when_called(monkeypatch):
    monkeypatch.delenv("GOOGLE_VISION_API_KEY", raising=False)
    rec = VisionRecognizer()  # 建構不該爆
    with pytest.raises(VisionUnavailableError):
        _ = rec.api_key


def test_per_cell_recognize_is_refused():
    rec = VisionRecognizer(api_key="fake")
    with pytest.raises(VisionUnavailableError):
        rec.recognize([np.zeros((10, 10), dtype=np.uint8)])
