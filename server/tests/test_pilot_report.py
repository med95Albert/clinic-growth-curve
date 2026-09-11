# -*- coding: utf-8 -*-
"""試用留底報表：欄位層級分類與格子層級 labels 匯出。

重點是分類判準不能被放寬——「機器有值、沒標黃、跟確認值不同」一定要算沉默錯誤，
否則整個試用就量不到唯一不可接受的那個指標。
"""
from __future__ import annotations

import csv
import json

from growth_ocr.pilot_report import (
    CORRECT,
    FLAGGED_FIXED,
    FLAGGED_OK,
    SILENT_MISS,
    SILENT_WRONG,
    export_labels,
    main,
    scan,
    to_cell_labels,
)

TODAY = "2026-09-11"


def ocr_payload(pid, data, ms=900):
    return {
        "id": pid,
        "today": TODAY,
        "data": data,
        "backend": "rapidocr",
        "ms": ms,
        "debug": {"cells": [], "quality": {}},
    }


def base_data(**over):
    d = {
        "seq": "0012345",
        "gender": "male",
        "birthDate": "2019-03-05",
        "fatherHeight": 175,
        "motherHeight": 160,
        "measurements": [{"measureDate": "2026-08-22", "height": 123.5, "weight": 24.0}],
        "uncertain": [],
        "notes": "",
    }
    d.update(over)
    return d


def write_pair(pilot_dir, pid, ocr_data, confirmed_data, ms=900):
    pilot_dir.mkdir(parents=True, exist_ok=True)
    (pilot_dir / f"{pid}.ocr.json").write_text(
        json.dumps(ocr_payload(pid, ocr_data, ms), ensure_ascii=False), encoding="utf-8"
    )
    (pilot_dir / f"{pid}.confirmed.json").write_text(
        json.dumps({"id": pid, "data": confirmed_data}, ensure_ascii=False), encoding="utf-8"
    )


def by_field(res, pid=None):
    return {
        f.field: f
        for r in res["reports"]
        for f in r.fields
        if pid is None or r.pilot_id == pid
    }


def test_perfect_pair_is_all_correct(tmp_path):
    pilot = tmp_path / "pilot"
    write_pair(pilot, "20260911T100000Z-aaaa", base_data(), base_data())
    res = scan(pilot)

    assert res["scored"] == 1 and res["photos"] == 1
    assert res["fields"] == 5 + 3  # 五個表頭欄位＋一列 ×3
    assert res["counts"][CORRECT] == res["fields"]
    assert res["accuracy"] == 1.0
    assert res["silentErrors"] == 0
    assert res["msMedian"] == 900


def test_classifies_silent_error_and_flagged_fix(tmp_path):
    """一筆沉默錯誤（158→138、沒標黃）＋一筆標黃後修正（母親身高標黃、確認值不同）。"""
    pilot = tmp_path / "pilot"
    ocr = base_data(
        motherHeight=None,
        measurements=[{"measureDate": "2026-08-22", "height": 138.0, "weight": 24.0}],
        uncertain=["motherHeight"],
    )
    confirmed = base_data(
        motherHeight=160,
        measurements=[{"measureDate": "2026-08-22", "height": 158.0, "weight": 24.0}],
    )
    write_pair(pilot, "20260911T100000Z-bbbb", ocr, confirmed)
    res = scan(pilot)
    fields = by_field(res)

    assert fields["measurements[0].height"].category == SILENT_WRONG
    assert fields["motherHeight"].category == FLAGGED_FIXED
    assert fields["measurements[0].weight"].category == CORRECT
    assert res["counts"][SILENT_WRONG] == 1 and res["counts"][FLAGGED_FIXED] == 1
    assert res["silentErrors"] == 1

    silent = res["silent"][0]
    assert (silent.pilot_id, silent.field, silent.ocr, silent.confirmed) == (
        "20260911T100000Z-bbbb",
        "measurements[0].height",
        138.0,
        158.0,
    )


