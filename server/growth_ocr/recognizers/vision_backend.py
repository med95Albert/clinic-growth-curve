# -*- coding: utf-8 -*-
"""Google Cloud Vision REST 後端（準確度基準／備援）。

計費以「單位」計，**整張校正影像只送一次**（171 格分開送＝171 單位，一個月免費額度就爆了）。
回來的 symbol 依中心點對回格子；同一格落進多個數字視為看不懂（寧可標黃，不要猜）。

沒有 GOOGLE_VISION_API_KEY 時 import 仍要成功（api.py 會列出所有後端），只有真的呼叫才報錯。
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

import cv2
import numpy as np

from .base import Recognizer, normalize_char

ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"


class VisionUnavailableError(RuntimeError):
    pass


def map_symbols_to_cells(
    symbols: list[dict], boxes: list[tuple[str, int, int, int, int]]
) -> dict[str, tuple[str | None, float]]:
    """把 Vision 回傳的 symbol（含 boundingBox 頂點）依中心點分配到格子。

    純函式，方便用假回應做離線測試。symbol dict 需有 'text' 與 'vertices'（[(x,y)…]）。
    """
    buckets: dict[str, list[tuple[str, float]]] = {path: [] for path, *_ in boxes}
    for sym in symbols:
        verts = sym.get("vertices") or []
        if not verts:
            continue
        cx = sum(v[0] for v in verts) / len(verts)
        cy = sum(v[1] for v in verts) / len(verts)
        text = (sym.get("text") or "").strip()
        if not text:
            continue
        for path, x0, y0, x1, y1 in boxes:
            if x0 <= cx < x1 and y0 <= cy < y1:
                buckets[path].append((text, float(sym.get("confidence", 0.0))))
                break

    out: dict[str, tuple[str | None, float]] = {}
    for path, items in buckets.items():
        if not items:
            out[path] = (None, 0.0)
            continue
        if len(items) > 1:
            # 一格出現多個字：可能是塗改或切格偏移，交給人核對
            out[path] = (None, 0.0)
            continue
        char = normalize_char(items[0][0])
        out[path] = (char, items[0][1] if char else 0.0)
    return out


def extract_symbols(response: dict) -> list[dict]:
    """從 images:annotate 回應攤平出 symbol 清單。"""
    out: list[dict] = []
    responses = response.get("responses") or []
    if not responses:
        return out
    first = responses[0]
    if "error" in first and first["error"]:
        raise VisionUnavailableError(f"Google Vision 回報錯誤：{first['error'].get('message')}")
    fta = first.get("fullTextAnnotation") or {}
    for page in fta.get("pages", []):
        for block in page.get("blocks", []):
            for para in block.get("paragraphs", []):
                for word in para.get("words", []):
                    for sym in word.get("symbols", []):
                        verts = [
                            (v.get("x", 0), v.get("y", 0))
                            for v in (sym.get("boundingBox", {}).get("vertices") or [])
                        ]
                        out.append(
                            {
                                "text": sym.get("text", ""),
                                "confidence": sym.get("confidence", 0.0),
                                "vertices": verts,
                            }
                        )
    return out


class VisionRecognizer(Recognizer):
    name = "google-vision"
    page_level = True

    def __init__(self, api_key: str | None = None, timeout: float = 30.0, jpeg_quality: int = 92):
        self._api_key = api_key
        self.timeout = timeout
        self.jpeg_quality = jpeg_quality

    @property
    def api_key(self) -> str:
        key = self._api_key or os.environ.get("GOOGLE_VISION_API_KEY")
        if not key:
            raise VisionUnavailableError(
                "未設定 GOOGLE_VISION_API_KEY，無法使用 google-vision 後端（改用 OCR_BACKEND=rapidocr）"
            )
        return key

    def recognize(self, cells: list[np.ndarray]) -> list[tuple[str | None, float]]:
        raise VisionUnavailableError(
            "google-vision 只支援整頁辨識（逐格送會把免費額度用光），請走 recognize_page"
        )

    # --- 網路 ---------------------------------------------------
    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{ENDPOINT}?key={self.api_key}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # pragma: no cover - 需要真金鑰
            raise VisionUnavailableError(
                f"Google Vision HTTP {exc.code}：{exc.read().decode('utf-8', 'replace')[:200]}"
            ) from exc
        except urllib.error.URLError as exc:  # pragma: no cover
            raise VisionUnavailableError(f"連不上 Google Vision：{exc.reason}") from exc

    def annotate(self, corrected: np.ndarray) -> dict:
        ok, buf = cv2.imencode(".jpg", corrected, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
        if not ok:
            raise VisionUnavailableError("校正影像編碼失敗")
        payload = {
            "requests": [
                {
                    "image": {"content": base64.b64encode(buf.tobytes()).decode("ascii")},
                    "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                    "imageContext": {"languageHints": ["en"]},
                }
            ]
        }
        return self._post(payload)

    def recognize_page(
        self, corrected: np.ndarray, boxes: list[tuple[str, int, int, int, int]]
    ) -> list[tuple[str | None, float]]:
        response = self.annotate(corrected)
        mapping = map_symbols_to_cells(extract_symbols(response), boxes)
        return [mapping.get(path, (None, 0.0)) for path, *_ in boxes]
