# -*- coding: utf-8 -*-
"""合成測試表單產生器：依 template 畫格線與定位塊、填印刷數字、再做透視變形與雜訊。

用途是把「幾何 → 切格 → 辨識 → parse」整條路自動化驗證。
預設的相機模擬參數刻意對齊 pwa/public/index.html 的壓縮設定（長邊 1600、JPEG 82），
這樣 benchmark 量到的就是護理師手機實際送上來的畫質，不會樂觀。
"""
from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .cells import CHECK_MARK
from .template import CHECK, PX_PER_MM, Template, load_template

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    raise RuntimeError("找不到可用的 TTF 字型，synth 需要印刷字型才能產生測試表單")


@dataclass
class FormValues:
    """人看得懂的表單內容；to_cells() 轉成 path→字元。"""

    seq: str | None = None  # 病歷號字串，前導零有意義（"0012345"）；'' 或 None ＝ 整排空白
    gender: str | None = None  # 'male' / 'female' / None
    birth: tuple[int, int, int] | None = None  # (民國年, 月, 日)
    father: int | None = None
    mother: int | None = None
    # 每列：(民國年, 月, 日, 身高, 體重)；None 代表整列空白
    rows: list[tuple[int, int, int, float, float] | None] = field(default_factory=list)
    today_date_blank: bool = False  # 今日量測列只填身高體重

    def to_cells(self, tpl: Template) -> dict[str, str]:
        v: dict[str, str] = {c.path: "" for c in tpl.cells if c.kind != CHECK}

        def put(group: str, text: str, pad: str = "0", skip_first: bool = False) -> None:
            """一律靠右填進該 group 的格子；寬度由 template 決定，不寫死位數。

            pad="0" 是補零欄（民國年、身高整數位），pad=" " 是留白欄（病歷號）。
            """
            slots = tpl.group(group)[1 if skip_first else 0 :]
            s = text.rjust(len(slots), pad)[-len(slots) :]
            for cell, ch in zip(slots, s):
                v[cell.path] = ch if ch.strip() else ""

        if self.seq:
            put("seq", self.seq, pad=" ")
        if self.birth:
            y, m, d = self.birth
            put("birth.y", str(y))
            put("birth.m", str(m))
            put("birth.d", str(d))
        if self.father:
            put("father", str(self.father))
        if self.mother:
            put("mother", str(self.mother))

        for ri, row in enumerate(self.rows):
            if row is None:
                continue
            y, m, d, h, w = row
            blank_date = self.today_date_blank and tpl.is_today_row(ri)
            if not blank_date:
                # 表格年欄第一格預印「1」，只填後兩位
                put(f"rows[{ri}].date.y", f"{y:03d}"[1:], skip_first=True)
                put(f"rows[{ri}].date.m", str(m))
                put(f"rows[{ri}].date.d", str(d))
            hi, hd = f"{h:05.1f}".split(".")
            wi, wd = f"{w:05.1f}".split(".")
            put(f"rows[{ri}].height.int", hi[-3:])
            put(f"rows[{ri}].height.dec", hd)
            put(f"rows[{ri}].weight.int", wi[-3:])
            put(f"rows[{ri}].weight.dec", wd)
        return v

    def to_check_labels(self, tpl: Template) -> dict[str, str]:
        """勾選框正解：有打的那個框給 CHECK_MARK，另一個給空字串。"""
        return {
            c.path: (CHECK_MARK if self.gender and c.path == f"gender.{self.gender}" else "")
            for c in tpl.check_cells
        }

    def to_expected(self, tpl: Template, today: str) -> dict:
        """同一份輸入的契約 JSON 正解，給端到端測試比對。"""
        measurements = []
        uncertain = []
        for ri, row in enumerate(self.rows):
            if row is None:
                continue
            y, m, d, h, w = row
            idx = len(measurements)
            if self.today_date_blank and tpl.is_today_row(ri):
                measure_date = today
                uncertain.append(f"measurements[{idx}].measureDate")
            else:
                measure_date = f"{y + 1911:04d}-{m:02d}-{d:02d}"
            measurements.append({"measureDate": measure_date, "height": h, "weight": w})
        return {
            # 病歷號原樣輸出，不去前導零
            "seq": (self.seq or None),
            "gender": self.gender,
            "birthDate": (
                f"{self.birth[0] + 1911:04d}-{self.birth[1]:02d}-{self.birth[2]:02d}" if self.birth else None
            ),
            "fatherHeight": self.father,
            "motherHeight": self.mother,
            "measurements": measurements,
            "uncertain": uncertain,
        }


def _mm(v: float) -> int:
    return int(round(v * PX_PER_MM))


