# -*- coding: utf-8 -*-
"""從校正影像切出每一格、清掉殘留框線、量墨水比例。

切格是整條管線最容易出錯的一環：內縮太少會把 0.9pt 框線一起餵給辨識器（很容易被讀成 1／7），
內縮太多會削掉手寫筆畫。預設 0.8mm 是「框線 0.32mm ＋ 校正誤差餘裕」推出來的。
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .template import CHECK, Cell, PX_PER_MM, Template

DIGIT_INSET_MM = 1.0
CHECK_INSET_MM = 1.2
# 空白判定：墨水比例低於此值視為沒寫。手寫「1」在 7.5×10mm 格內約佔 3–6%。
BLANK_INK = 0.012
# 勾選框判定：打勾／打叉／塗滿至少會蓋掉這個比例
CHECKED_INK = 0.035
# labels.csv 裡「這個勾選框有打」的正解字串（空字串＝沒打）。synth 用它寫標註；
# bench 讀的時候只看有沒有值，所以人工標註寫 1／v／✓ 都算數（docs/BENCHMARK.md 用的是 1）。
CHECK_MARK = "x"


@dataclass
class CellImage:
    cell: Cell
    image: np.ndarray  # 清理後灰階（白底、只留筆畫）
    raw: np.ndarray  # 未清理的原始切格，dump 時比較好看出切歪
    ink: float
    blank: bool

    @property
    def path(self) -> str:
        return self.cell.path


def _paper_level(crop: np.ndarray) -> float:
    """估這一格的紙張亮度：用高百分位數，避免被筆畫拉低。"""
    return float(np.percentile(crop, 88))


def _ink_mask(crop: np.ndarray) -> np.ndarray:
    """相對門檻的墨水遮罩。相對於「這一格自己的紙色」，才擋得住整頁的光照不均。"""
    paper = _paper_level(crop)
    # 光照已在 geometry 攤平，格內漸層很小；門檻放寬到「比紙暗 22 級」才留得住淡藍原子筆
    thr = max(40.0, min(paper * 0.85, paper - 22.0))
    return (crop < thr).astype(np.uint8)


def _strip_border_lines(mask: np.ndarray) -> np.ndarray:
    """清掉貼著邊緣的細長殘留框線。

    刻意不用「長度 70% 就當線」的形態學：手寫 7 的上橫、1 的直筆都會被誤刪。
    這裡要求「又細又長、而且緊貼某一邊」才移除。
    """
    h, w = mask.shape[:2]
    if h < 6 or w < 6:
        return mask
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = mask.copy()
    edge = 0.18
    for i in range(1, n):
        x, y, cw, ch, area = (
            stats[i, cv2.CC_STAT_LEFT],
            stats[i, cv2.CC_STAT_TOP],
            stats[i, cv2.CC_STAT_WIDTH],
            stats[i, cv2.CC_STAT_HEIGHT],
            stats[i, cv2.CC_STAT_AREA],
        )
        horizontal = cw >= 0.75 * w and ch <= 0.16 * h and (y <= edge * h or y + ch >= (1 - edge) * h)
        vertical = ch >= 0.75 * h and cw <= 0.16 * w and (x <= edge * w or x + cw >= (1 - edge) * w)
        tiny = area <= 4  # 掃描雜點（門檻放寬後略多）
        if horizontal or vertical or tiny:
            out[labels == i] = 0
    return out


def _strip_printed_lines(mask: np.ndarray) -> np.ndarray:
    """用投影清掉「細、直、貼外圍」的印刷框線殘留。

    紙張微彎時對位會差到 1mm，內縮後仍可能留下一段框線，模型會把它讀成「1」且信心極高
    ——這是第一張實拍（2026-09-11）抓到的沉默錯誤來源。判準刻意收緊：
    分辨靠「長度」不是「厚度」——原子筆筆畫也只有 3px 厚，用厚度會把 5 的上橫、2 的底橫刪掉
    （158→138、112→117 的沉默錯誤就是這樣來的）。印刷線貫穿整格（≥85%），手寫橫筆最多六七成。
    """
    h, w = mask.shape[:2]
    if h < 10 or w < 10:
        return mask
    out = mask.copy()
    zone_h, zone_w = int(h * 0.25), int(w * 0.25)
    rows = out.sum(axis=1) >= 0.85 * w   # 印刷線貫穿整格；手寫橫筆最多六七成
    cols = out.sum(axis=0) >= 0.85 * h
    def runs(flags):
        start = None
        for i, f in enumerate(list(flags) + [False]):
            if f and start is None:
                start = i
            elif not f and start is not None:
                yield start, i
                start = None
    for a, b in runs(rows):
        if b - a <= 4 and (a < zone_h or b > h - zone_h):
            out[max(0, a - 1):min(h, b + 1), :] = 0
    for a, b in runs(cols):
        if b - a <= 4 and (a < zone_w or b > w - zone_w):
            out[:, max(0, a - 1):min(w, b + 1)] = 0
    return out


def _clean(crop: np.ndarray) -> tuple[np.ndarray, float]:
    mask = _ink_mask(crop)
    mask = _strip_printed_lines(mask)
    mask = _strip_border_lines(mask)
    ink = float(mask.sum()) / mask.size if mask.size else 0.0
    # 稍微膨脹再取原值，保留筆畫邊緣的灰階（純二值化餵 OCR 反而掉分）
    grown = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    out = np.full_like(crop, 255)
    out[grown > 0] = crop[grown > 0]
    return out, ink


def crop_cell(corrected: np.ndarray, cell: Cell, inset_mm: float | None = None) -> np.ndarray:
    if inset_mm is None:
        inset_mm = CHECK_INSET_MM if cell.kind == CHECK else DIGIT_INSET_MM
    h, w = corrected.shape[:2]
    x0, y0, x1, y1 = cell.rect_px(inset_mm, PX_PER_MM)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return np.full((8, 8), 255, dtype=np.uint8)
    return corrected[y0:y1, x0:x1]


def extract_cells(corrected: np.ndarray, tpl: Template, inset_mm: float | None = None) -> list[CellImage]:
    """切出 template 中所有格子（含預印格與勾選框），順序與 tpl.cells 相同。"""
    out: list[CellImage] = []
    for cell in tpl.cells:
        raw = crop_cell(corrected, cell, inset_mm)
        cleaned, ink = _clean(raw)
        threshold = CHECKED_INK if cell.kind == CHECK else BLANK_INK
        out.append(CellImage(cell=cell, image=cleaned, raw=raw, ink=ink, blank=ink < threshold))
    return out


def is_checked(ci: CellImage) -> bool:
    return ci.cell.kind == CHECK and ci.ink >= CHECKED_INK


def dump_cells(cell_images: list[CellImage], out_dir, prefix: str) -> None:
    """把每格影像存成 PNG，檔名含 photo 與 path，供之後標註／訓練 digit-cnn。"""
    from pathlib import Path

    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    for ci in cell_images:
        safe = ci.path.replace("[", "_").replace("]", "").replace(".", "-")
        cv2.imwrite(str(d / f"{prefix}__{safe}.png"), ci.raw)