def test_flagged_but_actually_correct_is_its_own_category(tmp_path):
    pilot = tmp_path / "pilot"
    ocr = base_data(uncertain=["seq", "measurements[0].measureDate"])
    write_pair(pilot, "20260911T100000Z-cccc", ocr, base_data())
    res = scan(pilot)
    fields = by_field(res)

    assert fields["seq"].category == FLAGGED_OK
    assert fields["measurements[0].measureDate"].category == FLAGGED_OK
    assert res["counts"][FLAGGED_OK] == 2
    assert res["accuracy"] == 1.0  # 誤報不算讀錯
    assert res["flagRate"] == 2 / res["fields"]
    assert res["silentErrors"] == 0


def test_empty_ocr_value_without_flag_is_a_silent_miss(tmp_path):
    pilot = tmp_path / "pilot"
    write_pair(pilot, "20260911T100000Z-dddd", base_data(motherHeight=None), base_data(motherHeight=160))
    res = scan(pilot)

    assert by_field(res)["motherHeight"].category == SILENT_MISS
    assert res["silentErrors"] == 1


def test_numbers_compare_by_value_not_by_type(tmp_path):
    """前端 JSON 會把 175 與 175.0 混著送，型別差異不可以被當成讀錯。"""
    pilot = tmp_path / "pilot"
    ocr = base_data(fatherHeight=175, measurements=[{"measureDate": "2026-08-22", "height": 123.5, "weight": 24}])
    confirmed = base_data(
        fatherHeight=175.0, measurements=[{"measureDate": "2026-08-22", "height": 123.5, "weight": 24.0}]
    )
    write_pair(pilot, "20260911T100000Z-eeee", ocr, confirmed)
    res = scan(pilot)
    assert res["counts"][CORRECT] == res["fields"]


def test_seq_leading_zero_is_significant(tmp_path):
    pilot = tmp_path / "pilot"
    write_pair(pilot, "20260911T100000Z-ffff", base_data(seq="12345"), base_data(seq="0012345"))
    res = scan(pilot)
    assert by_field(res)["seq"].category == SILENT_WRONG


def test_rows_align_by_date_when_nurse_deletes_a_row(tmp_path):
    """護理師刪掉 OCR 多讀的第一列後，後面的列不可以整個錯位變成一堆沉默錯誤。"""
    pilot = tmp_path / "pilot"
    ocr = base_data(
        measurements=[
            {"measureDate": "2026-01-01", "height": 100.0, "weight": 16.0},  # 多讀的一列
            {"measureDate": "2026-08-22", "height": 123.5, "weight": 24.0},
        ]
    )
    confirmed = base_data(measurements=[{"measureDate": "2026-08-22", "height": 123.5, "weight": 24.0}])
    write_pair(pilot, "20260911T100000Z-1111", ocr, confirmed)
    res = scan(pilot)

    assert res["ocrExtraRows"] == 1 and res["ocrMissingRows"] == 0
    assert res["silentErrors"] == 0
    assert res["counts"][CORRECT] == res["fields"]


def test_row_missed_entirely_by_ocr_counts_as_silent(tmp_path):
    pilot = tmp_path / "pilot"
    ocr = base_data(measurements=[])
    confirmed = base_data(measurements=[{"measureDate": "2026-08-22", "height": 123.5, "weight": 24.0}])
    write_pair(pilot, "20260911T100000Z-2222", ocr, confirmed)
    res = scan(pilot)

    assert res["ocrMissingRows"] == 1
    assert res["counts"][SILENT_MISS] == 3
    assert all(f.reason == "OCR 漏列" for f in res["silent"])


def test_flag_path_follows_ocr_row_index(tmp_path):
    """OCR 第 1 列標黃、護理師刪掉第 0 列後，標黃仍要算在對的那一列上。"""
    pilot = tmp_path / "pilot"
    ocr = base_data(
        measurements=[
            {"measureDate": "2026-01-01", "height": 100.0, "weight": 16.0},
            {"measureDate": "2026-08-22", "height": None, "weight": 24.0},
        ],
        uncertain=["measurements[1].height"],
    )
    confirmed = base_data(measurements=[{"measureDate": "2026-08-22", "height": 123.5, "weight": 24.0}])
    write_pair(pilot, "20260911T100000Z-3333", ocr, confirmed)
    res = scan(pilot)

    assert by_field(res)["measurements[0].height"].category == FLAGGED_FIXED
    assert res["silentErrors"] == 0


