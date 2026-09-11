# 真實手寫實測流程（決定辨識後端用哪個）

目的：用 30 張**真實手寫**表單，量三個後端（rapidocr／google-vision／之後的 digit-cnn）的
每格準確率與**沉默錯誤數**。沉默錯誤＝讀錯但信心高、不會標黃──這是唯一不可接受的指標。

## 1. 收集（Albert／護理師，約 1 小時）

1. 列印 `form/growth-form-a4.html` 30 張（A4、無邊界、100%）。
2. 找**至少 6 個不同的人**填（含年長者、寫字快的人），每人 5 張，資料可以虛構但要合理。
   刻意涵蓋：1 和 7、0 和 6、4 和 9 這些易混淆數字；有塗改的格子；有空白列；只填一列的。
3. 用手機在**預定的拍攝台**拍（固定角度、四角入鏡），另外故意拍 5 張歪的／偏暗的當壓力測試。
4. 照片放進 `bench/photos/`，檔名 `f01.jpg` … `f35.jpg`（用手機原生相機拍、原始解析度）。
5. 另外挑 5 張**透過 PWA 拍照建檔**走一次（這才是正式流程：前端會壓縮到 2400px 再上傳），
   核對卡結果要與 bench 一致；若不一致，到 ⚙︎ 把「上傳解析度」改 3000 再試。

## 1.5 先看單張結果（每拍一張就能看）

```bash
cd server && python -m growth_ocr.recognize ../bench/photos/f01.jpg --dump ../bench/dump
```

會印出病歷號、生日、每列身高體重、不確定欄位，並在 `bench/dump/f01_overlay.png` 畫出每一格的辨識結果：
綠框＝可信、橘框＝低信心、灰框＝空白。**先看疊圖**：格子框對不上表格＝幾何問題（重拍或檢查列印縮放）；
框對得上但數字錯＝辨識問題（記下是哪個數字、誰的筆跡）。

## 2. 標註（一張約 2 分鐘）

省力做法：讓程式先產草稿，只修錯的格子：

```bash
cd server && python -m growth_ocr.recognize ../bench/photos/*.jpg --quiet --draft-labels ../bench/labels.csv
```

再用 Numbers／Excel 開 `bench/labels.csv`，對照紙本修正錯誤、補上漏讀的格子（漏讀＝草稿裡沒有那一列）。

`bench/labels.csv`，欄位 `photo,path,value`。只需標**有寫字的格子**與勾選框；沒列出的格子視為空白。

```csv
photo,path,value
f01.jpg,seq[5],0
f01.jpg,seq[6],1
f01.jpg,seq[7],2
f01.jpg,gender.male,1
f01.jpg,birth.y[0],1
f01.jpg,birth.y[1],0
f01.jpg,birth.y[2],8
f01.jpg,rows[9].height.int[0],1
f01.jpg,rows[9].height.int[1],2
f01.jpg,rows[9].height.int[2],3
f01.jpg,rows[9].height.dec[0],5
```

路徑名稱見 `form/template.json`（`seq[0..7]` 是病歷號 8 格、`rows[0]`～`rows[8]` 是歷史列，`rows[9]` 是「今日量測」）。

## 3. 執行

```bash
cd server && python -m growth_ocr.bench ../bench/photos --labels ../bench/labels.csv --backend rapidocr --dump-cells ../bench/cells
```

有 Google 金鑰時再跑一次 `--backend google-vision` 比較。

## 4. 判讀結果

| 指標 | 可接受 | 說明 |
|---|---|---|
| 沉默錯誤 | **0–1 格／30 張** | 超過就要調信心閾值、改切格或換後端 |
| 每格準確率 | ≥ 97% | 低於此值標黃會太多，護理師核對變慢 |
| 標黃率 | ≤ 5% | 每張約 2 格內 |
| 每張耗時 | ≤ 3 秒（診所 i5） | Mac 數字約可乘 1.5–2 |
| 幾何失敗 | 只出現在故意拍歪那 5 張 | 正常拍攝台照片必須 100% 找到四角 |

`bench/cells/` 裡的格子影像＋修正後的 labels 就是第二階段 digit-cnn 的訓練資料，請保留。

## 已知缺口
- 合成表單全是印刷數字，只證明幾何與管線正確，**不代表手寫準確率**。
