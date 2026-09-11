# -*- coding: utf-8 -*-
"""bench 指標計算：沉默錯誤只算「讀錯且信心 ≥ 門檻」，漏讀與低信心都算標黃。"""
from __future__ import annotations


from growth_ocr import synth
from growth_ocr.bench import evaluate_photo, expected_gender, load_labels, run_bench
from growth_ocr.cells import CHECK_MARK
from growth_ocr.recognizers.base import StubRecognizer


def _dataset(tmp_path, tpl, count=2, seed=99):
    labels = synth.generate_dataset(tmp_path, count=count, seed=seed, tpl=tpl)
    return tmp_path, labels


def _order_and_answers(tpl, labels_for_photo, wrong: dict | None = None, conf=0.99):
    order = [c.path for c in tpl.recognizable_cells if labels_for_photo.get(c.path)]
    answers = {p: (labels_for_photo[p], conf) for p in order}
    for path, (char, c) in (wrong or {}).items():
        answers[path] = (char, c)
    return StubRecognizer(answers=answers, order=order)


def test_labels_csv_roundtrip(tmp_path, tpl):
    photo_dir, labels_path = _dataset(tmp_path, tpl, count=2)
    labels = load_labels(labels_path)
    assert set(labels) == {"synth000.jpg", "synth001.jpg"}
    assert len(labels["synth000.jpg"]) == len(tpl.recognizable_cells) + len(tpl.check_cells)


def test_labels_csv_includes_gender_checkboxes(tmp_path, tpl):
    """之前 labels.csv 漏了這兩列，bench 因此完全測不到性別。"""
    photo_dir, labels_path = _dataset(tmp_path, tpl, count=3, seed=5)
    labels = load_labels(labels_path)
    for name, cells in labels.items():
        assert "gender.male" in cells and "gender.female" in cells, name
        ticked = [p for p in ("gender.male", "gender.female") if cells[p]]
        assert len(ticked) == 1 and cells[ticked[0]] == CHECK_MARK
        assert expected_gender(cells) == ticked[0].split(".")[1]


def test_bench_scores_gender(tmp_path, tpl):
    photo_dir, labels_path = _dataset(tmp_path, tpl, count=2, seed=11)
    all_labels = load_labels(labels_path)
    rec = _order_and_answers(tpl, all_labels["synth000.jpg"])
    rep = evaluate_photo(photo_dir / "synth000.jpg", all_labels["synth000.jpg"], tpl, rec, 0.85, "2026-09-10", None)
    assert rep.gender_expected in ("male", "female")
    assert rep.gender_got == rep.gender_expected
    assert rep.gender_scored and rep.gender_ok and not rep.gender_silent


def test_gender_accuracy_is_aggregated(tmp_path, tpl, monkeypatch):
    photo_dir, labels_path = _dataset(tmp_path, tpl, count=2, seed=13)
    all_labels = load_labels(labels_path)
    monkeypatch.setattr(
        "growth_ocr.bench.get_recognizer",
        lambda name: _order_and_answers(tpl, next(iter(all_labels.values()))),
    )
    res = run_bench(photo_dir, labels_path, backend="stub")
    assert res["genderScored"] == 2
    assert res["genderAccuracy"] == 1.0
    assert res["genderSilentErrors"] == 0


def test_gender_label_accepts_any_non_empty_mark(tmp_path, tpl):
    """docs/BENCHMARK.md 的人工標註寫 "1"，synth 寫 "x"，兩種都要算數。"""
    photo_dir, labels_path = _dataset(tmp_path, tpl, count=1)
    labels = dict(load_labels(labels_path)["synth000.jpg"])
    ticked = next(p for p in ("gender.male", "gender.female") if labels[p])
    labels[ticked] = "1"
    rec = _order_and_answers(tpl, labels)
    rep = evaluate_photo(photo_dir / "synth000.jpg", labels, tpl, rec, 0.85, "2026-09-10", None)
    assert expected_gender(labels) == ticked.split(".")[1]
    assert rep.gender_ok and rep.correct == rep.total


def test_gender_not_scored_when_labels_lack_checkboxes(tmp_path, tpl):
    photo_dir, labels_path = _dataset(tmp_path, tpl, count=1)
    labels = {k: v for k, v in load_labels(labels_path)["synth000.jpg"].items() if not k.startswith("gender.")}
    rec = _order_and_answers(tpl, labels)
    rep = evaluate_photo(photo_dir / "synth000.jpg", labels, tpl, rec, 0.85, "2026-09-10", None)
    assert rep.gender_expected is None and not rep.gender_scored


