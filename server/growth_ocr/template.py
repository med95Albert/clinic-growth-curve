# -*- coding: utf-8 -*-
"""載入 form/template.json，攤平成「一格一筆」的 Cell 清單。

座標單位一律 mm、原點為紙張左上角（與 template.json 相同），
換算成像素是 geometry / cells 的事，本模組不碰像素。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

# 校正影像的解析度：8 px/mm → A4 為 1680×2376。
# 這個常數同時決定 template 座標與影像像素的換算，改了要一起改 tests。
PX_PER_MM = 8

DIGIT = "digit"
CHECK = "check"


@dataclass(frozen=True)
class Cell:
    """一個可切出來的方格。path 是全域唯一的路徑字串（例：rows[2].height.int[0]）。"""

    path: str
    x: float  # mm，左上角
    y: float
    w: float
    h: float
    kind: str = DIGIT
    preprinted: str | None = None  # 預印字（表格民國「1」），不需辨識
    group: str | None = None  # 同一組數字的 group key，例：rows[2].height.int
    index: int = 0  # 在 group 內的順序

    @property
    def skip(self) -> bool:
        """預印格不送辨識器。"""
        return self.preprinted is not None

    def rect_px(self, inset_mm: float = 0.0, px_per_mm: int = PX_PER_MM) -> tuple[int, int, int, int]:
        """回傳校正影像上的 (x0, y0, x1, y1)，可指定內縮 mm 以避開框線。"""
        x0 = int(round((self.x + inset_mm) * px_per_mm))
        y0 = int(round((self.y + inset_mm) * px_per_mm))
        x1 = int(round((self.x + self.w - inset_mm) * px_per_mm))
        y1 = int(round((self.y + self.h - inset_mm) * px_per_mm))
        return x0, y0, x1, y1

    def center_px(self, px_per_mm: int = PX_PER_MM) -> tuple[float, float]:
        return ((self.x + self.w / 2) * px_per_mm, (self.y + self.h / 2) * px_per_mm)


@dataclass(frozen=True)
class Template:
    page: tuple[float, float]  # mm
    registration: dict[str, dict[str, float]]
    cells: tuple[Cell, ...]
    row_labels: tuple[str, ...]
    source: Path | None = None

    # --- 常用切片 -------------------------------------------------
    @property
    def digit_cells(self) -> tuple[Cell, ...]:
        return tuple(c for c in self.cells if c.kind == DIGIT)

    @property
    def recognizable_cells(self) -> tuple[Cell, ...]:
        """真正要送辨識器的格子（數字格且非預印）。"""
        return tuple(c for c in self.cells if c.kind == DIGIT and not c.skip)

    @property
    def check_cells(self) -> tuple[Cell, ...]:
        return tuple(c for c in self.cells if c.kind == CHECK)

    def by_path(self, path: str) -> Cell:
        return self._index[path]

    @property
    def row_count(self) -> int:
        return len(self.row_labels)

    @property
    def digit_cell_mm(self) -> tuple[float, float]:
        """數字格的標準尺寸（mm）。synth 用它決定字級，表單改格高就不必改程式。"""
        cells = self.digit_cells
        return (cells[0].w, cells[0].h) if cells else (0.0, 0.0)

    def is_today_row(self, ri: int) -> bool:
        """今日量測列一律以 label 判定。它是不是最後一列、表單有幾列，都不可以寫死。"""
        return 0 <= ri < len(self.row_labels) and self.row_labels[ri] == "today"

    @property
    def today_row_index(self) -> int | None:
        for i in range(len(self.row_labels)):
            if self.is_today_row(i):
                return i
        return None

    def group(self, group_key: str) -> tuple[Cell, ...]:
        """取出一組數字格（依 index 排序），例：group('birth.y')。"""
        return tuple(sorted((c for c in self.cells if c.group == group_key), key=lambda c: c.index))

    def __post_init__(self) -> None:
        object.__setattr__(self, "_index", {c.path: c for c in self.cells})

    def __iter__(self) -> Iterator[Cell]:
        return iter(self.cells)

    @property
    def size_px(self) -> tuple[int, int]:
        return (int(round(self.page[0] * PX_PER_MM)), int(round(self.page[1] * PX_PER_MM)))

    # 四角定位塊中心（mm），順序固定為 tl, tr, br, bl。
    def registration_centers(self) -> list[tuple[float, float]]:
        out = []
        for key in ("tl", "tr", "br", "bl"):
            r = self.registration[key]
            out.append((r["x"] + r["w"] / 2, r["y"] + r["h"] / 2))
        return out

    def orientation_dot(self) -> dict[str, float]:
        return self.registration["orientation_dot"]


def _boxes(raw: list[dict], prefix: str, kind: str = DIGIT) -> list[Cell]:
    cells = []
    for i, b in enumerate(raw):
        cells.append(
            Cell(
                path=f"{prefix}[{i}]",
                x=float(b["x"]),
                y=float(b["y"]),
                w=float(b["w"]),
                h=float(b["h"]),
                kind=kind,
                preprinted=b.get("pre"),
                group=prefix,
                index=i,
            )
        )
    return cells


def _tick(raw: dict, path: str) -> Cell:
    return Cell(
        path=path,
        x=float(raw["x"]),
        y=float(raw["y"]),
        w=float(raw["w"]),
        h=float(raw["h"]),
        kind=CHECK,
        group="gender",
        index=0 if path.endswith("male") and not path.endswith("female") else 1,
    )


def default_template_path() -> Path:
    """預設 form/template.json；可用環境變數 GROWTH_TEMPLATE 覆寫。"""
    env = os.environ.get("GROWTH_TEMPLATE")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "form" / "template.json"


def load_template(path: str | Path | None = None) -> Template:
    p = Path(path) if path else default_template_path()
    raw = json.loads(p.read_text(encoding="utf-8"))
    f = raw["fields"]

    cells: list[Cell] = []
    cells += _boxes(f["seq"], "seq")
    cells.append(_tick(f["gender"]["male"], "gender.male"))
    cells.append(_tick(f["gender"]["female"], "gender.female"))
    for part in ("y", "m", "d"):
        cells += _boxes(f["birth"][part], f"birth.{part}")
    cells += _boxes(f["father"], "father")
    cells += _boxes(f["mother"], "mother")

    labels = []
    for ri, row in enumerate(f["rows"]):
        labels.append(str(row.get("label", ri + 1)))
        for part in ("y", "m", "d"):
            cells += _boxes(row["date"][part], f"rows[{ri}].date.{part}")
        for field in ("height", "weight"):
            cells += _boxes(row[field]["int"], f"rows[{ri}].{field}.int")
            cells += _boxes(row[field]["dec"], f"rows[{ri}].{field}.dec")

    return Template(
        page=(float(raw["page"][0]), float(raw["page"][1])),
        registration=raw["registration"],
        cells=tuple(cells),
        row_labels=tuple(labels),
        source=p,
    )