def test_geometry_failures_and_unconfirmed_are_counted_not_scored(tmp_path):
    pilot = tmp_path / "pilot"
    write_pair(pilot, "20260911T100000Z-4444", base_data(), base_data())
    (pilot / "20260911T100100Z-5555.error.json").write_text(
        json.dumps({"error": "左下角定位塊未入鏡"}, ensure_ascii=False), encoding="utf-8"
    )
    (pilot / "20260911T100200Z-6666.ocr.json").write_text(
        json.dumps(ocr_payload("20260911T100200Z-6666", base_data()), ensure_ascii=False), encoding="utf-8"
    )
    res = scan(pilot)

    assert res["photos"] == 3
    assert res["geometryFailures"] == 1
    assert res["scored"] == 1
    assert res["unconfirmed"] == ["20260911T100200Z-6666"]


def test_cli_exit_code_is_1_when_silent_errors_exist(tmp_path, capsys):
    pilot = tmp_path / "pilot"
    write_pair(pilot, "20260911T100000Z-7777", base_data(), base_data())
    assert main([str(pilot)]) == 0

    write_pair(
        pilot,
        "20260911T100100Z-8888",
        base_data(fatherHeight=178),
        base_data(fatherHeight=175),
    )
    assert main([str(pilot)]) == 1
    out = capsys.readouterr().out
    assert "沉默錯誤" in out and "fatherHeight" in out


# --- labels 匯出 -------------------------------------------------


def test_to_cell_labels_right_aligns_and_converts_roc(tpl):
    cells = dict(
        to_cell_labels(
            {
                "seq": "0012345",
                "gender": "female",
                "birthDate": "2013-10-30",  # 民國 102
                "fatherHeight": 168,
                "motherHeight": None,
                "measurements": [{"measureDate": "2025-08-22", "height": 123.5, "weight": 24.0}],
            },
            tpl,
        )
    )

    # 病歷號原樣靠右：7 碼填進 8 格，最前面那格留白（不輸出）
    assert [cells.get(f"seq[{i}]") for i in range(8)] == [None, "0", "0", "1", "2", "3", "4", "5"]
    assert cells["gender.female"] == "x" and cells["gender.male"] == ""
    assert [cells[f"birth.y[{i}]"] for i in range(3)] == ["1", "0", "2"]
    assert [cells[f"birth.m[{i}]"] for i in range(2)] == ["1", "0"]
    assert [cells[f"birth.d[{i}]"] for i in range(2)] == ["3", "0"]
    assert [cells[f"father[{i}]"] for i in range(3)] == ["1", "6", "8"]
    assert "mother[2]" not in cells  # 空值不反推

    # 量測列的民國年首格是預印的「1」，不可以輸出成標註
    assert "rows[0].date.y[0]" not in cells
    assert [cells[f"rows[0].date.y[{i}]"] for i in (1, 2)] == ["1", "4"]
    assert [cells[f"rows[0].height.int[{i}]"] for i in range(3)] == ["1", "2", "3"]
    assert cells["rows[0].height.dec[0]"] == "5"
    # 24.0：整數靠右（首格留白不輸出），小數 .0 視為空白
    assert cells.get("rows[0].weight.int[0]") is None
    assert [cells[f"rows[0].weight.int[{i}]"] for i in (1, 2)] == ["2", "4"]
    assert "rows[0].weight.dec[0]" not in cells


def test_export_labels_writes_bench_readable_csv(tmp_path, tpl):
    from growth_ocr.bench import load_labels

    pilot = tmp_path / "pilot"
    write_pair(
        pilot,
        "20260911T100000Z-9999",
        base_data(),
        base_data(measurements=[{"measureDate": "2025-08-22", "height": 123.5, "weight": 24.0}]),
    )
    out = tmp_path / "labels.csv"
    n = export_labels(pilot, out, tpl)
    assert n > 0

    with out.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["photo", "path", "value"]
    assert rows[1][0].startswith("#") and rows[1][1] == ""  # 檔頭註解，bench 會略過

    labels = load_labels(out)
    assert set(labels) == {"20260911T100000Z-9999.jpg"}  # 註解列不會變成一筆假照片
    cells = labels["20260911T100000Z-9999.jpg"]
    assert cells["seq[7]"] == "5"
    assert cells["gender.male"] == "x" and cells["gender.female"] == ""
    assert cells["rows[0].height.dec[0]"] == "5"
