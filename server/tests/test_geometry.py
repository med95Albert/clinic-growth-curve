# -*- coding: utf-8 -*-
"""幾何校正：0/90/180/270 度＋透視變形下都要還原，且格子對位誤差 < 1mm。"""
from __future__ import annotations

import random

import cv2
import numpy as np
import pytest

from growth_ocr import synth
from growth_ocr.cells import extract_cells
from growth_ocr.geometry import GeometryError, decode_image, rectify
from growth_ocr.template import PX_PER_MM

TOLERANCE_PX = PX_PER_MM  # 1mm


def _reg_centers_in(corrected: np.ndarray, tpl) -> list[tuple[float, float]]:
    """在校正影像上重新找四個定位塊的中心，用來量對位誤差。"""
    _, binary = cv2.threshold(corrected, 128, 255, cv2.THRESH_BINARY_INV)
    found = []
    for key in ("tl", "tr", "br", "bl"):
        r = tpl.registration[key]
        pad = 4 * PX_PER_MM
        x0 = max(0, int((r["x"]) * PX_PER_MM) - pad)
        y0 = max(0, int((r["y"]) * PX_PER_MM) - pad)
        x1 = int((r["x"] + r["w"]) * PX_PER_MM) + pad
        y1 = int((r["y"] + r["h"]) * PX_PER_MM) + pad
        patch = binary[y0:y1, x0:x1]
        m = cv2.moments(patch, binaryImage=True)
        assert m["m00"] > 0, f"{key} 定位塊在校正影像上找不到"
        found.append((x0 + m["m10"] / m["m00"], y0 + m["m01"] / m["m00"]))
    return found


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rectify_handles_all_rotations(tpl, rotation):
    rng = random.Random(1000 + rotation)
    form = synth.make_form(tpl, rng, rotate=rotation)
    corrected = rectify(decode_image(form.jpeg), tpl)

    assert corrected.quality.rotation == rotation
    assert corrected.image.shape == (tpl.size_px[1], tpl.size_px[0])

    expected = [(x * PX_PER_MM, y * PX_PER_MM) for x, y in tpl.registration_centers()]
    for (ex, ey), (gx, gy) in zip(expected, _reg_centers_in(corrected.image, tpl)):
        assert abs(ex - gx) < TOLERANCE_PX and abs(ey - gy) < TOLERANCE_PX


@pytest.mark.parametrize("warp", [0.005, 0.02, 0.035])
def test_rectify_survives_perspective(tpl, warp):
    rng = random.Random(int(warp * 10000))
    form = synth.make_form(tpl, rng, warp=warp)
    corrected = rectify(decode_image(form.jpeg), tpl)
    expected = [(x * PX_PER_MM, y * PX_PER_MM) for x, y in tpl.registration_centers()]
    for (ex, ey), (gx, gy) in zip(expected, _reg_centers_in(corrected.image, tpl)):
        assert abs(ex - gx) < TOLERANCE_PX and abs(ey - gy) < TOLERANCE_PX


def test_cells_land_on_the_right_content(tpl):
    """切格對位：有寫字的格子要有墨水、空白格要沒有。"""
    rng = random.Random(42)
    form = synth.make_form(tpl, rng)
    corrected = rectify(decode_image(form.jpeg), tpl)
    for ci in extract_cells(corrected.image, tpl):
        if ci.cell.kind != "digit" or ci.cell.skip:
            continue
        truth = form.cells.get(ci.cell.path, "")
        assert ci.blank == (truth == ""), f"{ci.cell.path} 墨水判定錯（truth={truth!r} ink={ci.ink:.3f}）"


def test_missing_corner_raises_with_retake_hint(tpl):
    rng = random.Random(5)
    form = synth.make_form(tpl, rng)
    img = decode_image(form.jpeg)
    h, w = img.shape[:2]
    img[int(h * 0.80) :, : int(w * 0.25)] = 235  # 蓋掉左下角
    with pytest.raises(GeometryError) as exc:
        rectify(img, tpl)
    assert "左下" in str(exc.value) or "定位塊" in str(exc.value)


def test_too_dark_image_is_rejected(tpl):
    dark = np.full((900, 700, 3), 20, dtype=np.uint8)
    with pytest.raises(GeometryError) as exc:
        rectify(dark, tpl)
    assert "太暗" in str(exc.value)


def test_blank_photo_reports_no_markers(tpl):
    blank = np.full((900, 700, 3), 240, dtype=np.uint8)
    with pytest.raises(GeometryError) as exc:
        rectify(blank, tpl)
    assert "定位塊" in str(exc.value)


def test_grid_score_separates_correct_from_upside_down(tpl):
    """格線吻合度是方向判斷的主判準：正確 ≫ 0，轉 180 度 ≈ 0。"""
    from growth_ocr.geometry import GRID_MIN, grid_score

    rng = random.Random(77)
    form = synth.make_form(tpl, rng)
    corrected = rectify(decode_image(form.jpeg), tpl)
    good = grid_score(corrected.image, tpl)
    flipped = grid_score(cv2.rotate(corrected.image, cv2.ROTATE_180), tpl)
    assert good > GRID_MIN * 2
    assert flipped < GRID_MIN
    assert corrected.quality.grid_score == pytest.approx(good, abs=1e-6)


def test_fake_corner_block_is_rejected(tpl):
    """把一個角落方塊蓋掉、另外在版面中央畫一個同尺寸方塊，不可以矇混過關。"""
    rng = random.Random(88)
    form = synth.make_form(tpl, rng)
    img = decode_image(form.jpeg)
    h, w = img.shape[:2]
    block = int(round(7 * (w / 235.0)))  # 7mm 在這張照片上的像素邊長
    img[int(h * 0.02) : int(h * 0.10), int(w * 0.02) : int(w * 0.12)] = 240  # 蓋掉左上角
    cx, cy = int(w * 0.35), int(h * 0.30)
    img[cy : cy + block, cx : cx + block] = 0  # 版面中央偽造一個方塊
    with pytest.raises(GeometryError):
        rectify(img, tpl)


def test_lower_resolution_photo_still_rectifies(tpl):
    rng = random.Random(99)
    form = synth.make_form(tpl, rng, long_edge=1300, jpeg_quality=75, blur=1.0)
    corrected = rectify(decode_image(form.jpeg), tpl)
    expected = [(x * PX_PER_MM, y * PX_PER_MM) for x, y in tpl.registration_centers()]
    for (ex, ey), (gx, gy) in zip(expected, _reg_centers_in(corrected.image, tpl)):
        assert abs(ex - gx) < TOLERANCE_PX and abs(ey - gy) < TOLERANCE_PX
