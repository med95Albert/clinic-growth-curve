# 架構 v2：不用 LLM 的本機辨識（2026-09-10 決定）

## 決定與理由

- **否決「引導式輸入」**：那就是現況，太吃人力。目標是家長依規範手寫、護理師拍照、之後全由程式接手。
- **否決 Anthropic API**：不想依賴付費雲端 LLM。
- **診所全 Windows、配備不高（十代 i3～Core Ultra 5）**，跑不了 LLM。但「診所照片整理」專案已證明 **RapidOCR（ONNX、純 CPU）在診所常開的 Windows 伺服器上跑得動**，且有 FastAPI 佇列頁、ClinicSnap 資料夾介面可沿用。
- 表單是**方格單一數字**＋**四角定位塊**，辨識問題退化成「幾何切格 ＋ 單字元分類」——這不需要 LLM，也不需要理解版面。

## 管線（全部確定性，只有「認這一格是哪個數字」用模型）

```
照片 → 找四角 7mm 黑方塊（距紙邊 8mm，SVG 印出；＋右上 3mm 方向點定方向）
     → 透視校正到標準座標（210×297 mm，建議 8 px/mm → 1680×2376）
     → 依 form/template.json 切出 171 個數字格 + 2 個勾選框
     → 逐格辨識（可插拔後端）→ {char, confidence}
     → 規則轉換：民國+1911、去前導 0、小數組合、勾選框墨水密度判性別
     → 低信心／空白／多字 → null + uncertain[]
     → 與 v1 完全相同的 JSON → 現有核對卡 → 曲線工具 #d=
```

## 辨識後端（用 30 張真實手寫表單 benchmark 後定案）

| 後端 | 位置 | 費用 | 預期 | 角色 |
|---|---|---|---|---|
| `rapidocr` | 診所伺服器 | 0 | 印刷體極好、手寫數字待測 | 預設候選 |
| `google-vision` | 雲端（每月 1000 單位免費） | 0（診所用量內） | 手寫很好；一張校正後整頁＝1 單位，符號座標對回格子 | 準確度基準／備援 |
| `digit-cnn` | 伺服器或瀏覽器 | 0 | 需台灣筆跡資料；用核對後的格子影像訓練 | 第二階段、最輕 |

Benchmark 指標：每格準確率、整張全對率、**沉默錯誤數（讀錯且信心高）**、標黃率、每張耗時。沉默錯誤是唯一不可接受的指標。

## API 契約（與 v1 Vercel 版相同，前端零改動）

`POST /api/ocr`　body `{ "image": "<data URL jpeg/png>", "today": "YYYY-MM-DD" }`，標頭 `x-clinic-key`（LAN 內可設為不驗證）

回應：
```json
{ "data": { "seq": "0012345", "gender": "male", "birthDate": "2019-03-05",
            "fatherHeight": 175, "motherHeight": null,
            "measurements": [ { "measureDate": "2026-08-22", "height": 123.5, "weight": 24.0 } ],
            "uncertain": [ "motherHeight", "measurements[0].weight" ],
            "notes": "右下角反光" },
  "backend": "rapidocr", "ms": 850,
  "debug": { "cells": [ { "path": "birth.y[0]", "char": "1", "conf": 0.98 } ] } }
```
`GET /api/config` → `{ "growthToolUrl": "/tool.html", "queueEnabled": false, "backend": "rapidocr" }`

錯誤：`400` 找不到四角／影像不合格（訊息要能指導重拍：「左下角定位塊未入鏡」）。

## 部署

- 診所 Windows 伺服器（與照片整理專案同一台）：Python 3.12+、`pip install -r requirements.lock`，`python run.py` 起 FastAPI；同時靜態提供 `pwa/public/`（含 `/tool.html`）。
- 手機在診所 Wi-Fi 開 `http://<伺服器IP>:8790/`，`<input capture>` 在 http 也能叫相機；PWA 安裝需 https，沿用照片整理專案的 TLS 提前方案（Caddy）即可。
- 選配：監看 ClinicSnap 資料夾，拍進去就自動判讀進佇列。
- Vercel／Anthropic 版本封存於 `pwa/api/`，不再部署。

## 附錄：重新產生 template.json

在瀏覽器開啟 `form/growth-form-a4.html`，於 console 執行 `form/extract-template.js` 的內容，把輸出存成 `form/template.json`。

## 實拍後的修正（2026-09-11，第一張真實手寫表單）

合成表單全對，第一張實拍卻在幾何就失敗；逐步拆解後修了五處，都是合成資料永遠測不到的：

| 現象 | 原因 | 修正 |
|---|---|---|
| 找不到四角 | 定位塊與標題／頁尾文字相鄰，二值化後黏成一團 | 偵測前做形態學開運算洗掉筆畫；表單標題與頁尾文字內縮避開 |
| 校正後對位分數 0.175 < 0.18 | 手影讓半頁變暗，固定門檻看不到格線 | warp 後先做光照攤平（閉運算估背景再相除） |
| 空白格被讀成「1」「7」、信心 1.00 | 紙張微彎對位差 1mm，內縮 0.8mm 留下框線殘段 | 內縮 1.0mm；投影法清「貫穿整格（≥85%）的細直線」 |
| 158→138、112→117 沉默錯誤 | 第一版清線用厚度判斷，把 5 的上橫、2 的底橫刪了 | 改用長度判斷；手寫橫筆最多佔六七成寬 |
| 淡藍原子筆筆畫被切碎 | 墨水門檻 0.62×紙色太嚴 | 放寬到「比紙暗 22 級」 |

另外兩個真實行為：家長多半**把小數格留白**（8 個數值有 6 個）→ 改為算整數、不標黃；有人用**立可帶**塗改 → 只能靠標黃，SOP 要再強調「整格塗黑」。

結果：70 個有字格正確率 94%、沉默錯誤 0、標黃 6 格（含一格立可帶）。`bench/photos/f01.jpg`＋`bench/labels.csv` 是第一筆真實回歸樣本，之後任何調參都要先過它。
