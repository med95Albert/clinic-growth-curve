# -*- coding: utf-8 -*-
"""parse 規則全覆蓋：民國轉換、小數、前導零、性別、today 補日期、uncertain 索引。"""
from __future__ import annotations

import pytest

from growth_ocr.parse import parse_cells

TODAY = "2026-09-10"


def mark(results, path, char, conf=0.99):
    for r in results:
        if r.path == path:
            r.char, r.conf, r.ink, r.blank = char, conf, 0.1, False
            return r
    raise KeyError(path)


def check(results, path, ink=0.2):
    for r in results:
        if r.path == path:
            r.ink, r.blank = ink, False
            return r
    raise KeyError(path)


def fill_row(write, results, ri, y2="15", m="08", d="22", h="1235", w="0240", conf=0.99):
    if y2 is not None:
        write(results, f"rows[{ri}].date.y", y2, conf=conf, skip_first=True)
        write(results, f"rows[{ri}].date.m", m, conf=conf)
        write(results, f"rows[{ri}].date.d", d, conf=conf)
    if h is not None:
        write(results, f"rows[{ri}].height.int", h[:3], conf=conf)
        write(results, f"rows[{ri}].height.dec", h[3:], conf=conf)
    if w is not None:
        write(results, f"rows[{ri}].weight.int", w[:3], conf=conf)
        write(results, f"rows[{ri}].weight.dec", w[3:], conf=conf)
    return results


def test_minguo_to_ad_and_zero_padding(tpl, blank_results, write):
    r = blank_results()
    write(r, "birth.y", "108")
    write(r, "birth.m", "03")
    write(r, "birth.d", "05")
    out = parse_cells(r, tpl, TODAY)
    assert out["birthDate"] == "2019-03-05"
    assert "birthDate" not in out["uncertain"]


def test_two_digit_minguo_year_with_leading_blank(tpl, blank_results, write):
    r = blank_results()
    write(r, "birth.y", " 99")  # 前導留白＝沒用到的位數，屬正常
    write(r, "birth.m", "12")
    write(r, "birth.d", "31")
    out = parse_cells(r, tpl, TODAY)
    assert out["birthDate"] == "2010-12-31"


def test_invalid_date_becomes_uncertain(tpl, blank_results, write):
    r = blank_results()
    write(r, "birth.y", "108")
    write(r, "birth.m", "13")
    write(r, "birth.d", "05")
    out = parse_cells(r, tpl, TODAY)
    assert out["birthDate"] is None and "birthDate" in out["uncertain"]


def test_height_weight_decimal_and_leading_zero(tpl, blank_results, write):
    r = blank_results()
    fill_row(write, r, 0, h="1235", w="0240")
    out = parse_cells(r, tpl, TODAY)
    m = out["measurements"][0]
    assert m["height"] == 123.5
    assert m["weight"] == 24.0  # 024.0 → 去前導零
    assert m["measureDate"] == "2026-08-22"  # 民國 115 + 1911
    assert [p for p in out["uncertain"] if p.startswith("measurements")] == []


def test_row_all_blank_is_skipped(tpl, blank_results, write):
    r = blank_results()
    fill_row(write, r, 2)  # 只填第 3 列
    out = parse_cells(r, tpl, TODAY)
    assert len(out["measurements"]) == 1


def test_uncertain_index_is_output_index_not_row_number(tpl, blank_results, write):
    r = blank_results()
    fill_row(write, r, 1)  # 表單第 2 列 → 輸出 measurements[0]
    fill_row(write, r, 3, w="0250", conf=0.99)  # 表單第 4 列 → 輸出 measurements[1]
    mark(r, "rows[3].weight.int[1]", "5", conf=0.40)  # 低信心
    out = parse_cells(r, tpl, TODAY)
    assert len(out["measurements"]) == 2
    assert out["measurements"][1]["weight"] is None
    assert "measurements[1].weight" in out["uncertain"]
    assert "measurements[3].weight" not in out["uncertain"]


