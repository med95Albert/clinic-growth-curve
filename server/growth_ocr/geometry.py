# -*- coding: utf-8 -*-
"""照片 → 四角定位 → 方向判斷 → 透視校正成 8 px/mm 的標準灰階影像。

失敗一律丟 GeometryError，訊息寫成「護理師看得懂、知道怎麼重拍」的句子。
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import cv2
import numpy as np

from .template import PX_PER_MM, Template


class GeometryError(ValueError):
    """幾何校正失敗；message 直接回給前端當重拍指引。"""


@dataclass
class Quality:
    rotation: int = 0  # 照片相對表單的旋轉角（0/90/180/270），已在輸出影像中校正
    brightness: float = 0.0  # 原圖平均亮度 0–255
    sharpness: float = 0.0  # Laplacian 變異數，越小越糊
    marker_area_cv: float = 0.0  # 四塊定位塊面積的變異係數，越大代表透視越歪
    sheet_coverage: float = 0.0  # 定位塊四邊形佔畫面比例，太小代表拍太遠
    dot_score: float = 0.0  # 方向點的對比分數（判方向的信心來源）
    grid_score: float = 0.0  # 校正後格線與版面的吻合度
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "rotation": self.rotation,
            "brightness": round(self.brightness, 1),
            "sharpness": round(self.sharpness, 1),
            "markerAreaCv": round(self.marker_area_cv, 3),
            "sheetCoverage": round(self.sheet_coverage, 3),
            "dotScore": round(self.dot_score, 3),
            "gridScore": round(self.grid_score, 3),
            "warnings": list(self.warnings),
        }


CORNER_NAMES_ZH = {"tl": "左上", "tr": "右上", "br": "右下", "bl": "左下"}

# 校正後「格線位置真的有線」的最低比例。實測正確校正 0.44–0.73，錯誤校正 ≈ 0。
GRID_MIN = 0.18
GRID_WARN = 0.30


def decode_image(data: bytes) -> np.ndarray:
    """bytes → BGR ndarray；順手做 EXIF 方向校正（手機直拍常帶 EXIF）。"""
    try:
        from PIL import Image, ImageOps

        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        arr = np.array(img.convert("RGB"))
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    except Exception as exc:  # noqa: BLE001 - 交給呼叫端變成 400
        raise GeometryError(f"影像無法解碼（{exc}），請重新拍攝或改用 JPEG／PNG。") from exc


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _binarize(gray: np.ndarray) -> np.ndarray:
    """回傳「墨水=255」的二值圖。先 Otsu，太暗或光照不均時退回自適應。"""
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ink_ratio = float(np.count_nonzero(otsu)) / otsu.size
    if 0.002 < ink_ratio < 0.5:
        return otsu
    block = max(31, (min(gray.shape[:2]) // 20) | 1)
    return cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block, 12
    )


def _square_candidates(binary: np.ndarray) -> list[tuple[np.ndarray, float, tuple[float, float]]]:
    """找出近似實心正方形的輪廓，回傳 (contour, area, center)。

    關鍵是「實心」：表單上的空心格線框（7.5×10mm）外框面積與 7mm 定位塊接近，
    findContours 只描外緣、contourArea 會把空心框算成整塊，所以必須另外量內部填滿度。
    """
    h, w = binary.shape[:2]
    img_area = float(h * w)
    # 先做「開運算」把細筆畫洗掉：真實照片裡定位塊常跟旁邊的標題／頁尾文字黏成一團
    # （2026-09-11 第一張實拍就是這樣掛的）。定位塊是實心方塊，經侵蝕再膨脹會原樣回來；
    # 文字筆畫（粗約 1mm）只要比核心小就消失。核心取短邊 0.7%：3024px 寬約 21px、1200px 約 8px，
    # 都小於定位塊（短邊 2.8%）而大於一般筆畫。
    k = max(3, int(round(min(h, w) * 0.007)))
    opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    contours, _ = cv2.findContours(opened, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in contours:
        area = cv2.contourArea(c)
        # 7mm 方塊在合理拍攝距離下佔畫面 0.005%–5%；放寬一點以免離太遠就找不到
        if area < img_area * 2e-5 or area > img_area * 0.05:
            continue
        peri = cv2.arcLength(c, True)
        if peri <= 0:
            continue
        approx = cv2.approxPolyDP(c, 0.05 * peri, True)
        if not (4 <= len(approx) <= 6):  # 低解析度＋模糊會把角磨圓，多給兩個頂點的餘裕
            continue
        (_, _), (rw, rh), _ = cv2.minAreaRect(c)
        if rw < 4 or rh < 4:
            continue
        ratio = rw / rh if rh else 0
        # 定位塊是正方形（1.0），數字格改版後是 7.5×10mm ＝ 0.750／1.333，**剛好貼在這個窗的兩端**
        # （舊的 11mm 版是 0.68／1.47，還有餘裕）。所以長寬比已經不再是可靠的區分，
        # 真正擋住數字格的是下面的「實心度」與內部填滿度檢查——那兩關不可以放寬。
        # 這個範圍同樣不能為了「多找到一點」而放寬，否則數字格會開始混進來。
        if not (0.75 < ratio < 1.33):
            continue
        if area / (rw * rh) < 0.78:
            continue
        bx, by, bw, bh = cv2.boundingRect(c)
        ix0, iy0 = bx + int(bw * 0.25), by + int(bh * 0.25)
        ix1, iy1 = bx + int(bw * 0.75), by + int(bh * 0.75)
        inner = binary[iy0:iy1, ix0:ix1]
        if inner.size == 0 or np.count_nonzero(inner) / inner.size < 0.85:
            continue
        m = cv2.moments(c)
        if m["m00"] == 0:
            continue
        out.append(
            (approx.reshape(-1, 2).astype(np.float32), area, (m["m10"] / m["m00"], m["m01"] / m["m00"]))
        )
    return out


def _order_quad(points: list[tuple[float, float]]) -> list[tuple[float, float]] | None:
    """四點排成順時針（從畫面最左上者起算）。退化（同一點被選兩次）回 None。"""
    pts = np.array(points, dtype=np.float64)
    s = pts[:, 0] + pts[:, 1]
    d = pts[:, 0] - pts[:, 1]
    idx = [int(np.argmin(s)), int(np.argmax(d)), int(np.argmax(s)), int(np.argmin(d))]
    if len(set(idx)) != 4:
        return None
    return [tuple(map(float, pts[i])) for i in idx]


def _quad_shape_ok(quad: list[tuple[float, float]], aspect: float) -> float | None:
    """檢查四邊形是否像「193×280mm 的定位塊框」，回傳邊長比（不像則 None）。

    這一關是防呆重點：少偵測到一個角落方塊時，若只取極端點會拿版面裡的某個方框頂替，
    結果是「幾何看起來成功、切格整片偏掉」——那正是最危險的沉默錯誤。
    """
    (a, b, c, d) = quad
    top, right, bottom, left = (
        float(np.hypot(*(np.subtract(b, a)))),
        float(np.hypot(*(np.subtract(c, b)))),
        float(np.hypot(*(np.subtract(d, c)))),
        float(np.hypot(*(np.subtract(a, d)))),
    )
    if min(top, right, bottom, left) < 20:
        return None
    if min(top, bottom) / max(top, bottom) < 0.6 or min(left, right) / max(left, right) < 0.6:
        return None
    ratio = (top + bottom) / (left + right)
    upright = aspect * 0.78 < ratio < aspect * 1.28
    sideways = (1 / aspect) * 0.78 < ratio < (1 / aspect) * 1.28
    if not (upright or sideways):
        return None
    return ratio


def _pick_four(
    cands: list[tuple[np.ndarray, float, tuple[float, float]]], shape, tpl: Template
) -> tuple[list[tuple[float, float]], float]:
    """挑出面積一致、形狀符合表單比例的四塊，回傳 (順時針中心點, 邊長比)。"""
    if len(cands) < 4:
        raise GeometryError(_missing_corner_message(cands, shape))

    centers = tpl.registration_centers()
    aspect = abs(centers[1][0] - centers[0][0]) / abs(centers[3][1] - centers[0][1])  # 193/280

    # 只在「四個方向各自最外圍的候選」裡窮舉：定位塊必定是候選點雲的四個極端點之一。
    # 不用面積分群當篩選條件，因為版面裡的方框在低解析度下面積會跟定位塊重疊。
    pts = np.array([c[2] for c in cands], dtype=np.float64)
    s, d = pts[:, 0] + pts[:, 1], pts[:, 0] - pts[:, 1]
    k = 6
    corner_sets = [
        list(np.argsort(s)[:k]),  # 左上
        list(np.argsort(-d)[:k]),  # 右上
        list(np.argsort(-s)[:k]),  # 右下
        list(np.argsort(d)[:k]),  # 左下
    ]

    import itertools

    best: tuple[float, list[tuple[float, float]], float] | None = None
    seen: set[tuple[int, ...]] = set()
    for combo in itertools.product(*corner_sets):
        key = tuple(sorted(int(i) for i in combo))
        if len(set(key)) != 4 or key in seen:
            continue
        seen.add(key)
        quad = _order_quad([cands[i][2] for i in key])
        if quad is None:
            continue
        ratio = _quad_shape_ok(quad, aspect)
        if ratio is None:
            continue
        areas = [cands[i][1] for i in key]
        if max(areas) / max(min(areas), 1e-6) > 2.2:
            continue
        area = float(cv2.contourArea(np.array(quad, dtype=np.float32)))
        if best is None or area > best[0]:
            best = (area, quad, ratio)

    if best is None:
        raise GeometryError(_missing_corner_message(cands, shape))

    quad_area, quad, ratio = best
    h, w = shape[:2]
    if quad_area < 0.05 * h * w:
        raise GeometryError("表單在畫面中太小，請靠近一點重拍，讓表單填滿畫面。")
    return quad, ratio


def _missing_corner_message(cands, shape) -> str:
    h, w = shape[:2]
    if len(cands) == 0:
        return "找不到四角的黑色定位塊，請確認整張表單都入鏡、紙張攤平、不要反光。"
    found = set()
    for _, _, (cx, cy) in cands:
        found.add(("t" if cy < h / 2 else "b") + ("l" if cx < w / 2 else "r"))
    missing = [k for k in ("tl", "tr", "br", "bl") if k not in found]
    if not missing:
        return "找不到成組的四角定位塊（角落可能被手指或紙張遮住），請確認四個黑方塊都完整入鏡後重拍。"
    zh = "、".join(CORNER_NAMES_ZH[k] for k in missing)
    return f"照片{zh}角的定位塊未入鏡，請把整張表單拍進畫面後重拍。"


def _homography(quad: list[tuple[float, float]], tpl: Template, rot: int) -> np.ndarray:
    """rot 為把照片四角旋轉對齊表單四角的位移量（0/1/2/3 → 0/90/180/270 度）。"""
    src = np.array([quad[(i + rot) % 4] for i in range(4)], dtype=np.float32)
    dst = np.array(tpl.registration_centers(), dtype=np.float32) * PX_PER_MM
    return cv2.getPerspectiveTransform(src, dst)


def _dot_score(gray: np.ndarray, H: np.ndarray, tpl: Template) -> float:
    """方向點是否在該出現的地方：回傳「比周圍紙色暗多少」的比例 0–1。

    用區域平均對比而不是二值墨水比例——手機壓縮後這顆點只有十幾個像素，
    模糊一來墨水比例會掉到門檻以下，平均對比則還撐得住。
    """
    dot = tpl.orientation_dot()
    cx = dot["x"] + dot["w"] / 2
    cy = dot["y"] + dot["h"] / 2
    win_mm = 7.0
    size = int(round(win_mm * PX_PER_MM))
    x0 = (cx - win_mm / 2) * PX_PER_MM
    y0 = (cy - win_mm / 2) * PX_PER_MM
    shift = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], dtype=np.float64)
    patch = cv2.warpPerspective(gray, shift @ H, (size, size), borderValue=255)
    if patch.size == 0:
        return 0.0
    paper = float(np.percentile(patch, 85))
    if paper < 1:
        return 0.0
    r = max(2, int(round(1.1 * PX_PER_MM)))
    c = size // 2
    inner = patch[c - r : c + r, c - r : c + r]
    return float(np.clip((paper - float(inner.mean())) / paper, 0.0, 1.0))


_GRID_MASK_CACHE: dict[int, tuple[np.ndarray, np.ndarray]] = {}


def _grid_masks(tpl: Template) -> tuple[np.ndarray, np.ndarray]:
    """快取「格線環」與「格子內部」兩張遮罩，用來驗證校正結果真的貼在版面上。"""
    key = id(tpl)
    cached = _GRID_MASK_CACHE.get(key)
    if cached is not None:
        return cached
    w_px, h_px = tpl.size_px
    border = np.zeros((h_px, w_px), dtype=np.uint8)
    inner = np.zeros((h_px, w_px), dtype=np.uint8)
    for cell in tpl.cells:
        x0, y0, x1, y1 = cell.rect_px(0.0)
        cv2.rectangle(border, (x0, y0), (x1 - 1, y1 - 1), 255, thickness=5)
        ix0, iy0, ix1, iy1 = cell.rect_px(1.8)
        if ix1 > ix0 and iy1 > iy0:
            cv2.rectangle(inner, (ix0, iy0), (ix1 - 1, iy1 - 1), 255, thickness=-1)
    masks = (border > 0, inner > 0)
    _GRID_MASK_CACHE[key] = masks
    return masks


def flatten_illumination(gray: np.ndarray) -> np.ndarray:
    """把陰影、漸層光攤平：用大核閉運算估出「沒有墨水的背景亮度」，再把每個像素除以它。

    第一張實拍（2026-09-11）手影蓋住右下半頁，固定門檻在陰影裡看不到格線與筆畫；
    攤平後所有判定都變成「相對於局部背景有多暗」，與光照無關。
    核心 61px（約 7.6mm）遠大於筆畫與格線（≤1mm），墨水不會被當成背景。
    """
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (61, 61))
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, k)
    bg = cv2.GaussianBlur(bg, (0, 0), 15)
    norm = gray.astype(np.float32) / np.maximum(bg.astype(np.float32), 1.0)
    return np.clip(norm * 235.0, 0, 255).astype(np.uint8)


def grid_score(corrected: np.ndarray, tpl: Template) -> float:
    """校正影像上「格線位置真的有線」的比例。

    這是對抗沉默錯誤的最後一關：四角只要有一個被別的方塊頂替，整片切格就會偏掉，
    但四角本身仍然「完美對齊」（它們就是解 homography 的錨點），只有版面對不上會露餡。
    """
    b_mask, i_mask = _grid_masks(tpl)
    h, w = corrected.shape[:2]
    if (h, w) != b_mask.shape:
        return 0.0
    paper = float(np.percentile(corrected, 85))
    thr = max(60.0, paper * 0.75)
    border_ink = float(np.count_nonzero(corrected[b_mask] < thr)) / max(int(b_mask.sum()), 1)
    inner_ink = float(np.count_nonzero(corrected[i_mask] < thr)) / max(int(i_mask.sum()), 1)
    return border_ink - inner_ink


@dataclass
class Corrected:
    image: np.ndarray  # 8 px/mm 灰階，1680×2376
    quality: Quality
    homography: np.ndarray


def rectify(img: np.ndarray, tpl: Template) -> Corrected:
    """主入口：BGR/灰階照片 → 校正後灰階影像。"""
    gray = to_gray(img)
    brightness = float(np.mean(gray))
    if brightness < 45:
        raise GeometryError("影像太暗，請開燈或避開陰影後重拍。")
    if brightness > 245:
        raise GeometryError("影像過曝（太亮），請避開直射光或關掉閃光燈後重拍。")

    # 定位塊偵測在縮圖上做就夠，省下大照片的輪廓搜尋時間；warp 仍用原圖以保畫質。
    scale = 1.0
    det = gray
    longest = max(gray.shape[:2])
    if longest > 1600:
        scale = 1600.0 / longest
        det = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    binary = _binarize(det)
    # 先閉運算補掉方塊上的反光小洞，避免輪廓破裂
    k = max(3, (min(det.shape[:2]) // 400) | 1)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))

    cands = _square_candidates(binary)
    quad_small, ratio = _pick_four(cands, det.shape, tpl)
    quad = [(x / scale, y / scale) for x, y in quad_small]

    # 四邊形長寬比已經決定「表單是直的還是橫躺」，只剩相差 180 度的兩個候選要二選一。
    # 主判準是「格線對不對得上版面」（比 3mm 方向點可靠得多），方向點只當佐證。
    candidates = [0, 2] if ratio < 1 else [1, 3]
    w_px, h_px = tpl.size_px
    H = _homography(quad, tpl, candidates[0])
    corrected = cv2.warpPerspective(gray, H, (w_px, h_px), flags=cv2.INTER_CUBIC, borderValue=255)
    corrected = flatten_illumination(corrected)
    # 轉 180 度的校正結果＝同一張影像轉 180 度，不必再 warp 一次
    corrected_alt = cv2.rotate(corrected, cv2.ROTATE_180)
    grids = [grid_score(corrected, tpl), grid_score(corrected_alt, tpl)]
    dots = [_dot_score(gray, _homography(quad, tpl, rot), tpl) for rot in candidates]

    pick = 0 if grids[0] >= grids[1] else 1
    if grids[pick] < GRID_MIN:
        raise GeometryError(
            "校正後對不上表單版面（四角定位塊可能有一個沒拍到、被手指遮住，或被別的方塊誤認），"
            "請確認四個黑方塊都完整入鏡、紙張攤平後重拍。"
        )
    if abs(grids[0] - grids[1]) < 0.10:
        # 版面分不出正反時才回頭靠方向點
        pick = 0 if dots[0] >= dots[1] else 1
        if max(dots) < 0.30 or abs(dots[0] - dots[1]) < 0.12:
            raise GeometryError(
                "無法判斷照片方向（右上角定位塊下方的小方塊沒拍清楚），"
                "請確認四角都入鏡、避開反光後重拍。"
            )

    best = candidates[pick]
    grid = grids[pick]
    top = dots[pick]
    if pick == 1:
        corrected = corrected_alt
        H = np.array([[-1.0, 0.0, w_px], [0.0, -1.0, h_px], [0.0, 0.0, 1.0]]) @ H

    areas = []
    for _, area, center in cands:
        for qx, qy in quad_small:
            if abs(center[0] - qx) < 2 and abs(center[1] - qy) < 2:
                areas.append(area)
                break
    area_cv = float(np.std(areas) / np.mean(areas)) if len(areas) >= 2 and np.mean(areas) > 0 else 0.0
    coverage = float(cv2.contourArea(np.array(quad_small, dtype=np.float32)) / (det.shape[0] * det.shape[1]))
    sharp = float(cv2.Laplacian(det, cv2.CV_64F).var())

    q = Quality(
        # rot=1 代表照片裡的表單順時針轉了 90 度（已在輸出影像中轉正）
        rotation=best * 90,
        brightness=brightness,
        sharpness=sharp,
        marker_area_cv=area_cv,
        sheet_coverage=coverage,
        dot_score=top,
        grid_score=grid,
    )
    if sharp < 60:
        q.warnings.append("影像偏模糊，辨識可能不準，建議重拍。")
    if area_cv > 0.35:
        q.warnings.append("拍攝角度偏斜，建議正上方俯拍。")
    if coverage < 0.25:
        q.warnings.append("表單只佔畫面一小塊，靠近一點會更準。")
    if grid < GRID_WARN:
        q.warnings.append("格線對位偏弱，請確認判讀結果。")
    return Corrected(image=corrected, quality=q, homography=H)


def rectify_bytes(data: bytes, tpl: Template) -> Corrected:
    return rectify(decode_image(data), tpl)
