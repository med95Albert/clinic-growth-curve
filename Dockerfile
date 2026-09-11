# 生長曲線手寫表單判讀伺服器 — Docker 映像檔
#
# 目標：診所的 Synology NAS（Container Manager）或任何 Linux 機器
#       `docker compose up -d` 就能跑，**不需要連外網**（模型在建置時就烤進映像檔）。
#
# 設計決定（改之前先看這幾行）：
#  1. 基底固定 `python:3.12-slim-bookworm`（Debian 12）。不寫成 `python:3.12-slim`，
#     是因為 Debian 13（trixie）把 glib 套件改名成 libglib2.0-0t64，
#     基底哪天跟著換版，apt-get 會整個失敗；鎖 bookworm 讓建置可重現。
#  2. **不用 opencv-python-headless**。rapidocr 3.9.1 的 metadata 寫死
#     `Requires-Dist: opencv_python>=4.5.1.48`，pip 不認得 headless 是它的替代品，
#     結果會兩個 cv2 都裝（更大、還可能互相蓋掉）。所以維持 opencv-python，
#     改成在映像檔裡補 libgl1 / libglib2.0-0 這兩個系統函式庫（約 40MB）。
#  3. 多階段建置：builder 裝套件＋下載模型，runtime 只搬 /opt/venv 過來，
#     pip 快取、apt 快取都不會進最終映像檔。
#  4. 目錄結構維持 repo 原樣（/app/server、/app/form、/app/pwa/public），
#     因為 config.py 與 template.py 都用 `parents[2]` 往上找 form/ 與 pwa/public/，
#     不需要另外設 GROWTH_TEMPLATE / STATIC_DIR。

########################  第一階段：裝套件 + 烤模型  ########################
FROM python:3.12-slim-bookworm AS builder

# 這一階段刻意**不設** PYTHONDONTWRITEBYTECODE：要讓 pip 把 .pyc 寫進 /opt/venv。
# 執行階段是非 root、也不能寫 /opt/venv，沒有 .pyc 的話每次啟動都要重新編譯幾百個模組。
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# cv2 在 slim 需要的系統函式庫（見上面決定 2）
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 全部裝進 venv，最後整包搬到 runtime 階段（模型也在裡面）
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# requirements.txt 是唯一的版本來源：這裡只把測試用套件（pytest / httpx）切掉。
# 切法是砍掉 TEST-ONLY-BELOW 標記以下的整段；三個 grep 是保險絲——
# 哪天有人把執行期套件放到標記下面，建置會當場失敗，而不是做出一個跑不起來的映像檔。
COPY server/requirements.txt /tmp/requirements.txt
RUN sed '/TEST-ONLY-BELOW/,$d' /tmp/requirements.txt > /tmp/requirements-runtime.txt \
    && grep -q '^rapidocr==' /tmp/requirements-runtime.txt \
    && grep -q '^fastapi==' /tmp/requirements-runtime.txt \
    && ! grep -qiE '^(pytest|httpx)' /tmp/requirements-runtime.txt \
    && pip install --no-cache-dir -r /tmp/requirements-runtime.txt

# 建置時就把 rapidocr 的 ONNX 模型下載進映像檔（診所 NAS 可能完全沒有外網）。
# 用專案自己的程式觸發，確保下載到的版本／model_type 與執行時一模一樣
# （RapidOCR() 建構子會一次建 Det/Cls/Rec 三個 session，所以三個模型都會落地）。
COPY server/growth_ocr /build/growth_ocr
WORKDIR /build
RUN python -c "from growth_ocr.recognizers.base import get_recognizer; get_recognizer('rapidocr').warmup()"

# 驗證模型真的在映像檔裡，並把路徑與大小印進建置 log（總量 < 50MB 就當作失敗）
RUN python -c "import rapidocr,pathlib,sys; d=pathlib.Path(rapidocr.__file__).parent/'models'; fs=sorted(d.glob('*.onnx')); [print(f'{f}  {f.stat().st_size/1048576:.1f} MB') for f in fs]; t=sum(f.stat().st_size for f in fs); print(f'模型合計 {t/1048576:.1f} MB'); sys.exit(0 if t > 50*1048576 else 1)"

########################  第二階段：執行  ########################
FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="clinic-growth-curve" \
      org.opencontainers.image.description="手寫生長紀錄表單 → 本機 RapidOCR 判讀 → 生長曲線（純 CPU、無雲端）" \
      org.opencontainers.image.source="https://github.com/med95Albert/clinic-growth-curve"

# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 非 root 執行。uid/gid 固定 1000，NAS 上要對 ./pilot 設權限時才有個確定的數字可用。
RUN groupadd --gid 1000 growth \
    && useradd --uid 1000 --gid 1000 --no-log-init --create-home --shell /usr/sbin/nologin growth

COPY --from=builder /opt/venv /opt/venv

# 目錄結構與 repo 相同，api.py 的相對路徑推導才會成立
WORKDIR /app
COPY server/ /app/server/
COPY form/ /app/form/
COPY pwa/public/ /app/pwa/public/

# 試用留底掛載點。預設不開（PILOT_DIR 空字串），開了才會寫東西進來。
RUN mkdir -p /data/pilot && chown -R growth:growth /data

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8790 \
    OCR_BACKEND=rapidocr \
    OCR_CONF_THRESHOLD=0.85 \
    PILOT_DIR=""
# PILOT_DIR 刻意留空＝關閉試用留底。
# 留底會把「整張表單照片＋病歷號」寫進 /data/pilot，是可識別身分的病歷資料，
# 必須由部署的人在 docker-compose.yml 明確打開（見 server/README.md「試用模式」）。
# 映像檔預設就開＝任何人 pull 下來跑都會落地病人照片，所以這裡不給預設值。

USER growth
WORKDIR /app/server

EXPOSE 8790

# 用 python 打 /api/health，省得為了健康檢查多裝 curl
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
    CMD ["python", "-c", "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8790')+'/api/health',timeout=8).status==200 else 1)"]

# 單一 worker：辨識是 CPU 密集、onnxruntime session 不共享，多 worker 只會多吃記憶體
CMD ["python", "run.py"]