def render(tpl: Template, values: FormValues, jitter: random.Random | None = None) -> np.ndarray:
    """畫出乾淨的 8 px/mm 表單（灰階），只包含辨識需要的元素。"""
    w_px, h_px = tpl.size_px
    img = Image.new("L", (w_px, h_px), 255)
    dr = ImageDraw.Draw(img)

    for key in ("tl", "tr", "bl", "br", "orientation_dot"):
        r = tpl.registration[key]
        dr.rectangle([_mm(r["x"]), _mm(r["y"]), _mm(r["x"] + r["w"]) - 1, _mm(r["y"] + r["h"]) - 1], fill=0)

    chars = values.to_cells(tpl)
    # 字級跟著 template 的格高走：表單把格子從 11mm 改成 10mm 時，字不會跟著撐爆格線
    cell_h_mm = tpl.digit_cell_mm[1]
    digit_font = _font(max(8, int(cell_h_mm * PX_PER_MM * 0.60)))
    pre_font = _font(max(6, int(cell_h_mm * PX_PER_MM * 0.42)))
    border = max(2, int(round(0.9 / 72 * 25.4 * PX_PER_MM)))  # 0.9pt

    for cell in tpl.cells:
        x0, y0 = _mm(cell.x), _mm(cell.y)
        x1, y1 = _mm(cell.x + cell.w) - 1, _mm(cell.y + cell.h) - 1
        if cell.kind == CHECK:
            dr.rectangle([x0, y0, x1, y1], outline=0, width=max(3, border + 1))
            if values.gender and cell.path == f"gender.{values.gender}":
                pad = _mm(1.6)
                dr.line([x0 + pad, y0 + pad, x1 - pad, y1 - pad], fill=0, width=4)
                dr.line([x1 - pad, y0 + pad, x0 + pad, y1 - pad], fill=0, width=4)
            continue

        dr.rectangle([x0, y0, x1, y1], outline=0, width=border)
        if cell.preprinted is not None:
            dr.rectangle([x0 + border, y0 + border, x1 - border, y1 - border], fill=232)
            _center_text(dr, (x0, y0, x1, y1), cell.preprinted, pre_font, fill=90)
            continue
        ch = chars.get(cell.path, "")
        if ch:
            dx = dy = 0
            if jitter is not None:
                dx = jitter.randint(-3, 3)
                dy = jitter.randint(-3, 3)
            _center_text(dr, (x0 + dx, y0 + dy, x1 + dx, y1 + dy), ch, digit_font, fill=25)

    return np.array(img)


def _center_text(dr: ImageDraw.ImageDraw, box, text: str, font, fill: int) -> None:
    x0, y0, x1, y1 = box
    bb = dr.textbbox((0, 0), text, font=font)
    tx = x0 + (x1 - x0 - (bb[2] - bb[0])) / 2 - bb[0]
    ty = y0 + (y1 - y0 - (bb[3] - bb[1])) / 2 - bb[1]
    dr.text((tx, ty), text, font=font, fill=fill)


def photograph(
    clean: np.ndarray,
    rng: random.Random,
    warp: float = 0.02,
    rotate: int = 0,
    long_edge: int = 1600,
    jpeg_quality: int = 82,
    blur: float = 0.7,
    noise: float = 3.0,
) -> bytes:
    """乾淨表單 → 模擬拍攝：留白桌面、隨機透視、模糊、雜訊、JPEG 壓縮。"""
    h, w = clean.shape[:2]
    margin = int(min(h, w) * 0.06)
    canvas = np.full((h + 2 * margin, w + 2 * margin), 215, dtype=np.uint8)  # 桌面比紙暗
    canvas[margin : margin + h, margin : margin + w] = clean
    ch, cw = canvas.shape[:2]

    def jit(x, y):
        return [x + rng.uniform(-warp, warp) * cw, y + rng.uniform(-warp, warp) * ch]

    src = np.float32([[0, 0], [cw, 0], [cw, ch], [0, ch]])
    dst = np.float32([jit(0, 0), jit(cw, 0), jit(cw, ch), jit(0, ch)])
    M = cv2.getPerspectiveTransform(src, dst)
    out = cv2.warpPerspective(canvas, M, (cw, ch), flags=cv2.INTER_LINEAR, borderValue=215)

    scale = long_edge / max(out.shape[:2])
    out = cv2.resize(out, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    if blur > 0:
        out = cv2.GaussianBlur(out, (0, 0), blur)
    # 輕微的光照不均：模擬燈管在紙上留下的亮度梯度
    yy, xx = np.mgrid[0 : out.shape[0], 0 : out.shape[1]].astype(np.float32)
    grad = 1.0 + 0.08 * ((xx / out.shape[1] - 0.5) * rng.uniform(-1, 1) + (yy / out.shape[0] - 0.5) * rng.uniform(-1, 1))
    out = np.clip(out.astype(np.float32) * grad, 0, 255)
    if noise > 0:
        out = out + np.random.default_rng(rng.randrange(1 << 30)).normal(0, noise, out.shape)
    out = np.clip(out, 0, 255).astype(np.uint8)

    if rotate:
        code = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}[rotate]
        out = cv2.rotate(out, code)

    ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
    if not ok:
        raise RuntimeError("JPEG 編碼失敗")
    return buf.tobytes()


@dataclass
class SynthForm:
    jpeg: bytes
    clean: np.ndarray
    values: FormValues
    cells: dict[str, str]  # path -> 正解字元（'' 代表空白）
    rotate: int


