# -*- coding: utf-8 -*-
"""啟動伺服器：python run.py（PORT 預設 8790）。

Windows 上就是這一行，不需要 gunicorn。單 worker 即可——
辨識是 CPU 密集且 onnxruntime session 不共享，多 worker 只會多吃記憶體。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from growth_ocr.api import create_app  # noqa: E402
from growth_ocr.config import load_settings  # noqa: E402


def main() -> None:
    settings = load_settings()
    app = create_app(settings)
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"生長曲線判讀伺服器：http://{host}:{settings.port}/　後端={settings.backend}　"
          f"{'（LAN 模式，不驗密鑰）' if settings.lan_mode else '（需 x-clinic-key）'}")
    if settings.static_dir:
        print(f"靜態檔：{settings.static_dir}")
    uvicorn.run(app, host=host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
