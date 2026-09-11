# -*- coding: utf-8 -*-
"""單張（或多張）照片辨識 CLI：不需要標註檔，給實測時「拍一張就看結果」用。

    python -m growth_ocr.recognize 照片.jpg [更多照片...] [--dump 目錄] [--draft-labels labels.csv]

--dump         另存校正後影像與「疊圖」（每格標出辨識結果與信心，綠＝可信、橘＝低信心、灰＝空白），
               一眼看出是幾何偏了還是模型讀錯。
--draft-labels 把辨識結果寫成 labels.csv 格式當草稿：人只要修正錯的格子，不用從零標 171 格。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

import cv2

from .geometry import GeometryError
from .parse import DEFAULT_THRESHOLD
from .pipeline import run_image
from .recognizers.base import get_recognizer
from .template import PX_PER_MM, load_template


def _overlay(outcome, threshold: float):
    img = cv2.cvtColor(outcome.corrected.image, cv2.COLOR_GRAY2BGR)
    for ci in outcome.cell_images:
        cell = ci.cell
        x0, y0, x1, y1 = cell.rect_px(0.0, PX_PER_MM)
        if cell.kind != "digit" or cell.skip:
            cv2.rectangle(img, (x0, y0), (x1, y1), (160, 160, 160), 1)
            continue
        char, conf = outcome.recognized.get(cell.path, (None, 0.0))
        if ci.blank:
            color = (170, 170, 170)
        elif char is not None and conf >= threshold:
            color = (40, 170, 40)
        else:
            color = (0, 140, 255)
        cv2.rectangle(img, (x0, y0), (x1, y1), color, 2)
        if not ci.blank:
            label = f"{char or '?'} {conf:.2f}"
            cv2.putText(img, label, (x0, max(12, y0 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
    return img


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="單張照片辨識（實測用）")
    ap.add_argument("photos", nargs="+", type=Path)
    ap.add_argument("--backend", default="rapidocr")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--today", default=date.today().isoformat())
    ap.add_argument("--dump", type=Path, default=None, help="另存校正影像、疊圖與每格 PNG")
    ap.add_argument("--draft-labels", type=Path, default=None, help="把辨識結果附加成 labels.csv 草稿")
    ap.add_argument("--quiet", action="store_true", help="只印摘要，不印完整 JSON")
    args = ap.parse_args(argv)

    tpl = load_template()
    recognizer = get_recognizer(args.backend)
    recognizer.warmup()
    if args.dump:
        args.dump.mkdir(parents=True, exist_ok=True)

    draft_rows: list[tuple[str, str, str]] = []
    failures = 0
    for photo in args.photos:
        print(f"\n===== {photo.name} =====")
        try:
            outcome = run_image(
                photo.read_bytes(), tpl, recognizer, today=args.today, threshold=args.threshold,
                dump_dir=str(args.dump) if args.dump else None, dump_prefix=photo.stem,
            )
        except GeometryError as exc:
            failures += 1
            print(f"✗ 幾何失敗：{exc}")
            continue

        d = outcome.data
        low = [(p, c, conf) for p, (c, conf) in outcome.recognized.items() if c is None or conf < args.threshold]
        print(f"後端 {outcome.backend}　耗時 {outcome.ms} ms　品質：{outcome.debug.get('quality', {})}")
        print(f"病歷號 {d['seq']!r}　性別 {d['gender']}　生日 {d['birthDate']}　父 {d['fatherHeight']}　母 {d['motherHeight']}")
        for i, m in enumerate(d["measurements"]):
            print(f"  [{i}] {m['measureDate']}　{m['height']} cm　{m['weight']} kg")
        print(f"不確定欄位：{d['uncertain'] or '無'}")
        if d.get("notes"):
            print(f"備註：{d['notes']}")
        if low:
            print(f"低信心／無法辨識的格子（{len(low)}）：" + "、".join(f"{p}={c or '?'}({conf:.2f})" for p, c, conf in low))
        if not args.quiet:
            print(json.dumps(d, ensure_ascii=False, indent=2))

        if args.dump:
            cv2.imwrite(str(args.dump / f"{photo.stem}_warped.png"), outcome.corrected.image)
            cv2.imwrite(str(args.dump / f"{photo.stem}_overlay.png"), _overlay(outcome, args.threshold))
            print(f"已存 {args.dump / (photo.stem + '_overlay.png')}")

        for p, (c, _conf) in outcome.recognized.items():
            if c is not None:
                draft_rows.append((photo.name, p, c))
        if d["gender"] in ("male", "female"):
            draft_rows.append((photo.name, f"gender.{d['gender']}", "x"))

    if args.draft_labels and draft_rows:
        new_file = not args.draft_labels.exists()
        with args.draft_labels.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["photo", "path", "value"])
            w.writerows(draft_rows)
        print(f"\n已把 {len(draft_rows)} 列辨識結果附加到 {args.draft_labels}（請修正錯的格子後再跑 bench）")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