def random_seq(rng: random.Random, width: int) -> str:
    """病歷號：0–width 位（0 位＝整排空白）。刻意讓約 1/3 以「0」開頭，逼出前導零的案例。"""
    n = rng.randint(0, width)
    if n == 0:
        return ""
    digits = [str(rng.randint(0, 9)) for _ in range(n)]
    if rng.random() < 0.35:
        digits[0] = "0"
    return "".join(digits)


def random_values(
    rng: random.Random, tpl: Template, this_year_roc: int = 115, full: bool = False
) -> FormValues:
    """隨機但合理的表單內容：出生年在今年之前，量測年不超過今年。

    列數與病歷號位數都由 template 決定；`full=True` 是「所有列都填、病歷號填滿且以 0 開頭」
    的最壞情況，用來驗證格子最多時切格還對得上。
    """
    n_rows = tpl.row_count
    seq_width = len(tpl.group("seq"))
    roc_birth = rng.randint(this_year_roc - 18, this_year_roc - 1)
    rows: list[tuple[int, int, int, float, float] | None] = [None] * n_rows
    n_used = n_rows if full else rng.randint(1, n_rows)
    height = rng.uniform(60, 150)
    weight = rng.uniform(8, 45)
    for i in range(n_used):
        year = min(this_year_roc, roc_birth + 1 + i)
        rows[i] = (
            year,
            rng.randint(1, 12),
            rng.randint(1, 28),
            round(height + i * rng.uniform(3, 7), 1),
            round(weight + i * rng.uniform(1, 4), 1),
        )
    if full:
        seq = "0" + "".join(str(rng.randint(0, 9)) for _ in range(seq_width - 1))
    else:
        seq = random_seq(rng, seq_width)
    return FormValues(
        seq=seq,
        gender=rng.choice(["male", "female"]),
        birth=(roc_birth, rng.randint(1, 12), rng.randint(1, 28)),
        father=rng.randint(155, 190) if full else rng.choice([None, rng.randint(155, 190)]),
        mother=rng.randint(145, 178) if full else rng.choice([None, rng.randint(145, 178)]),
        rows=rows,
        today_date_blank=False if full else rng.random() < 0.3,
    )


def make_form(
    tpl: Template,
    rng: random.Random,
    values: FormValues | None = None,
    rotate: int = 0,
    **photo_kwargs,
) -> SynthForm:
    values = values or random_values(rng, tpl)
    clean = render(tpl, values, jitter=rng)
    jpeg = photograph(clean, rng, rotate=rotate, **photo_kwargs)
    return SynthForm(jpeg=jpeg, clean=clean, values=values, cells=values.to_cells(tpl), rotate=rotate)


def generate_dataset(
    out_dir: Path,
    count: int,
    seed: int,
    tpl: Template,
    rotate_some: bool = False,
    full_first: bool = True,
    **photo_kwargs,
) -> Path:
    """產生 count 張合成表單與 labels.csv（photo,path,value），可直接餵 bench。

    第一張固定是「所有列都填、病歷號填滿」的最壞情況（full_first），
    否則隨機取樣可能整批都只填三五列，切格對位最吃緊的那種表單反而測不到。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    rows = []
    for i in range(count):
        rot = rng.choice([0, 0, 0, 90, 180, 270]) if rotate_some else 0
        values = random_values(rng, tpl, full=True) if (i == 0 and full_first) else None
        form = make_form(tpl, rng, values=values, rotate=rot, **photo_kwargs)
        name = f"synth{i:03d}.jpg"
        (out_dir / name).write_bytes(form.jpeg)
        for cell in tpl.recognizable_cells:
            rows.append({"photo": name, "path": cell.path, "value": form.cells.get(cell.path, "")})
        # 勾選框也要有正解，否則 bench 測不到性別
        for path, value in form.values.to_check_labels(tpl).items():
            rows.append({"photo": name, "path": path, "value": value})
    labels = out_dir / "labels.csv"
    with labels.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["photo", "path", "value"])
        writer.writeheader()
        writer.writerows(rows)
    return labels


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="產生合成測試表單與 labels.csv")
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20260910)
    ap.add_argument("--rotate-some", action="store_true", help="混入 90/180/270 度的照片")
    ap.add_argument(
        "--no-full-first", action="store_true", help="不要固定讓第一張是「全部列都填滿」的表單"
    )
    ap.add_argument("--warp", type=float, default=0.02, help="透視變形強度（角落位移佔畫面比例）")
    ap.add_argument("--blur", type=float, default=0.7)
    ap.add_argument("--noise", type=float, default=3.0)
    ap.add_argument("--long-edge", type=int, default=1600, help="模擬手機壓縮後的長邊像素")
    ap.add_argument("--jpeg-quality", type=int, default=82)
    args = ap.parse_args(argv)
    tpl = load_template()
    labels = generate_dataset(
        args.out_dir,
        args.count,
        args.seed,
        tpl,
        args.rotate_some,
        full_first=not args.no_full_first,
        warp=args.warp,
        blur=args.blur,
        noise=args.noise,
        long_edge=args.long_edge,
        jpeg_quality=args.jpeg_quality,
    )
    print(f"已產生 {args.count} 張 → {args.out_dir}\nlabels: {labels}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
