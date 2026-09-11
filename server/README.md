# `server/` — 本機表單判讀伺服器（不用 LLM）

手寫方格表單 → 拍照 → 四角定位校正 → 切格 → 逐格辨識 → 契約 JSON。
全部在診所自己的機器上跑，純 CPU，沒有雲端呼叫（`google-vision` 後端除外，預設不啟用）。

管線與 API 契約見 `../docs/ARCHITECTURE-v2.md`；表單座標來自 `../form/template.json`。

## 表單版本

現行表單是 **10 列版**（2026-09-10）：病歷號 8 格、量測列 10 列（`rows[0..8]` 歷史、`rows[9]` label 為 `today`）、
格高 10mm，共 171 個數字格＋2 個勾選框。**所有格數、列數、今日列位置都從 `form/template.json` 讀，程式裡沒有寫死**；
表單再改版時重新產生 template.json 即可（做法見 `../docs/ARCHITECTURE-v2.md` 附錄）。

## API 契約範例

`POST /api/ocr`　body `{ "image": "<data URL>", "today": "YYYY-MM-DD" }`

```json
{ "data": { "seq": "0012345", "gender": "male", "birthDate": "2019-03-05",
            "fatherHeight": 175, "motherHeight": null,
            "measurements": [ { "measureDate": "2026-08-22", "height": 123.5, "weight": 24.0 } ],
            "uncertain": [ "motherHeight", "measurements[0].weight" ],
            "notes": "右下角反光" },
  "backend": "rapidocr", "ms": 850,
  "debug": { "cells": [ { "path": "seq[0]", "char": "0", "conf": 0.98 } ] } }
```

`seq` 是 **HIS 病歷號的字串**，不是候診序號：**前導零有意義，一律原樣輸出**（`"0012345"` 不可變成 `"12345"`）。
填寫規則是靠右填，所以前面留白＝沒用到的位數（正常，不標黃）；中間有空格＝讀法有歧義 → `null` ＋ 進 `uncertain`；
尾端留白＝可能漏填一位 → 仍給值但進 `uncertain`。整排空白 → `null`，不標黃。

## 模組

| 檔案 | 職責 |
|---|---|
| `growth_ocr/template.py` | 讀 `form/template.json`，攤平成 173 個 Cell（171 數字格＋2 勾選框），預印格標記 skip |
| `growth_ocr/geometry.py` | 找四角定位塊、判方向、透視校正成 8 px/mm（1680×2376）灰階，並驗證格線真的對上版面 |
| `growth_ocr/cells.py` | 依 template 切格、內縮 0.8mm 去框線、清殘留線段、算墨水比例判空白／勾選 |
| `growth_ocr/recognizers/base.py` | 辨識器介面 `recognize(cells) -> [(char\|None, conf)]`、字元正規化、後端註冊 |
| `growth_ocr/recognizers/rapidocr_backend.py` | RapidOCR（ONNX、純 CPU）逐格辨識，預設後端 |
| `growth_ocr/recognizers/vision_backend.py` | Google Cloud Vision REST，整張校正影像只送一次，symbol 依中心點對回格子 |
| `growth_ocr/recognizers/digit_cnn.py` | 第二階段自訓 CNN 的介面佔位（`NotImplementedError`） |
| `growth_ocr/parse.py` | 格子結果 → 契約 JSON：民國轉西元、小數組合、身高體重去前導零（病歷號**不**去）、性別、today 補日期、uncertain |
| `growth_ocr/pipeline.py` | 上面全部串起來的唯一入口（API 與 bench 共用同一條路） |
| `growth_ocr/api.py` | FastAPI：`POST /api/ocr`、`GET /api/config`、`GET /api/health`、靜態提供 `pwa/public/` |
| `growth_ocr/config.py` | 環境變數設定 |
| `growth_ocr/bench.py` | benchmark CLI：每格準確率、整張全對率、沉默錯誤數、性別準確率、標黃率、每張耗時 |
| `growth_ocr/pilot_report.py` | 試用留底 CLI：欄位層級準確率、沉默錯誤清單，另可反推成 bench 用的 labels |
| `growth_ocr/synth.py` | 合成測試表單產生器（印刷數字＋透視變形＋模糊雜訊），測試與 benchmark 都靠它 |
| `run.py` | `python run.py` 起 uvicorn |

## 跑起來

```bash
cd server
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py                                          # http://0.0.0.0:8790/
```

第一次啟動時 rapidocr 會下載 ONNX 模型（約 140MB，之後離線可用）。
（Docker／NAS 版不會下載——模型在建置映像檔時就抓進去了，見 [`../docs/DEPLOY-NAS.md`](../docs/DEPLOY-NAS.md)。）

