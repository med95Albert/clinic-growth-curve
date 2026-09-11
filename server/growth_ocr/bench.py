# -*- coding: utf-8 -*-
"""辨識後端 benchmark：算每格準確率、整張全對率、沉默錯誤數、標黃率、每張耗時。

沉默錯誤（讀錯了、信心卻高過門檻、系統不會標黃）是唯一不可接受的指標；
其他指標差只是慢一點、麻煩一點，沉默錯誤是把錯的身高畫進生長曲線。

用法：
    python -m growth_ocr.bench 照片資料夾 --labels labels.csv --backend rapidocr [--dump-cells 目錄]
labels.csv 欄位：photo,path,value（value 空字串＝該格空白）。
path 若是 gender.male／gender.female，value 只要非空就代表有打勾（1／x／✓ 皆可）——
**有這兩列才算得出性別準確率**，沒有的話性別完全不會被測到。
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .geometry import GeometryError
from .parse import DEFAULT_THRESHOLD
from .pipeline import run_image
from .recognizers.base import get_recognizer
from .template import CHECK, load_template

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
GENDER_PATHS = ("gender.male", "gender.female")


def load_labels(path: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = defaultdict(dict)
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            photo = (row.get("photo") or "").strip()
            cell_path = (row.get("path") or "").strip()
            if not photo or not cell_path:
                continue
            out[photo][cell_path] = (row.get("value") or "").strip()
    return dict(out)


def expected_gender(labels: dict[str, str]) -> str | None:
    """從 labels.csv 的兩個勾選框算出性別正解；沒給或兩個都給就是無正解（不計分）。"""
    on = [p.split(".")[1] for p in GENDER_PATHS if (labels.get(p) or "").strip()]
    return on[0] if len(on) == 1 else None


@dataclass
class PhotoReport:
    photo: str
    ms: int = 0
    total: int = 0
    correct: int = 0
    silent: list[tuple[str, str, str, float]] = field(default_factory=list)  # path, 正解, 讀成, conf
    missed: list[tuple[str, str]] = field(default_factory=list)  # 有寫卻讀成空白
    flagged: int = 0
    uncertain: int = 0
    error: str | None = None
    gender_expected: str | None = None  # None＝labels 沒給正解，這張不計性別分
    gender_got: str | None = None

    @property
    def perfect(self) -> bool:
        return self.error is None and self.total > 0 and self.correct == self.total

    @property
    def gender_scored(self) -> bool:
        return self.error is None and self.gender_expected is not None

    @property
    def gender_ok(self) -> bool:
        return self.gender_scored and self.gender_got == self.gender_expected

    @property
    def gender_silent(self) -> bool:
        """讀出了性別、卻讀錯——不會標黃，會直接畫到錯的生長曲線上。"""
        return self.gender_scored and self.gender_got is not None and not self.gender_ok


def evaluate_photo(
    image_path: Path,
    labels: dict[str, str],
    tpl,
    recognizer,
    threshold: float,
    today: str,
    dump_dir: str | None,
) -> PhotoReport:
    rep = PhotoReport(photo=image_path.name)
    started = time.perf_counter()
    try:
        outcome = run_image(
            image_path.read_bytes(),
            tpl,
            recognizer,
            today=today,
            threshold=threshold,
            include_debug=False,
            dump_dir=dump_dir,
            dump_prefix=image_path.stem,
        )
    except GeometryError as exc:
        rep.error = str(exc)
        rep.ms = int((time.perf_counter() - started) * 1000)
        return rep

    rep.ms = outcome.ms
    rep.uncertain = len(outcome.data.get("uncertain", []))
    rep.gender_expected = expected_gender(labels)
    rep.gender_got = outcome.data.get("gender")
    ink = {ci.cell.path: (not ci.blank) for ci in outcome.cell_images}
    kinds = {c.path: c.kind for c in tpl.cells}

    for path, truth in labels.items():
        inked = ink.get(path, False)
        if kinds.get(path) == CHECK:
            # 勾選框沒有辨識器，墨水就是答案，正解只看 value 有沒有填（人工標註寫 1 或 x 都算）。
            # 它的「沉默錯誤」以整張的性別結果判定（gender_silent），不在這裡重複計
            # ——單一個框判錯未必會讓性別判錯（兩個框都判有墨水時 parse 本來就會標黃）。
            rep.total += 1
            if inked == bool(truth.strip()):
                rep.correct += 1
            continue
        if not inked:
            predicted, conf = "", 1.0
        else:
            char, conf = outcome.recognized.get(path, (None, 0.0))
            predicted = char or ""
        rep.total += 1
        confident = conf >= threshold and predicted != ""
        if predicted == truth:
            rep.correct += 1
            if not confident and truth != "":
                rep.flagged += 1
            continue
        if predicted == "" and truth != "":
            rep.missed.append((path, truth))
            rep.flagged += 1
        elif confident:
            rep.silent.append((path, truth, predicted, round(float(conf), 3)))
        else:
            rep.flagged += 1
    return rep


def run_bench(
    photo_dir: Path,
    labels_path: Path,
    backend: str = "rapidocr",
    threshold: float = DEFAULT_THRESHOLD,
    today: str = "2026-09-10",
    dump_cells: Path | None = None,
    template_path: Path | None = None,
) -> dict:
    tpl = load_template(template_path)
    labels = load_labels(labels_path)
    recognizer = get_recognizer(backend)
    recognizer.warmup()

    photos = sorted(p for p in photo_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    reports: list[PhotoReport] = []
    for p in photos:
        if p.name not in labels:
            continue
        reports.append(
            evaluate_photo(
                p, labels[p.name], tpl, recognizer, threshold, today, str(dump_cells) if dump_cells else None
            )
        )

    total = sum(r.total for r in reports)
    correct = sum(r.correct for r in reports)
    silent = sum(len(r.silent) for r in reports)
    missed = sum(len(r.missed) for r in reports)
    flagged = sum(r.flagged for r in reports)
    ok_reports = [r for r in reports if r.error is None]
    times = sorted(r.ms for r in ok_reports)
    gender_scored = sum(1 for r in reports if r.gender_scored)
    gender_correct = sum(1 for r in reports if r.gender_ok)
    gender_silent = sum(1 for r in reports if r.gender_silent)
    return {
        "backend": backend,
        "threshold": threshold,
        "photos": len(reports),
        "geometryFailures": sum(1 for r in reports if r.error),
        "cells": total,
        "cellAccuracy": (correct / total) if total else 0.0,
        "formAccuracy": (sum(1 for r in reports if r.perfect) / len(reports)) if reports else 0.0,
        "silentErrors": silent,
        "genderScored": gender_scored,
        "genderCorrect": gender_correct,
        "genderAccuracy": (gender_correct / gender_scored) if gender_scored else 0.0,
        "genderSilentErrors": gender_silent,
        "missedCells": missed,
        "flagRate": (flagged / total) if total else 0.0,
        "uncertainPerForm": (sum(r.uncertain for r in ok_reports) / len(ok_reports)) if ok_reports else 0.0,
        "msMedian": times[len(times) // 2] if times else 0,
        "msMean": (sum(times) / len(times)) if times else 0,
        "msMax": times[-1] if times else 0,
        "reports": reports,
    }


def print_report(res: dict) -> None:
    print(f"後端：{res['backend']}　信心門檻：{res['threshold']}")
    print(f"照片：{res['photos']} 張（幾何失敗 {res['geometryFailures']} 張）　格子：{res['cells']}")
    print(f"每格準確率：{res['cellAccuracy'] * 100:.2f}%")
    print(f"整張全對率：{res['formAccuracy'] * 100:.2f}%")
    print(f"沉默錯誤數：{res['silentErrors']}　（讀錯且信心 ≥ 門檻，唯一不可接受的指標）")
    if res["genderScored"]:
        print(
            f"性別準確率：{res['genderAccuracy'] * 100:.2f}%"
            f"（{res['genderCorrect']}/{res['genderScored']} 張）"
            f"　性別沉默錯誤：{res['genderSilentErrors']}"
        )
    else:
        print("性別準確率：labels.csv 沒有 gender.male／gender.female，未計分")
    print(f"漏讀（有寫卻當空白）：{res['missedCells']}")
    print(f"標黃率（格）：{res['flagRate'] * 100:.2f}%　平均每張標黃欄位：{res['uncertainPerForm']:.1f}")
    print(f"每張耗時：中位數 {res['msMedian']}ms　平均 {res['msMean']:.0f}ms　最慢 {res['msMax']}ms")
    for rep in res["reports"]:
        if rep.error:
            print(f"  [幾何失敗] {rep.photo}：{rep.error}")
        for path, truth, got, conf in rep.silent:
            print(f"  [沉默錯誤] {rep.photo} {path}：正解 {truth!r} 讀成 {got!r}（conf {conf}）")
        for path, truth in rep.missed[:5]:
            print(f"  [漏讀] {rep.photo} {path}：正解 {truth!r} 被當成空白")
        if rep.gender_scored and not rep.gender_ok:
            tag = "沉默錯誤" if rep.gender_silent else "已標黃"
            print(f"  [性別{tag}] {rep.photo}：正解 {rep.gender_expected} 讀成 {rep.gender_got}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="辨識後端 benchmark")
    ap.add_argument("photo_dir", type=Path)
    ap.add_argument("--labels", type=Path, required=True)
    ap.add_argument("--backend", default="rapidocr")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--today", default="2026-09-10")
    ap.add_argument("--dump-cells", type=Path, default=None, help="把每格影像存成 PNG，供標註／訓練")
    ap.add_argument("--json", type=Path, default=None, help="另存 JSON 結果")
    args = ap.parse_args(argv)

    res = run_bench(
        args.photo_dir,
        args.labels,
        backend=args.backend,
        threshold=args.threshold,
        today=args.today,
        dump_cells=args.dump_cells,
    )
    print_report(res)
    if args.json:
        payload = {k: v for k, v in res.items() if k != "reports"}
        payload["perPhoto"] = [
            {"photo": r.photo, "ms": r.ms, "correct": r.correct, "total": r.total, "error": r.error}
            for r in res["reports"]
        ]
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if (res["silentErrors"] or res["genderSilentErrors"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
