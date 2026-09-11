# -*- coding: utf-8 -*-
"""試用留底 → 欄位層級準確率報表。

試用期間 `PILOT_DIR` 會留下三種檔案（見 api.py）：
    <id>.jpg            上傳的原始影像
    <id>.ocr.json       機器辨識輸出（data / backend / ms / debug）
    <id>.confirmed.json 護理師在核對卡按下送出時的最終值
配對起來就是一組「不必人工標註的 benchmark 樣本」：確認值當正解、uncertain 當標黃紀錄。

計分單位是**欄位**不是格子——護理師確認的是欄位，而且欄位才是真正會畫進生長曲線的東西。
分類裡只有「沉默錯誤」不可接受：機器給了值、沒標黃、跟護理師最後填的不一樣，
代表錯的數值會安安靜靜地畫進曲線。其餘分類差只是麻煩。

用法：
    python -m growth_ocr.pilot_report <PILOT_DIR> [--json out.json] [--export-labels labels.csv]
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

# 欄位分類
CORRECT = "correct"  # 一致，且沒標黃
FLAGGED_OK = "flagged_ok"  # 標黃了但其實讀對（誤報，只是多花護理師一眼）
FLAGGED_FIXED = "flagged_fixed"  # 標黃了，護理師確實改掉（系統照設計運作）
SILENT_WRONG = "silent_wrong"  # 有值、沒標黃、跟確認值不同 → 沉默錯誤
SILENT_MISS = "silent_miss"  # 空值、沒標黃、確認值有東西 → 沉默漏讀

SILENT = (SILENT_WRONG, SILENT_MISS)

CATEGORY_ZH = {
    CORRECT: "正確",
    FLAGGED_OK: "標黃但其實正確",
    FLAGGED_FIXED: "標黃後修正",
    SILENT_WRONG: "沉默錯誤",
    SILENT_MISS: "沉默漏讀",
}

TOP_FIELDS = ("seq", "gender", "birthDate", "fatherHeight", "motherHeight")
ROW_FIELDS = ("measureDate", "height", "weight")

NUMERIC_FIELDS = {"fatherHeight", "motherHeight", "height", "weight"}


# --- 值正規化 ---------------------------------------------------


def _empty(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _norm(field_name: str, v):
    """比對用的正規化。病歷號是字串（前導零有意義），身高體重是數字（175 == 175.0）。"""
    if _empty(v):
        return None
    if field_name in NUMERIC_FIELDS:
        try:
            return round(float(v), 3)
        except (TypeError, ValueError):
            return str(v).strip()
    return str(v).strip()


def _show(v) -> str:
    return "（空白）" if _empty(v) else str(v)


# --- 一筆欄位比對 -----------------------------------------------


@dataclass
class FieldResult:
    pilot_id: str
    field: str  # 顯示用名稱，例如 "measurements[2].height"
    ocr: object
    confirmed: object
    flagged: bool
    category: str
    reason: str = ""  # 只有特殊情況才填，例如 OCR 整列漏掉


@dataclass
class PairReport:
    pilot_id: str
    ms: int = 0
    backend: str = ""
    fields: list[FieldResult] = field(default_factory=list)
    ocr_missing_rows: int = 0  # 確認資料有、OCR 對不上的列
    ocr_extra_rows: int = 0  # OCR 有、確認資料沒有的列（護理師刪掉的）


def classify(pilot_id: str, name: str, ocr_value, confirmed_value, flagged: bool, reason: str = "") -> FieldResult:
    base = name.split(".")[-1].split("[")[0]
    same = _norm(base, ocr_value) == _norm(base, confirmed_value)
    if same:
        category = FLAGGED_OK if flagged else CORRECT
    elif flagged:
        category = FLAGGED_FIXED
    elif _empty(ocr_value):
        category = SILENT_MISS
    else:
        category = SILENT_WRONG
    return FieldResult(pilot_id, name, ocr_value, confirmed_value, flagged, category, reason)


# --- 列對齊 -----------------------------------------------------


def align_rows(ocr_rows: list[dict], confirmed_rows: list[dict]) -> tuple[list[int | None], list[int]]:
    """以確認後的列為準，替每一列找出對應的 OCR 列索引。

    先用日期配對（護理師刪列、補列之後位置會整個位移，日期才是穩定的鍵），
    剩下的才退回位置配對。回傳 (每個確認列對到的 OCR 索引或 None, 沒被對到的 OCR 索引)。
    """
    used: set[int] = set()
    matched: list[int | None] = [None] * len(confirmed_rows)

    for ci, crow in enumerate(confirmed_rows):
        cdate = _norm("measureDate", crow.get("measureDate"))
        if cdate is None:
            continue
        for oi, orow in enumerate(ocr_rows):
            if oi in used:
                continue
            if _norm("measureDate", orow.get("measureDate")) == cdate:
                matched[ci] = oi
                used.add(oi)
                break

    for ci in range(len(confirmed_rows)):
        if matched[ci] is not None:
            continue
        if ci < len(ocr_rows) and ci not in used:
            matched[ci] = ci
            used.add(ci)

    leftover = [oi for oi in range(len(ocr_rows)) if oi not in used]
    return matched, leftover


# --- 一組 ocr/confirmed 配對 -------------------------------------


def compare_pair(pilot_id: str, ocr_json: dict, confirmed_json: dict) -> PairReport:
    ocr_data = ocr_json.get("data") or {}
    confirmed = confirmed_json.get("data") or {}
    flags = set(ocr_data.get("uncertain") or [])
    rep = PairReport(pilot_id=pilot_id, ms=int(ocr_json.get("ms") or 0), backend=str(ocr_json.get("backend") or ""))

    for name in TOP_FIELDS:
        rep.fields.append(classify(pilot_id, name, ocr_data.get(name), confirmed.get(name), name in flags))

    ocr_rows = list(ocr_data.get("measurements") or [])
    confirmed_rows = list(confirmed.get("measurements") or [])
    matched, leftover = align_rows(ocr_rows, confirmed_rows)
    rep.ocr_extra_rows = len(leftover)
    rep.ocr_missing_rows = sum(1 for m in matched if m is None)

    for ci, crow in enumerate(confirmed_rows):
        oi = matched[ci]
        orow = ocr_rows[oi] if oi is not None else {}
        for f in ROW_FIELDS:
            # 標黃路徑用 OCR 當時的列號；OCR 根本沒有這一列時就不可能標黃 → 落進沉默漏讀
            path = f"measurements[{oi}].{f}" if oi is not None else None
            rep.fields.append(
                classify(
                    pilot_id,
                    f"measurements[{ci}].{f}",
                    orow.get(f),
                    crow.get(f),
                    path in flags if path else False,
                    reason="OCR 漏列" if oi is None else "",
                )
            )
    return rep


# --- 掃描 PILOT_DIR ---------------------------------------------


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 壞掉的留底檔不該讓整份報表跑不出來
        return None


def scan(pilot_dir: Path) -> dict:
    ocr_ids = sorted(p.name[: -len(".ocr.json")] for p in pilot_dir.glob("*.ocr.json"))
    error_ids = sorted(p.name[: -len(".error.json")] for p in pilot_dir.glob("*.error.json"))

    reports: list[PairReport] = []
    unconfirmed: list[str] = []
    unreadable: list[str] = []
    for pid in ocr_ids:
        ocr_json = _read_json(pilot_dir / f"{pid}.ocr.json")
        if ocr_json is None:
            unreadable.append(pid)
            continue
        confirmed_path = pilot_dir / f"{pid}.confirmed.json"
        if not confirmed_path.exists():
            unconfirmed.append(pid)  # 護理師沒按送出（重拍、換人、直接手輸）→ 沒有正解，不計分
            continue
        confirmed_json = _read_json(confirmed_path)
        if confirmed_json is None:
            unreadable.append(pid)
            continue
        reports.append(compare_pair(pid, ocr_json, confirmed_json))

    fields = [f for r in reports for f in r.fields]
    counts = {k: sum(1 for f in fields if f.category == k) for k in CATEGORY_ZH}
    total = len(fields)
    flagged = sum(1 for f in fields if f.flagged)
    times = sorted(r.ms for r in reports)
    silent = [f for f in fields if f.category in SILENT]

    return {
        "pilotDir": str(pilot_dir),
        "photos": len(ocr_ids) + len(error_ids),
        "geometryFailures": len(error_ids),
        "scored": len(reports),
        "unconfirmed": unconfirmed,
        "unreadable": unreadable,
        "fields": total,
        "counts": counts,
        "accuracy": ((counts[CORRECT] + counts[FLAGGED_OK]) / total) if total else 0.0,
        "flagRate": (flagged / total) if total else 0.0,
        "silent": silent,
        "silentErrors": len(silent),
        "ocrMissingRows": sum(r.ocr_missing_rows for r in reports),
        "ocrExtraRows": sum(r.ocr_extra_rows for r in reports),
        "msMedian": times[len(times) // 2] if times else 0,
        "reports": reports,
    }


def print_report(res: dict) -> None:
    print(f"試用留底：{res['pilotDir']}")
    print(f"照片：{res['photos']} 張（幾何失敗 {res['geometryFailures']} 張、可計分 {res['scored']} 張）")
    if res["unconfirmed"]:
        print(f"　未確認（沒有 confirmed.json，不計分）：{len(res['unconfirmed'])} 張")
    if res["unreadable"]:
        print(f"　留底檔毀損：{len(res['unreadable'])} 張 {res['unreadable'][:5]}")
    print(f"欄位總數：{res['fields']}")
    c = res["counts"]
    print(
        f"正確率：{res['accuracy'] * 100:.2f}%"
        f"（正確 {c[CORRECT]}　標黃但其實正確 {c[FLAGGED_OK]}）"
    )
    print(f"標黃率：{res['flagRate'] * 100:.2f}%　其中確實被改掉 {c[FLAGGED_FIXED]} 個")
    print(
        f"沉默錯誤：{res['silentErrors']} 個"
        f"（讀錯 {c[SILENT_WRONG]}　漏讀 {c[SILENT_MISS]}）　← 唯一不可接受的指標"
    )
    print(f"列對齊：OCR 漏列 {res['ocrMissingRows']}　OCR 多讀（護理師刪掉）{res['ocrExtraRows']}")
    print(f"每張耗時中位數：{res['msMedian']} ms")
    for f in res["silent"]:
        tag = CATEGORY_ZH[f.category] + (f"／{f.reason}" if f.reason else "")
        print(f"  [{tag}] {f.pilot_id} {f.field}：機器 {_show(f.ocr)} → 確認 {_show(f.confirmed)}")


# --- 反推格子層級 labels ----------------------------------------

LABELS_NOTE = (
    "由欄位值反推的參考標註：前導留白的格子無法判斷是否原本就寫了 0，"
    "小數格也分不出「留白」與「寫了 0」，僅供參考；正式 benchmark 仍需人工複核"
)


def _right(text: str, width: int) -> list[str]:
    """靠右填：回傳每格的值，前面填不滿的用空字串（＝空白格）。"""
    text = text[-width:]
    return [""] * (width - len(text)) + list(text)


def _roc_year(iso: str) -> str:
    return str(int(iso[:4]) - 1911)


def _date_rows(prefix: str, iso: str, tpl, out: list[tuple[str, str]]) -> None:
    """日期 → 年／月／日三組格子。年的第一格在量測列是預印的「1」，要跳過。"""
    y, m, d = _roc_year(iso), iso[5:7], iso[8:10]
    for part, text in (("y", y), ("m", m), ("d", d)):
        cells = tpl.group(f"{prefix}.{part}")
        writable = [c for c in cells if not c.skip]
        pre = "".join(c.preprinted or "" for c in cells if c.skip)
        if pre and text.startswith(pre):
            text = text[len(pre) :]
        for cell, value in zip(writable, _right(text, len(writable))):
            out.append((cell.path, value))


def _decimal_rows(prefix: str, value, tpl, out: list[tuple[str, str]]) -> None:
    """身高體重：三位整數格＋一位小數格。小數為 .0 一律當空白（家長多半就是留白）。"""
    if _empty(value):
        return
    whole = int(float(value))
    dec = int(round((float(value) - whole) * 10))
    int_cells = [c for c in tpl.group(f"{prefix}.int") if not c.skip]
    for cell, ch in zip(int_cells, _right(str(whole), len(int_cells))):
        out.append((cell.path, ch))
    for cell in (c for c in tpl.group(f"{prefix}.dec") if not c.skip):
        out.append((cell.path, "" if dec == 0 else str(dec)))


def to_cell_labels(data: dict, tpl, ocr_rows: list[dict] | None = None) -> list[tuple[str, str]]:
    """確認後的欄位值 → [(cell path, 值)]；空白格不輸出（見 LABELS_NOTE）。"""
    from .cells import CHECK_MARK  # 延後 import：只有匯出 labels 才需要，報表本身不必載 opencv

    out: list[tuple[str, str]] = []

    seq = data.get("seq")
    if not _empty(seq):
        cells = [c for c in tpl.group("seq") if not c.skip]
        for cell, ch in zip(cells, _right(str(seq).strip(), len(cells))):
            out.append((cell.path, ch))

    gender = data.get("gender")
    if gender in ("male", "female"):
        # 兩個框都要有一列，bench 才算得出性別準確率（沒打的那個框值留空）
        out.append(("gender.male", CHECK_MARK if gender == "male" else ""))
        out.append(("gender.female", CHECK_MARK if gender == "female" else ""))

    birth = data.get("birthDate")
    if not _empty(birth):
        _date_rows("birth", str(birth), tpl, out)

    for key, group in (("fatherHeight", "father"), ("motherHeight", "mother")):
        v = data.get(key)
        if _empty(v):
            continue
        cells = [c for c in tpl.group(group) if not c.skip]
        for cell, ch in zip(cells, _right(str(int(float(v))), len(cells))):
            out.append((cell.path, ch))

    # 列號要對回表單上的列，不是確認資料的順序：用日期／位置對齊 OCR 的列
    rows = list(data.get("measurements") or [])
    matched, _ = align_rows(list(ocr_rows or []), rows)
    taken = {m for m in matched if m is not None}
    for ci, row in enumerate(rows):
        ri = matched[ci]
        if ri is None:  # OCR 沒對到這一列，塞進還沒被佔用的最小列號
            ri = next((i for i in range(tpl.row_count) if i not in taken), tpl.row_count)
            taken.add(ri)
        if ri >= tpl.row_count:
            continue  # 護理師手動加的列在表單上沒有對應格子
        if not _empty(row.get("measureDate")):
            _date_rows(f"rows[{ri}].date", str(row["measureDate"]), tpl, out)
        _decimal_rows(f"rows[{ri}].height", row.get("height"), tpl, out)
        _decimal_rows(f"rows[{ri}].weight", row.get("weight"), tpl, out)

    return [(path, v) for path, v in out if v != "" or path.startswith("gender.")]


def export_labels(pilot_dir: Path, out_path: Path, tpl) -> int:
    """把每一組確認值寫成 bench 吃得下的 labels.csv。回傳寫出的列數。"""
    rows: list[tuple[str, str, str]] = []
    for confirmed_path in sorted(pilot_dir.glob("*.confirmed.json")):
        pid = confirmed_path.name[: -len(".confirmed.json")]
        confirmed = _read_json(confirmed_path)
        if not confirmed:
            continue
        ocr_json = _read_json(pilot_dir / f"{pid}.ocr.json") or {}
        ocr_rows = ((ocr_json.get("data") or {}).get("measurements")) or []
        for path, value in to_cell_labels(confirmed.get("data") or {}, tpl, ocr_rows):
            rows.append((f"{pid}.jpg", path, value))

    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["photo", "path", "value"])
        # 註解放在欄位名之後：bench.load_labels 會因為 path 欄為空而略過這一列
        w.writerow([f"# {LABELS_NOTE}", "", ""])
        w.writerows(rows)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="試用留底的欄位層級準確率報表")
    ap.add_argument("pilot_dir", type=Path)
    ap.add_argument("--json", type=Path, default=None, help="另存 JSON 結果")
    ap.add_argument("--export-labels", type=Path, default=None, help="把確認值反推成 bench 用的格子層級 labels.csv")
    args = ap.parse_args(argv)

    if not args.pilot_dir.is_dir():
        print(f"找不到留底目錄：{args.pilot_dir}")
        return 2

    res = scan(args.pilot_dir)
    print_report(res)

    if args.export_labels:
        from .template import load_template

        n = export_labels(args.pilot_dir, args.export_labels, load_template())
        print(f"已寫出 {n} 列格子層級 labels → {args.export_labels}")
        print(f"（{LABELS_NOTE}）")

    if args.json:
        payload = {k: v for k, v in res.items() if k not in ("reports", "silent")}
        payload["silent"] = [
            {
                "id": f.pilot_id,
                "field": f.field,
                "ocr": f.ocr,
                "confirmed": f.confirmed,
                "category": f.category,
                "reason": f.reason,
            }
            for f in res["silent"]
        ]
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return 1 if res["silentErrors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