def test_perfect_run_has_no_silent_errors(tmp_path, tpl):
    photo_dir, labels_path = _dataset(tmp_path, tpl, count=1)
    labels = load_labels(labels_path)["synth000.jpg"]
    rec = _order_and_answers(tpl, labels)
    rep = evaluate_photo(photo_dir / "synth000.jpg", labels, tpl, rec, 0.85, "2026-09-10", None)
    assert rep.error is None
    assert rep.correct == rep.total == len(labels)
    assert rep.silent == [] and rep.missed == [] and rep.flagged == 0
    assert rep.perfect


def test_high_confidence_misread_counts_as_silent(tmp_path, tpl):
    photo_dir, labels_path = _dataset(tmp_path, tpl, count=1)
    labels = load_labels(labels_path)["synth000.jpg"]
    victim = next(p for p, v in labels.items() if v)
    other = "9" if labels[victim] != "9" else "8"
    rec = _order_and_answers(tpl, labels, wrong={victim: (other, 0.99)})
    rep = evaluate_photo(photo_dir / "synth000.jpg", labels, tpl, rec, 0.85, "2026-09-10", None)
    assert [s[0] for s in rep.silent] == [victim]
    assert not rep.perfect


def test_low_confidence_misread_is_only_flagged(tmp_path, tpl):
    photo_dir, labels_path = _dataset(tmp_path, tpl, count=1)
    labels = load_labels(labels_path)["synth000.jpg"]
    victim = next(p for p, v in labels.items() if v)
    other = "9" if labels[victim] != "9" else "8"
    rec = _order_and_answers(tpl, labels, wrong={victim: (other, 0.20)})
    rep = evaluate_photo(photo_dir / "synth000.jpg", labels, tpl, rec, 0.85, "2026-09-10", None)
    assert rep.silent == []
    assert rep.flagged == 1


def test_unreadable_cell_counts_as_missed(tmp_path, tpl):
    photo_dir, labels_path = _dataset(tmp_path, tpl, count=1)
    labels = load_labels(labels_path)["synth000.jpg"]
    victim = next(p for p, v in labels.items() if v)
    rec = _order_and_answers(tpl, labels, wrong={victim: (None, 0.0)})
    rep = evaluate_photo(photo_dir / "synth000.jpg", labels, tpl, rec, 0.85, "2026-09-10", None)
    assert [m[0] for m in rep.missed] == [victim]
    assert rep.silent == []


def test_run_bench_aggregates(tmp_path, tpl, monkeypatch):
    from growth_ocr.bench import PhotoReport

    photo_dir, labels_path = _dataset(tmp_path, tpl, count=3, seed=7)
    all_labels = load_labels(labels_path)

    # 每張逐一評估都該全對（stub 直接回正解）
    for name, labels in all_labels.items():
        rec = _order_and_answers(tpl, labels)
        assert evaluate_photo(photo_dir / name, labels, tpl, rec, 0.85, "2026-09-10", None).perfect

    # run_bench 只有一個 recognizer，這裡用第一張的答案，驗的是彙總欄位而非準確率
    monkeypatch.setattr(
        "growth_ocr.bench.get_recognizer",
        lambda name: _order_and_answers(tpl, next(iter(all_labels.values()))),
    )
    res = run_bench(photo_dir, labels_path, backend="stub")
    assert res["photos"] == 3
    assert res["cells"] == 3 * (len(tpl.recognizable_cells) + len(tpl.check_cells))
    assert isinstance(res["reports"][0], PhotoReport)
    assert 0.0 <= res["cellAccuracy"] <= 1.0
    assert res["geometryFailures"] == 0


def test_dump_cells_writes_pngs(tmp_path, tpl):
    photo_dir, labels_path = _dataset(tmp_path / "photos", tpl, count=1)
    labels = load_labels(labels_path)["synth000.jpg"]
    rec = _order_and_answers(tpl, labels)
    dump = tmp_path / "cells"
    evaluate_photo(photo_dir / "synth000.jpg", labels, tpl, rec, 0.85, "2026-09-10", str(dump))
    pngs = list(dump.glob("*.png"))
    assert len(pngs) == len(tpl.cells) == 173
    assert any("synth000__rows_9-weight-dec_0.png" == p.name for p in pngs)
    assert any("synth000__seq_7.png" == p.name for p in pngs)
