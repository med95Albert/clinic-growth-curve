# AGENT_DEPLOY — 生長曲線判讀伺服器：診所 Windows 伺服器部署 runbook

給現場執行部署的人（或診所伺服器上的 Claude Code）。與「診所照片整理」專案**同一台伺服器、同一套做法**：
Python 3.12 venv、啟動 .bat、「啟動」資料夾捷徑開機自啟。照片整理專案的 `deploy/AGENT_DEPLOY.md` 疑難排解全部適用。

## 目標與成功定義

- 伺服器開機後自動起 `http://<伺服器IP>:8790/`；手機在員工 Wi-Fi 開首頁能拍照建檔；診間電腦開 `/room.html` 與 `/tool.html`。
- `verify.ps1` 全綠：health、config、靜態頁、**用合成表單真的跑一次辨識並比對數值**。
- 照片與辨識結果**不離開診所內網**；伺服器不儲存任何照片。

## 鐵律

1. 專案放 `C:\GrowthCurve`（**路徑不能有中文或空白**——opencv／onnxruntime 在中文路徑會出怪錯）。
2. 一律用 `py -3.12`，不要用 `python`（Windows 的 App Execution Alias 會把 `python` 導到 Microsoft Store 存根）。
3. 以 `requirements.txt` 安裝；成功後 `pip freeze > requirements.lock` 留底，之後重裝照 lock。
4. 首次啟動一定要連外網一次讓 rapidocr 下載模型（約 140MB）；之後可離線。無法連外網 → 在有網路的機器裝好後複製整個 `.venv\Lib\site-packages\rapidocr\models\`。
5. 防火牆規則只開 `private,domain` 設定檔；若伺服器網路被判成 Public，先改成 Private（`Set-NetConnectionProfile -NetworkCategory Private`），不要把規則開到 public。
6. 環境變數（閾值、密鑰）只寫在 `start_growth.bat`，不寫進程式。

## 固定路徑

| 用途 | 路徑 |
|---|---|
| 專案 | `C:\GrowthCurve`（內含 `server\`、`form\`、`pwa\public\`、`growth-tool\`、`docs\`） |
| venv | `C:\GrowthCurve\server\.venv` |
| 啟動腳本 | `C:\GrowthCurve\start_growth.bat` |
| 開機自啟捷徑 | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\GrowthCurve.lnk` |
| Port | 8790（照片整理專案用 8770，不衝突） |

## 給 Claude Code 的執行注意事項

- 你在 Windows 上的 shell 是 **Git Bash**。本文件的 PowerShell 指令請這樣執行：
  `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:/GrowthCurve/server/deploy/bootstrap.ps1"`
  （單行指令用 `-Command "..."`）。路徑在 Git Bash 裡可用正斜線。
- 每一步做完要驗證通過才進下一步；同一步失敗兩次就停下來，把狀況整理給人類。
- 需要系統管理員權限（防火牆規則、winget 安裝）先明講，請人類允許 UAC。
- 完成報告用本文件最後的格式；不要印出任何密鑰或病人資料。

## Step 0｜取得程式

**A. 有 GitHub（正常路徑）**
```bash
git clone https://github.com/med95Albert/clinic-growth-curve "C:/GrowthCurve"
```
clone 下來的 repo 根目錄就是 `C:\GrowthCurve`（裡面直接有 `server\`、`form\`、`pwa\public\`）。已存在就 `git -C C:/GrowthCurve pull`。

**B. 沒有 GitHub（zip 備援）**
在桌面、下載、D:\ 找 `GrowthCurve_*.zip`（取日期最新的一個），`Expand-Archive` 到 `C:\GrowthCurve`；解開後 `C:\GrowthCurve\server\run.py` 必須直接存在（多包一層就把內層搬上來）。zip 根目錄的 `INSTALL.md` 就是本文件。

驗證：
```powershell
Test-Path C:\GrowthCurve\server\run.py; Test-Path C:\GrowthCurve\form\template.json; Test-Path C:\GrowthCurve\pwa\public\index.html   # 三個都要 True
```

## Step 1｜前置檢查（PowerShell）

```powershell
py -3.12 --version                 # 沒有 → winget install -e --id Python.Python.3.12（需 UAC）
Get-NetConnectionProfile | Select Name, NetworkCategory   # 應為 Private 或 DomainAuthenticated
Get-NetIPAddress -AddressFamily IPv4 | ? { $_.IPAddress -like '192.168.*' } | Select IPAddress
```
記下 IP（與照片整理專案同一台伺服器）。若 `import onnxruntime` 之後報 DLL 錯誤，需要人類裝 VC++ 2015-2022 Redistributable x64（`winget install -e --id Microsoft.VCRedist.2015+.x64`）。

## Step 2｜（已併入 Step 0）

程式已在 `C:\GrowthCurve`，直接進 Step 3。

## Step 3｜安裝、模型、自測、常駐（一鍵）

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
C:\GrowthCurve\server\deploy\bootstrap.ps1
```
它會：建 venv → 裝依賴 → 用合成表單跑一次 benchmark（同時觸發模型下載，**必須每格 100%、沉默錯誤 0**）→ 寫 `start_growth.bat` → 加防火牆規則（需 UAC，會提示）→ 建開機自啟捷徑。任何一步失敗會停下並印出原因。

