# -*- coding: utf-8 -*-
"""環境變數設定集中處。Windows 部署時只要改環境變數，不用動程式。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .parse import DEFAULT_THRESHOLD


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass
class Settings:
    backend: str = "rapidocr"
    threshold: float = DEFAULT_THRESHOLD
    clinic_key: str | None = None
    port: int = 8790
    static_dir: Path | None = None
    growth_tool_url: str = "/tool.html"
    max_image_bytes: int = 12_000_000
    include_debug: bool = True
    pilot_dir: Path | None = None

    @property
    def lan_mode(self) -> bool:
        """沒設 CLINIC_KEY＝診所區網內不驗證（雲端部署務必要設）。"""
        return not self.clinic_key

    @property
    def pilot(self) -> bool:
        return self.pilot_dir is not None


def load_settings() -> Settings:
    static = os.environ.get("STATIC_DIR")
    static_dir = Path(static) if static else Path(__file__).resolve().parents[2] / "pwa" / "public"
    # PILOT_DIR 留空＝關閉試用留底。留底含病歷號與整張照片，所以必須明確設定才會寫。
    pilot = (os.environ.get("PILOT_DIR") or "").strip()
    return Settings(
        backend=os.environ.get("OCR_BACKEND", "rapidocr"),
        threshold=_float("OCR_CONF_THRESHOLD", DEFAULT_THRESHOLD),
        clinic_key=os.environ.get("CLINIC_KEY") or None,
        port=_int("PORT", 8790),
        static_dir=static_dir if static_dir.is_dir() else None,
        growth_tool_url=os.environ.get("GROWTH_TOOL_URL", "/tool.html"),
        max_image_bytes=_int("MAX_IMAGE_BYTES", 12_000_000),
        include_debug=os.environ.get("OCR_DEBUG", "1") != "0",
        pilot_dir=Path(pilot) if pilot else None,
    )