手機在同一個 Wi-Fi 開 `http://<伺服器IP>:8790/` 就是原本的 PWA 首頁。

### 環境變數

| 變數 | 預設 | 說明 |
|---|---|---|
| `PORT` | `8790` | 監聽埠 |
| `HOST` | `0.0.0.0` | 監聽位址 |
| `OCR_BACKEND` | `rapidocr` | `rapidocr` / `google-vision` / `digit-cnn` / `stub` |
| `OCR_CONF_THRESHOLD` | `0.85` | 低於此信心的格子 → 該欄位 null 並標黃 |
| `CLINIC_KEY` | 無 | **有設就驗** `x-clinic-key` 標頭；沒設＝區網模式不驗證 |
| `STATIC_DIR` | `../pwa/public` | 靜態檔目錄 |
| `GROWTH_TOOL_URL` | `/tool.html` | 回給前端的曲線工具網址 |
| `OCR_DEBUG` | `1` | 設 `0` 則回應不含 `debug.cells` |
| `RAPIDOCR_VERSION` | `PPOCRV6` | 換 `PPOCRV5` 可用較小的 mobile 模型 |
| `RAPIDOCR_REC_WIDTH` | `96` | 辨識輸入寬度；`320`（rapidocr 預設）慢 4 倍但信心值更高 |
| `GOOGLE_VISION_API_KEY` | 無 | 只有 `OCR_BACKEND=google-vision` 才需要 |
| `PILOT_DIR` | 無（空＝關閉） | 試用留底目錄，見下節。**留底含病歷號與原始照片** |

安全提醒：`CLINIC_KEY` 沒設就是**不驗證任何請求**。這在診所區網內是刻意的（護理師不用輸密鑰），
但如果哪天把這台機器對外開放，一定要設 `CLINIC_KEY`。

## 試用模式（pilot mode）

診所同仁試用期間，把每一張照片、機器輸出、以及護理師在核對卡按下送出時的最終值都留底，
**試用本身就是 benchmark 資料，不必再另外人工標註**。設了 `PILOT_DIR` 就開啟（預設關閉）：

```bash
export PILOT_DIR=/var/growth/pilot        # Windows: set PILOT_DIR=C:\GrowthCurve\pilot
```

留底檔案（`<id>` 是 UTC 時間戳＋4 位隨機十六進位，例如 `20260911T103012Z-a1f3`）：

| 檔案 | 何時寫入 | 內容 |
|---|---|---|
| `<id>.jpg` | 每次 `/api/ocr`（含幾何失敗） | 上傳的**原始位元組**，不重新編碼 |
| `<id>.ocr.json` | 判讀成功 | `data`、`backend`、`ms`、`debug.quality`、`debug.cells` |
| `<id>.error.json` | 幾何失敗（400） | 回給護理師的重拍訊息 |
| `<id>.confirmed.json` | 護理師按「確認，送到診間」或「直接開啟曲線」 | 核對卡上的最終值（與 `/api/ocr` 同形狀） |

開啟後 `/api/ocr` 的回應多一個 `id`（400 幾何失敗時也附），`GET /api/config` 多回 `pilot: true`。
前端拿到 `id` 後，護理師確認時會 fire-and-forget 打 `POST /api/confirm`（body `{ id, data }`），**不阻塞開啟曲線**。
`PILOT_DIR` 沒設時 `/api/confirm` 回 `{ "stored": false }`（200，不是錯誤），前端不必知道有沒有開。
留底寫檔失敗只記 log，永遠不會讓判讀失敗——留底是附加價值，護理師的流程優先。
`OCR_DEBUG=0` 只是不把 debug 回給前端，留底仍會寫完整的 `debug`，否則報表算不出結果。

### 報表

```bash
cd server
python -m growth_ocr.pilot_report /var/growth/pilot
python -m growth_ocr.pilot_report /var/growth/pilot --export-labels labels.csv --json report.json
```

以**欄位**為計分單位（不是格子——護理師確認的是欄位，欄位才是會畫進曲線的東西）：
`seq`／`gender`／`birthDate`／`fatherHeight`／`motherHeight`，加上每一列的 `measureDate`／`height`／`weight`。
列以確認後的資料為準，OCR 的列先用日期、再用位置對齊（護理師刪列後位置會整個位移，日期才是穩定的鍵）。

每個欄位分成五類：**正確**／**標黃後修正**（系統照設計運作）／**標黃但其實正確**（誤報，只是多花一眼）／
**沉默錯誤**（機器有值、沒標黃、跟確認值不同）／**沉默漏讀**（機器空值、沒標黃、確認值有東西）。
後兩類會逐筆列出 id、欄位、機器值、確認值，**只要 >0 離開碼就是 1**——
錯的身高會安安靜靜地畫進生長曲線，這是醫療風險，其餘分類差只是麻煩。

