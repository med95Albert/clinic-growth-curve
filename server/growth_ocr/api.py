# -*- coding: utf-8 -*-
"""FastAPI 伺服器：回應格式完全照 docs/ARCHITECTURE-v2.md 的契約，前端零改動。

安全模型：設了 CLINIC_KEY 就驗 x-clinic-key，沒設就是「診所區網內不驗證」。
（v1 的 Vercel 版沒設 key 一律拒絕，是因為那是公開網址；本版預設跑在區網。）
"""
from __future__ import annotations

import base64
import hmac
import json
import logging
import re
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, load_settings
from .geometry import GeometryError
from .pipeline import run_image
from .recognizers.base import Recognizer, get_recognizer
from .template import load_template

log = logging.getLogger("growth_ocr")

DATA_URL_RE = re.compile(r"^data:(image/(?:jpeg|jpg|png|webp));base64,(.+)$", re.DOTALL)
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 試用留底 id：只有這些字元才會被當成檔名，擋掉 . / \ 等路徑跳脫字元。
# 用 fullmatch 比對（$ 會放行結尾的換行，換行進檔名一樣危險）
PILOT_ID_RE = re.compile(r"[A-Za-z0-9T\-]{1,64}")


class _State:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.template = load_template()
        self._recognizer: Recognizer | None = None

    @property
    def recognizer(self) -> Recognizer:
        if self._recognizer is None:
            self._recognizer = get_recognizer(self.settings.backend)
        return self._recognizer


def _authorized(request: Request, settings: Settings) -> bool:
    if settings.lan_mode:
        return True
    got = request.headers.get("x-clinic-key") or ""
    return hmac.compare_digest(got, settings.clinic_key or "")


def _decode_data_url(image: str, max_bytes: int) -> bytes:
    m = DATA_URL_RE.match(image.strip())
    if not m:
        raise ValueError("影像格式不支援（僅接受 jpeg / png / webp 的 data URL）")
    try:
        raw = base64.b64decode(m.group(2), validate=False)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("影像 base64 解碼失敗，請重新拍攝") from exc
    if len(raw) > max_bytes:
        raise ValueError("影像過大，請重新壓縮後上傳")
    return raw


def _today(value) -> str:
    if isinstance(value, str) and ISO_DATE_RE.match(value):
        return value
    return date.today().isoformat()


# --- 試用留底（PILOT_DIR）-------------------------------------------------
# 試用期間把「照片 → 機器輸出 → 護理師確認值」三件事存成同一個 id，
# 試用本身就變成 benchmark 資料，不必再人工標註（報表見 growth_ocr/pilot_report.py）。
# 寫檔一律包 try/except：留底失敗絕不能讓護理師的判讀失敗。


