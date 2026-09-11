# -*- coding: utf-8 -*-
"""template 展開：格數、路徑格式、預印格標記、mm→px 換算。"""
from __future__ import annotations

from growth_ocr.template import CHECK, DIGIT, PX_PER_MM, load_template


def test_cell_counts(tpl):
    assert len(tpl.digit_cells) == 171
    assert len(tpl.check_cells) == 2
    assert len(tpl.cells) == 173
    assert len(tpl.recognizable_cells) == 161  # 171 − 10 個預印「1」


def test_seq_is_eight_boxes(tpl):
    """病歷號 8 格（舊版是 3 格候診序號）。"""
    assert len(tpl.group("seq")) == 8
    assert [c.index for c in tpl.group("seq")] == list(range(8))


def test_paths_follow_the_documented_format(tpl):
    paths = {c.path for c in tpl.cells}
    for expected in (
        "seq[0]",
        "seq[7]",
        "birth.y[1]",
        "father[2]",
        "mother[0]",
        "rows[2].height.int[0]",
        "rows[9].weight.dec[0]",
        "gender.male",
        "gender.female",
    ):
        assert expected in paths, expected


def test_preprinted_cells_are_skipped(tpl):
    pre = [c for c in tpl.cells if c.skip]
    assert len(pre) == tpl.row_count  # 每一列的民國「1」各一格
    assert all(c.path.endswith("date.y[0]") for c in pre)
    assert all(c.preprinted == "1" for c in pre)


def test_row_labels_end_with_today(tpl):
    assert tpl.row_labels == ("1", "2", "3", "4", "5", "6", "7", "8", "9", "today")
    assert tpl.row_count == 10


def test_today_row_is_found_by_label_not_index(tpl):
    """今日量測列由 label 決定；把 label 搬到別列，today_row_index 要跟著搬。"""
    import dataclasses

    assert tpl.today_row_index == 9
    assert tpl.is_today_row(9) and not tpl.is_today_row(5)

    moved = dataclasses.replace(tpl, row_labels=("today",) + tpl.row_labels[1:9] + ("10",))
    assert moved.today_row_index == 0
    assert moved.is_today_row(0) and not moved.is_today_row(9)

    none_row = dataclasses.replace(tpl, row_labels=tuple(str(i + 1) for i in range(tpl.row_count)))
    assert none_row.today_row_index is None


def test_group_lookup_is_ordered(tpl):
    g = tpl.group("birth.y")
    assert [c.index for c in g] == [0, 1, 2]
    assert g[0].x < g[1].x < g[2].x


def test_rect_px_applies_inset(tpl):
    cell = tpl.by_path("seq[0]")
    assert (cell.w, cell.h) == (7.5, 10.0)  # 改版後格高 11mm → 10mm
    x0, y0, x1, y1 = cell.rect_px(0.0)
    assert (x1 - x0, y1 - y0) == (round(7.5 * PX_PER_MM), round(10 * PX_PER_MM))
    ix0, iy0, ix1, iy1 = cell.rect_px(0.8)
    assert ix0 > x0 and iy0 > y0 and ix1 < x1 and iy1 < y1
    # 內縮 0.8mm 後仍要留得下一個數字：短邊至少 5mm
    assert (ix1 - ix0) >= 5 * PX_PER_MM and (iy1 - iy0) >= 5 * PX_PER_MM


def test_digit_cell_mm_comes_from_template(tpl):
    assert tpl.digit_cell_mm == (7.5, 10.0)


def test_page_size_is_8px_per_mm(tpl):
    assert tpl.size_px == (1680, 2376)


def test_kinds(tpl):
    assert tpl.by_path("gender.male").kind == CHECK
    assert tpl.by_path("seq[0]").kind == DIGIT


def test_env_override(monkeypatch, tmp_path, tpl):
    monkeypatch.setenv("GROWTH_TEMPLATE", str(tpl.source))
    assert len(load_template().cells) == len(tpl.cells)