`--export-labels` 把確認值反推成 `bench` 吃得下的格子層級 labels（數字靠右填、身高體重三位整數＋一位小數、
小數 `.0` 視為空白、民國年＝西元−1911、病歷號原樣靠右）。**前導留白的格子無法判斷原本是否寫了 0**，
所以那些格子不輸出，檔頭也寫了同一句提醒：這份標註僅供參考，正式 benchmark 仍要人工複核。

### 隱私（重要）

留底目錄裡有**病歷號與整張表單照片**，等於一份可識別身分的病歷資料。

- 只能放在**診所自己的伺服器**上，不要放到 NAS 共用資料夾、雲端同步資料夾或隨身碟。
- 試用結束、報表跑完後就把目錄刪掉；要留樣本就先把 `seq` 塗掉。
- 平時（非試用期）`PILOT_DIR` 保持不設，伺服器就完全不落地任何照片，與原本的承諾一致。

## 測試

```bash
cd server
python -m pytest tests -q
```

測試不需要 OCR 模型（API 測試用 stub 辨識器，幾何／parse 測試用合成表單）。

## Benchmark

```bash
# 1) 產 20 張合成表單（印刷數字），順便產 labels.csv
python -m growth_ocr.synth /tmp/synth20 --count 20

# 2) 用真的 rapidocr 模型跑
python -m growth_ocr.recognize 照片.jpg --dump out/     # 單張看結果＋疊圖；--draft-labels 產標註草稿
python -m growth_ocr.bench /tmp/synth20 --labels /tmp/synth20/labels.csv --backend rapidocr

# 3) 想蒐集訓練資料就加 --dump-cells（每格存成 PNG，檔名含 photo 與 path）
python -m growth_ocr.bench 真實照片資料夾 --labels labels.csv --dump-cells /tmp/cells
```

`labels.csv` 欄位 `photo,path,value`，`value` 空字串＝該格空白。
`path` 用 template 的路徑字串，例如 `seq[0]`、`birth.y[1]`、`rows[2].height.int[0]`。
勾選框 `gender.male`／`gender.female` 也要有一列，`value` 只要非空就代表有打（`1`／`x`／`✓` 都算，
`synth.py` 寫的是 `x`、`docs/BENCHMARK.md` 的人工標註寫 `1`）——**有這兩列 bench 才算得出性別準確率**。
`synth.py` 產的第一張固定是「10 列全填、病歷號填滿」的最壞情況（`--no-full-first` 可關掉），
因為隨機取樣很可能整批都只填三五列，格子最多、切格最吃緊的那種表單反而測不到。

指標裡**沉默錯誤數是唯一不可接受的**：讀錯了、信心卻高過門檻，系統不會標黃，
錯的身高就直接畫進生長曲線。準確率低只是麻煩，沉默錯誤是醫療風險。

`synth.py` 的相機模擬預設對齊 `pwa/public/index.html` 的壓縮設定（長邊 1600、JPEG 82），
所以 benchmark 量到的畫質就是護理師手機實際送上來的。想壓力測試可加
`--warp 0.035 --blur 1.4 --noise 6 --long-edge 1200 --jpeg-quality 70 --rotate-some`。

## Windows 部署

完整 runbook 見 `deploy/AGENT_DEPLOY.md`，一鍵安裝 `deploy/bootstrap.ps1`、驗收 `deploy/verify.ps1`。

- Python 3.12+，`py -m venv .venv` → `.venv\Scripts\pip install -r requirements.txt`。
  裝完後建議 `pip freeze > requirements.lock`，之後照 lock 檔重現。
- **路徑**：程式用相對於 `server/` 的路徑找 `form/template.json` 與 `pwa/public/`，
  所以整個專案資料夾要一起複製過去；只搬 `server/` 會找不到 template（可用 `GROWTH_TEMPLATE`、`STATIC_DIR` 指定）。
  資料夾路徑避免中文與空白比較保險（照片整理專案已經踩過）。
- **模型檔**：rapidocr 會把 ONNX 下載到 `site-packages/rapidocr/models/`。診所機器若不能連外網，
  先在有網路的機器下載好，整個 `models/` 資料夾複製過去。
- **開機自動啟動**：用工作排程器建立「電腦啟動時」觸發、動作為 `<專案>\server\.venv\Scripts\python.exe <專案>\server\run.py`、
  勾選「不論使用者是否登入」的工作即可（與照片整理專案同一套做法）。
- 單一 worker 就好：辨識是 CPU 密集、onnxruntime session 不共享，多 worker 只會多吃記憶體。