def _pilot_id() -> str:
    """UTC 時間戳＋4 位隨機十六進位。同一秒內多張也不會撞名，且檔名安全、可排序。"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(2)}"


def _pilot_write(pilot_dir: Path, name: str, payload: bytes) -> bool:
    try:
        pilot_dir.mkdir(parents=True, exist_ok=True)
        (pilot_dir / name).write_bytes(payload)
        return True
    except Exception as exc:  # noqa: BLE001 - 留底是附加價值，不能影響主流程
        log.warning("試用留底寫檔失敗（%s）：%s", name, exc)
        return False


def _pilot_json(pilot_dir: Path, name: str, obj: dict) -> bool:
    try:
        blob = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    except Exception as exc:  # noqa: BLE001
        log.warning("試用留底序列化失敗（%s）：%s", name, exc)
        return False
    return _pilot_write(pilot_dir, name, blob)


def _pilot_store(pilot_dir: Path, pilot_id: str, raw: bytes, suffix: str, record: dict) -> None:
    """存照片＋一份 JSON。外層再包一次 try/except：留底不能有任何路徑弄垮判讀回應。"""
    try:
        # 影像存原始位元組、不重新編碼：報表回頭跑 bench 時，看到的要跟辨識器當時看到的一模一樣
        _pilot_write(pilot_dir, f"{pilot_id}.jpg", raw)
        _pilot_json(pilot_dir, f"{pilot_id}.{suffix}", record)
    except Exception as exc:  # noqa: BLE001
        log.warning("試用留底失敗（%s）：%s", pilot_id, exc)


def create_app(settings: Settings | None = None, recognizer: Recognizer | None = None) -> FastAPI:
    settings = settings or load_settings()
    state = _State(settings)
    if recognizer is not None:
        state._recognizer = recognizer

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            state.recognizer.warmup()  # 先把 ONNX session 建好，第一張照片才不會特別慢
        except Exception as exc:  # noqa: BLE001 - 後端壞掉不該讓靜態頁也開不了
            log.warning("辨識後端預熱失敗：%s", exc)
        if settings.pilot:
            log.warning("試用留底已開啟：%s（含病歷號與原始照片，僅限診所內）", settings.pilot_dir)
        yield

    app = FastAPI(title="生長曲線表單判讀", version="2.0", lifespan=lifespan)
    app.state.growth = state

    @app.get("/api/config")
    async def config(request: Request):
        if not _authorized(request, settings):
            return JSONResponse({"error": "密鑰錯誤"}, status_code=401)
        return {
            "growthToolUrl": settings.growth_tool_url,
            "queueEnabled": False,
            "backend": settings.backend,
            "authRequired": not settings.lan_mode,
            "pilot": settings.pilot,
        }

    @app.get("/api/health")
    async def health():
        return {"ok": True, "backend": settings.backend, "cells": len(state.template.cells)}

    @app.post("/api/ocr")
    async def ocr(request: Request):
        if not _authorized(request, settings):
            return JSONResponse({"error": "密鑰錯誤或未提供 x-clinic-key"}, status_code=401)

        content_type = (request.headers.get("content-type") or "").lower()
        try:
            if content_type.startswith("multipart/form-data"):
                form = await request.form()
                upload = form.get("file") or form.get("image")
                if upload is None or not hasattr(upload, "read"):
                    return JSONResponse({"error": "缺少上傳檔案（欄位名 file）"}, status_code=400)
                raw = await upload.read()
                if len(raw) > settings.max_image_bytes:
                    return JSONResponse({"error": "影像過大，請重新壓縮後上傳"}, status_code=413)
                today = _today(form.get("today"))
            else:
                body = await request.json()
                image = (body or {}).get("image")
                if not isinstance(image, str):
                    return JSONResponse({"error": "缺少 image 欄位"}, status_code=400)
                raw = _decode_data_url(image, settings.max_image_bytes)
                today = _today((body or {}).get("today"))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"請求解析失敗：{exc}"}, status_code=400)

        from starlette.concurrency import run_in_threadpool

        pilot_id = _pilot_id() if settings.pilot else None

        try:
            outcome = await run_in_threadpool(
                run_image,
                raw,
                state.template,
                state.recognizer,
                today,
                settings.threshold,
                # 試用留底需要完整 debug（quality＋cells）才算得出欄位層級報表；
                # 回應要不要帶 debug 仍由 OCR_DEBUG 決定，契約不變。
                settings.include_debug or settings.pilot,
            )
        except GeometryError as exc:
            body: dict = {"error": str(exc)}
            if pilot_id:
                # 幾何失敗也留底：重拍指引是否有用、失敗率多高，都要從真的失敗照片回頭看
                _pilot_store(
                    settings.pilot_dir, pilot_id, raw, "error.json", {"id": pilot_id, "error": str(exc)}
                )
                body["id"] = pilot_id
            return JSONResponse(body, status_code=400)
        except NotImplementedError as exc:
            return JSONResponse({"error": str(exc)}, status_code=501)
        except Exception as exc:  # noqa: BLE001
            log.exception("判讀失敗")
            return JSONResponse({"error": f"判讀失敗：{exc}"}, status_code=500)

        payload = {"data": outcome.data, "backend": outcome.backend, "ms": outcome.ms}
        if settings.include_debug:
            payload["debug"] = outcome.debug
        if pilot_id:
            _pilot_store(
                settings.pilot_dir,
                pilot_id,
                raw,
                "ocr.json",
                {
                    "id": pilot_id,
                    "today": today,
                    "data": outcome.data,
                    "backend": outcome.backend,
                    "ms": outcome.ms,
                    "debug": outcome.debug,
                },
            )
            payload["id"] = pilot_id
        return payload

    @app.post("/api/confirm")
    async def confirm(request: Request):
        """核對卡確認後的最終值。前端 fire-and-forget 呼叫，回應內容不影響護理師流程。"""
        if not _authorized(request, settings):
            return JSONResponse({"error": "密鑰錯誤或未提供 x-clinic-key"}, status_code=401)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "請求不是合法的 JSON"}, status_code=400)

        pilot_id = (body or {}).get("id")
        data = (body or {}).get("data")
        if not isinstance(pilot_id, str) or not PILOT_ID_RE.fullmatch(pilot_id):
            return JSONResponse({"error": "id 格式不正確"}, status_code=400)
        if not isinstance(data, dict):
            return JSONResponse({"error": "缺少 data 物件"}, status_code=400)
        if not settings.pilot:
            return {"stored": False}  # 沒開試用模式不是錯誤，前端照樣可以送

        ok = _pilot_json(
            settings.pilot_dir,
            f"{pilot_id}.confirmed.json",
            {"id": pilot_id, "data": data},
        )
        return {"stored": ok}

    if settings.static_dir:
        # 掛在最後：/api/* 先被上面的路由吃掉，其餘交給 pwa/public（含 tool.html、room.html）
        app.mount("/", StaticFiles(directory=str(settings.static_dir), html=True), name="static")
    return app


app = None  # 由 run.py 或 uvicorn factory 建立


def get_app() -> FastAPI:
    return create_app()