def test_low_confidence_makes_null(tpl, blank_results, write):
    r = blank_results()
    fill_row(write, r, 0, h="1235", conf=0.99)
    mark(r, "rows[0].height.int[0]", "1", conf=0.5)
    out = parse_cells(r, tpl, TODAY)
    assert out["measurements"][0]["height"] is None
    assert "measurements[0].height" in out["uncertain"]


def test_unreadable_char_makes_null(tpl, blank_results, write):
    r = blank_results()
    fill_row(write, r, 0)
    mark(r, "rows[0].weight.dec[0]", None, conf=0.0)
    out = parse_cells(r, tpl, TODAY)
    assert out["measurements"][0]["weight"] is None
    assert "measurements[0].weight" in out["uncertain"]


def test_gender_needs_exactly_one_tick(tpl, blank_results, write):
    r = blank_results()
    out = parse_cells(r, tpl, TODAY)
    assert out["gender"] is None and "gender" in out["uncertain"]

    r = blank_results()
    check(r, "gender.female")
    assert parse_cells(r, tpl, TODAY)["gender"] == "female"

    r = blank_results()
    check(r, "gender.male")
    check(r, "gender.female")
    out = parse_cells(r, tpl, TODAY)
    assert out["gender"] is None and "gender" in out["uncertain"]


def test_today_row_blank_date_uses_request_today(tpl, blank_results, write):
    r = blank_results()
    ri = tpl.today_row_index
    fill_row(write, r, ri, y2=None, h="1235", w="0240")  # 今日量測列只填身高體重
    out = parse_cells(r, tpl, TODAY)
    assert out["measurements"][0]["measureDate"] == TODAY
    assert "measurements[0].measureDate" in out["uncertain"]


def test_today_row_is_chosen_by_label_not_index(tpl, blank_results, write):
    """今日量測列從 rows[5] 搬到 rows[9] 了：判定依據必須是 label，不能是索引。"""
    import dataclasses

    assert tpl.today_row_index == 9

    # 舊的寫死索引 rows[5] 現在只是普通歷史列，日期空白不可以被自動補成今天
    r = blank_results()
    fill_row(write, r, 5, y2=None, h="1235", w="0240")
    assert parse_cells(r, tpl, TODAY)["measurements"][0]["measureDate"] is None

    # 把 label 搬到第 0 列，補日期的行為也要跟著搬過去
    moved = dataclasses.replace(tpl, row_labels=("today",) + tpl.row_labels[1:9] + ("10",))
    r = blank_results()
    fill_row(write, r, 0, y2=None, h="1235", w="0240")
    out = parse_cells(r, moved, TODAY)
    assert out["measurements"][0]["measureDate"] == TODAY
    assert "今日量測列" in out["notes"]


def test_all_ten_rows_filled_yields_ten_measurements(tpl, blank_results, write):
    r = blank_results()
    for ri in range(tpl.row_count):
        fill_row(write, r, ri, y2=f"{10 + ri:02d}", m="08", d="22", h="1235", w="0240")
    out = parse_cells(r, tpl, TODAY)
    assert tpl.row_count == 10
    assert len(out["measurements"]) == 10
    assert out["measurements"][0]["measureDate"] == "2021-08-22"  # 民國 110
    assert out["measurements"][9]["measureDate"] == "2030-08-22"  # 民國 119
    assert all(m["height"] == 123.5 and m["weight"] == 24.0 for m in out["measurements"])
    assert [p for p in out["uncertain"] if p.startswith("measurements")] == []


def test_history_row_blank_date_is_not_auto_filled(tpl, blank_results, write):
    r = blank_results()
    fill_row(write, r, 0, y2=None, h="1235", w="0240")
    out = parse_cells(r, tpl, TODAY)
    assert out["measurements"][0]["measureDate"] is None
    assert "measurements[0].measureDate" in out["uncertain"]


def test_parent_height_optional_blank_not_flagged(tpl, blank_results, write):
    r = blank_results()
    write(r, "father", "175")
    out = parse_cells(r, tpl, TODAY)
    assert out["fatherHeight"] == 175
    assert out["motherHeight"] is None
    assert "motherHeight" not in out["uncertain"]


def test_parent_height_out_of_range_flagged(tpl, blank_results, write):
    r = blank_results()
    write(r, "father", "875")
    out = parse_cells(r, tpl, TODAY)
    assert out["fatherHeight"] is None and "fatherHeight" in out["uncertain"]