## Step 4｜首次啟動與驗收

```powershell
C:\GrowthCurve\start_growth.bat        # 留一個主控台視窗，屬正常
C:\GrowthCurve\server\deploy\verify.ps1
```
`verify.ps1` 全綠才算完成。它最後會印出手機要開的網址與 `/qr.html`。

| # | 項目 | 通過條件 |
|---|---|---|
| 1 | `/api/health` | `ok: true`，`backend: rapidocr` |
| 2 | `/api/config` | 200，`authRequired: false`（LAN 模式）或依設定 |
| 3 | 靜態頁 `/`、`/tool.html`、`/room.html`、`/qr.html` | 200 |
| 4 | 合成表單 `POST /api/ocr` | 回傳欄位與真值一致、`uncertain` 為空 |
| 5 | 手機實測 | 員工 Wi-Fi 開 `http://<IP>:8790/`，拍一張真實表單，核對卡出現 |
| 6 | 重開機演練 | 重開機 → 自動登入 → 主控台視窗自動出現 → 步驟 1 重測通過 |

## Step 5｜手機與診間電腦設定

- 診間電腦開 `http://<IP>:8790/qr.html`，護理師手機掃 QR → 加入主畫面（iPhone：Safari 分享→加入主畫面；Android：Chrome 選單→加到主畫面）。
- 診間電腦書籤：`http://<IP>:8790/room.html`（清單，需 Redis 才啟用，未接時可略）與 `http://<IP>:8790/tool.html`（曲線工具）。
- http 下 iPhone 的「加入主畫面」仍可用（以 Safari 開啟，不是獨立 App 視窗）；要完整 PWA 體驗才需要 https，屆時沿用照片整理專案的 `TLS_EARLY.md`（Caddy）。

## Step 6｜依 benchmark 結果調整（上線前必做一次）

`docs/BENCHMARK.md` 跑完 30 張真實表單後，把定案的值寫進 `start_growth.bat`：

```bat
set OCR_CONF_THRESHOLD=0.85      rem 信心閾值；調到沉默錯誤=0 為止
set OCR_BACKEND=rapidocr         rem 或 google-vision（需 GOOGLE_VISION_API_KEY）
set CLINIC_KEY=                  rem 留空＝內網不驗密鑰；設了就要在手機輸入一次
set PILOT_DIR=C:\GrowthCurve\pilot   rem 試用期間留底；不試用就整行刪掉
```
改完重跑 `start_growth.bat`（關掉舊視窗再開）。

試用期間會把每張照片、機器輸出與護理師確認後的最終值留在 `PILOT_DIR`，
**試用結束後在伺服器上跑一次報表**，確認沉默錯誤是 0 才算可以正式上線：

```powershell
C:\GrowthCurve\server\.venv\Scripts\python.exe -m growth_ocr.pilot_report C:\GrowthCurve\pilot
```
（工作目錄要在 `C:\GrowthCurve\server`。有沉默錯誤時離開碼是 1 並逐筆列出是哪一張、哪個欄位。）
報表跑完就把 `C:\GrowthCurve\pilot` 刪掉並把 `set PILOT_DIR=` 那行移除——
**留底含病歷號與整張表單照片，只能留在診所伺服器上**，細節見 `server/README.md`「試用模式」。

## 疑難排解

| 症狀 | 處理 |
|---|---|
| 手機打不開網址 | 手機是否在員工 Wi-Fi；伺服器 NetworkCategory 是否 Public（規則不生效）；AP 隔離（同照片整理專案 §8） |
| 8790 起不來 | 主控台錯誤訊息；`netstat -ano \| findstr 8790` 看是否被佔 |
| `DLL load failed while importing onnxruntime` | 裝 VC++ Redistributable x64，開新視窗重跑 |
| 第一次辨識很慢／失敗 | 模型還在下載；看主控台。無外網 → 鐵律 4 |
| 「找不到定位塊」 | 四角黑方塊要完整入鏡、紙攤平、避開反光；固定拍攝台幾乎不會發生 |
| 每張超過 5 秒 | 這台 CPU 太弱或被其他程式吃滿；`RAPIDOCR_REC_WIDTH` 維持預設 96，不要調大 |
| 改了表單版面 | 重新產生 `form/template.json`（見 `docs/ARCHITECTURE-v2.md` 附錄）並重印表單；舊表單與新範本不相容 |
