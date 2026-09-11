# -*- coding: utf-8 -*-
"""逐格辨識結果 → API 契約 JSON（docs/ARCHITECTURE-v2.md）。

設計原則只有一條：**沉默錯誤是唯一不可接受的失敗**。
所以只要有一點說不準（信心不足、認不出、格子留空的位置怪、數值超出生理範圍），
一律輸出 null 並把路徑寫進 uncertain，讓核對卡標黃給人看，絕不猜。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .template import CHECK, Template

DEFAULT_THRESHOLD = 0.85

# 生理合理範圍：超出就是誤讀，不是真的資料
HEIGHT_RANGE = (20.0, 250.0)
WEIGHT_RANGE = (1.0, 200.0)
PARENT_HEIGHT_RANGE = (100, 230)

FIELD_ZH = {
    "seq": "病歷號",
    "gender": "性別",
    "birthDate": "出生日期",
    "fatherHeight": "父親身高",
    "motherHeight": "母親身高",
    "measureDate": "日期",
    "height": "身高",
    "weight": "體重",
}


@dataclass
class CellResult:
    """一格的最終狀態。preprinted 格不經辨識，char 直接用預印字。"""

    path: str
    char: str | None
    conf: float
    ink: float
    blank: bool
    kind: str = "digit"
    preprinted: str | None = None

    def as_debug(self) -> dict:
        return {"path": self.path, "char": self.char, "conf": round(float(self.conf), 3)}


# --- 群組讀取 ---------------------------------------------------

EMPTY, OK, RAGGED, BAD = "empty", "ok", "ragged", "bad"


def _group_empty(cells: list[CellResult]) -> bool:
    """預印格不算「有寫」。"""
    real = [c for c in cells if c.preprinted is None]
    return all(c.blank for c in real) if real else all(c.blank for c in cells)


def read_group(cells: list[CellResult], threshold: float) -> tuple[str, str]:
    """讀一組數字格 → (文字, 狀態)。

    留白位置很重要：開頭留白＝沒用到的位數（正常），中間留白＝讀法有歧義（標黃），
    尾端留白＝可能少寫一位（給值但標黃）。
    """
    if _group_empty(cells):
        return "", EMPTY
    chars: list[str] = []
    bad = False
    seen = False
    gap_after_digit = False
    interior_gap = False
    for c in cells:
        if c.preprinted is not None:
            seen = True
            chars.append(c.preprinted)
            continue
        if c.blank:
            if seen:
                gap_after_digit = True
            continue
        if gap_after_digit:
            interior_gap = True
        seen = True
        if c.char is None or c.conf < threshold:
            bad = True
        else:
            chars.append(c.char)
    text = "".join(chars)
    if bad:
        return text, BAD
    if interior_gap:
        return text, BAD
    if gap_after_digit:
        return text, RAGGED
    return text, OK


def _strip_zeros(text: str) -> str:
    s = text.lstrip("0")
    return s if s else "0"


# --- 主流程 -----------------------------------------------------


class _Out:
    def __init__(self) -> None:
        self.uncertain: list[str] = []
        self.notes: list[str] = []

    def flag(self, path: str, note: str | None = None) -> None:
        if path not in self.uncertain:
            self.uncertain.append(path)
        if note and note not in self.notes:
            self.notes.append(note)


def _row_label(tpl: Template, ri: int) -> str:
    label = tpl.row_labels[ri] if ri < len(tpl.row_labels) else str(ri + 1)
    return "今日量測列" if label == "today" else f"第 {label} 列"


def _valid_date(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def parse_cells(
    results: list[CellResult],
    tpl: Template,
    today: str,
    threshold: float = DEFAULT_THRESHOLD,
    extra_notes: list[str] | None = None,
) -> dict:
    by_path = {r.path: r for r in results}

    def grp(key: str) -> list[CellResult]:
        return [by_path[c.path] for c in tpl.group(key) if c.path in by_path]

    out = _Out()
    for n in extra_notes or []:
        if n not in out.notes:
            out.notes.append(n)

    # 病歷號：HIS 的病歷號，**前導零有意義**，一律原樣輸出字串（"0012345" 不可變成 "12345"）。
    # 靠右填寫，所以前面留白＝沒用到的位數（正常）、尾端留白＝可能漏填一位（給值但標黃）。
    seq_text, seq_state = read_group(grp("seq"), threshold)
    if seq_state == EMPTY:
        seq = None  # 櫃檯還沒填，空白很正常，不標黃
    elif seq_state in (OK, RAGGED) and seq_text:
        seq = seq_text
        if seq_state == RAGGED:
            out.flag("seq", "病歷號尾端有格子留空，請確認")
    else:
        seq = None
        out.flag("seq", "病歷號無法確認（看不清楚或中間留空）")

    # 性別：恰一個框有墨水才算
    male = by_path.get("gender.male")
    female = by_path.get("gender.female")
    male_on = bool(male and not male.blank)
    female_on = bool(female and not female.blank)
    if male_on != female_on:
        gender = "male" if male_on else "female"
    else:
        gender = None
        out.flag("gender", "性別兩個框都打了" if male_on else "性別沒有勾選")

    birth_date, birth_ragged = _parse_date(grp("birth.y"), grp("birth.m"), grp("birth.d"), threshold)
    if birth_date is None:
        out.flag("birthDate", "出生日期無法確認")
    elif birth_ragged:
        out.flag("birthDate", "出生日期的月／日有格子留空，請確認")

    father = _parse_parent_height(grp("father"), threshold, out, "fatherHeight")
    mother = _parse_parent_height(grp("mother"), threshold, out, "motherHeight")

    measurements: list[dict] = []
    for ri, _label in enumerate(tpl.row_labels):
        y = grp(f"rows[{ri}].date.y")
        m = grp(f"rows[{ri}].date.m")
        d = grp(f"rows[{ri}].date.d")
        h_int, h_dec = grp(f"rows[{ri}].height.int"), grp(f"rows[{ri}].height.dec")
        w_int, w_dec = grp(f"rows[{ri}].weight.int"), grp(f"rows[{ri}].weight.dec")
        if all(_group_empty(g) for g in (y, m, d, h_int, h_dec, w_int, w_dec)):
            continue  # 整列空白就略過

        idx = len(measurements)
        label = _row_label(tpl, ri)
        is_today_row = tpl.is_today_row(ri)

        height = _parse_decimal(h_int, h_dec, threshold, HEIGHT_RANGE, out, f"measurements[{idx}].height", label, "身高")
        weight = _parse_decimal(w_int, w_dec, threshold, WEIGHT_RANGE, out, f"measurements[{idx}].weight", label, "體重")

        date_empty = _group_empty(y) and _group_empty(m) and _group_empty(d)
        if date_empty and is_today_row and (height is not None or weight is not None):
            # 今日量測列常常只填身高體重，日期由伺服器補；仍要標黃讓人確認
            measure_date = today
            out.flag(f"measurements[{idx}].measureDate", "今日量測列的日期由系統代填，請確認")
        else:
            measure_date, date_ragged = _parse_date(y, m, d, threshold)
            if measure_date is None:
                out.flag(f"measurements[{idx}].measureDate", f"{label}日期無法確認")
            elif date_ragged:
                out.flag(f"measurements[{idx}].measureDate", f"{label}日期有格子留空，請確認")

        measurements.append({"measureDate": measure_date, "height": height, "weight": weight})

    notes = "；".join(out.notes[:4])
    if len(out.notes) > 4:
        notes += f"；等共 {len(out.notes)} 項需確認"
    return {
        "seq": seq,
        "gender": gender,
        "birthDate": birth_date,
        "fatherHeight": father,
        "motherHeight": mother,
        "measurements": measurements,
        "uncertain": out.uncertain,
        "notes": notes,
    }


def _parse_date(
    y: list[CellResult], m: list[CellResult], d: list[CellResult], threshold: float
) -> tuple[str | None, bool]:
    """民國年 → 西元 YYYY-MM-DD。回傳 (日期, 是否有留空需確認)；不確定就回 (None, False)。"""
    yt, ys = read_group(y, threshold)
    mt, ms = read_group(m, threshold)
    dt, ds = read_group(d, threshold)
    if ys != OK or ms not in (OK, RAGGED) or ds not in (OK, RAGGED):
        return None, False
    if not yt or not mt or not dt:
        return None, False
    try:
        roc = int(yt)
        month = int(mt)
        day = int(dt)
    except ValueError:
        return None, False
    if not (1 <= roc <= 200):
        return None, False
    iso = _valid_date(roc + 1911, month, day)
    # 月／日只寫一格（例如「3_」）在語意上分不出 3 還是 30，給值但要人確認
    return iso, iso is not None and (ms == RAGGED or ds == RAGGED)


def _parse_parent_height(cells: list[CellResult], threshold: float, out: _Out, path: str) -> int | None:
    text, state = read_group(cells, threshold)
    if state == EMPTY:
        return None  # 選填欄位空白不標黃，否則每張都是黃的、護理師會失去警覺
    if state == BAD or not text:
        out.flag(path, f"{FIELD_ZH[path]}看不清楚")
        return None
    value = int(_strip_zeros(text))
    if not (PARENT_HEIGHT_RANGE[0] <= value <= PARENT_HEIGHT_RANGE[1]):
        out.flag(path, f"{FIELD_ZH[path]}數值不合理（讀到 {value}）")
        return None
    if state == RAGGED:
        out.flag(path, f"{FIELD_ZH[path]}位數可能少寫")
    return value


def _parse_decimal(
    int_cells: list[CellResult],
    dec_cells: list[CellResult],
    threshold: float,
    valid: tuple[float, float],
    out: _Out,
    path: str,
    label: str,
    zh: str,
) -> float | None:
    it, istate = read_group(int_cells, threshold)
    dt, dstate = read_group(dec_cells, threshold)

    if istate == EMPTY and dstate == EMPTY:
        out.flag(path, f"{label}{zh}空白")
        return None
    if istate == BAD or dstate == BAD:
        out.flag(path, f"{label}{zh}信心不足")
        return None
    if istate == EMPTY:
        out.flag(path, f"{label}{zh}只寫了小數位")
        return None

    ragged = istate == RAGGED
    if dstate == EMPTY:
        # 小數格留白＝整數值（第一張實拍 8 個數值有 6 個這樣寫）。空白格不可能被誤讀成數字，
        # 而 0.5 公分／公斤的差距在臨床上沒有意義，所以不標黃；整數位留白位置怪才標。
        dt = "0"
    value = float(f"{int(_strip_zeros(it))}.{dt}")
    if not (valid[0] <= value <= valid[1]):
        out.flag(path, f"{label}{zh}數值不合理（讀到 {value}）")
        return None
    if ragged:
        out.flag(path, f"{label}{zh}有格子留空，請確認")
    return value


def build_cell_results(cell_images, recognized: dict[str, tuple[str | None, float]]) -> list[CellResult]:
    """把 cells.extract_cells 的輸出與辨識結果合併成 parse 需要的 CellResult。"""
    out = []
    for ci in cell_images:
        cell = ci.cell
        if cell.kind == CHECK:
            out.append(
                CellResult(path=cell.path, char=None, conf=1.0, ink=ci.ink, blank=ci.blank, kind=CHECK)
            )
            continue
        if cell.preprinted is not None:
            out.append(
                CellResult(
                    path=cell.path,
                    char=cell.preprinted,
                    conf=1.0,
                    ink=ci.ink,
                    blank=False,
                    preprinted=cell.preprinted,
                )
            )
            continue
        if ci.blank:
            out.append(CellResult(path=cell.path, char=None, conf=0.0, ink=ci.ink, blank=True))
            continue
        char, conf = recognized.get(cell.path, (None, 0.0))
        out.append(CellResult(path=cell.path, char=char, conf=conf, ink=ci.ink, blank=False))
    return out