def test_seq_keeps_leading_zeros(tpl, blank_results, write):
    """病歷號是字串，前導零有意義，去零就是換一個病人。"""
    r = blank_results()
    write(r, "seq", " 0012345")  # 8 格靠右填，最前面留白＝沒用到的位數
    out = parse_cells(r, tpl, TODAY)
    assert out["seq"] == "0012345"
    assert "seq" not in out["uncertain"]

    r = blank_results()
    write(r, "seq", "00123456")  # 填滿 8 格
    assert parse_cells(r, tpl, TODAY)["seq"] == "00123456"

    r = blank_results()
    write(r, "seq", "  000012")
    assert parse_cells(r, tpl, TODAY)["seq"] == "000012"


def test_seq_blank_is_ok_and_not_flagged(tpl, blank_results):
    out = parse_cells(blank_results(), tpl, TODAY)
    assert out["seq"] is None and "seq" not in out["uncertain"]


def test_seq_interior_gap_is_null_and_uncertain(tpl, blank_results, write):
    """中間有空格＝讀法有歧義（0012_45 到底是幾號），不可以猜。"""
    r = blank_results()
    write(r, "seq", " 0012 45")
    out = parse_cells(r, tpl, TODAY)
    assert out["seq"] is None
    assert "seq" in out["uncertain"]


def test_seq_trailing_gap_gives_value_but_flags(tpl, blank_results, write):
    """靠右填寫，尾端留白代表可能漏填一位：仍給值，但要標黃。"""
    r = blank_results()
    write(r, "seq", "  12345 ")
    out = parse_cells(r, tpl, TODAY)
    assert out["seq"] == "12345"
    assert "seq" in out["uncertain"]


def test_seq_low_confidence_is_null_and_uncertain(tpl, blank_results, write):
    r = blank_results()
    write(r, "seq", " 0012345")
    mark(r, "seq[4]", "2", conf=0.4)
    out = parse_cells(r, tpl, TODAY)
    assert out["seq"] is None and "seq" in out["uncertain"]


def test_interior_blank_is_ambiguous(tpl, blank_results, write):
    r = blank_results()
    write(r, "birth.y", "1 8")  # 中間留白 → 讀法有歧義
    write(r, "birth.m", "03")
    write(r, "birth.d", "05")
    out = parse_cells(r, tpl, TODAY)
    assert out["birthDate"] is None and "birthDate" in out["uncertain"]


def test_missing_decimal_is_integer_without_flag(tpl, blank_results, write):
    # 實拍發現家長多半把小數格留白（8 個數值有 6 個）；空白不會被誤讀，所以算整數且不標黃
    r = blank_results()
    fill_row(write, r, 0, h="123 ", w="0240")
    out = parse_cells(r, tpl, TODAY)
    assert out["measurements"][0]["height"] == 123.0
    assert "measurements[0].height" not in out["uncertain"]


def test_out_of_range_height_rejected(tpl, blank_results, write):
    r = blank_results()
    fill_row(write, r, 0, h="9995")
    out = parse_cells(r, tpl, TODAY)
    assert out["measurements"][0]["height"] is None
    assert "measurements[0].height" in out["uncertain"]


def test_notes_are_traditional_chinese_summary(tpl, blank_results, write):
    r = blank_results()
    fill_row(write, r, 1)
    mark(r, "rows[1].weight.int[2]", "4", conf=0.3)
    out = parse_cells(r, tpl, TODAY)
    assert "第 2 列體重" in out["notes"]


def test_blank_form_yields_no_measurements(tpl, blank_results):
    out = parse_cells(blank_results(), tpl, TODAY)
    assert out["measurements"] == []
    assert out["birthDate"] is None


@pytest.mark.parametrize("threshold,expected", [(0.5, 123.5), (0.95, None)])
def test_threshold_is_configurable(tpl, blank_results, write, threshold, expected):
    r = blank_results()
    fill_row(write, r, 0, h="1235", conf=0.9)
    out = parse_cells(r, tpl, TODAY, threshold=threshold)
    assert out["measurements"][0]["height"] == expected
