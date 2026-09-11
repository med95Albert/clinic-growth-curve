# 生長門診優化：手寫表單 → 拍照 → 本機辨識 → 生長曲線

家長在候診區填**方格式手寫表單**，護理師用手機拍一張照，診所內網的伺服器把每一格切出來辨識，
手機上出現核對卡，按一下就畫出生長曲線。看完診把「複診連結」存進 HIS 或印 QR 貼兒童手冊，
下次回診不用再填表、不用再拍照。

- **不用 LLM、不用任何雲端 API**：辨識用 RapidOCR（ONNX、純 CPU），跑在診所 Windows 伺服器。
- **照片與資料不出診所**；表單不含姓名，曲線連結不含病歷號。
- 決策脈絡與 API 契約：[docs/ARCHITECTURE-v2.md](docs/ARCHITECTURE-v2.md)。

```
form/          A4 手寫填寫單 + 格子座標範本 template.json
growth-tool/   生長曲線工具（改造版：連結帶入、合理性檢查、複診連結＋QR；含離線版）
pwa/public/    手機拍照端（PWA）、診間清單、QR 加入頁；由伺服器靜態提供
server/        辨識伺服器（FastAPI + growth_ocr）、benchmark、合成表單、Windows 部署套件
docs/          架構、SOP、實測流程
bench/         真實手寫實測照片與標註（待收集）
pwa/api/       v1（Vercel＋Anthropic）封存，不再部署
Dockerfile     容器映像檔（模型已烤在裡面）；docker-compose.yml 給 NAS／Linux 用
```

## 流程

```
手寫表單 → 手機拍照（PWA，壓縮到 2400px）
         → POST /api/ocr（診所伺服器）
             找四角定位塊 → 透視校正 8 px/mm → 依 template.json 切 171 格＋2 勾選框
             → 逐格辨識（rapidocr，可換 google-vision）→ 規則轉換（民國+1911、小數、性別）
             → 低信心／歧義 → null + uncertain
         → 核對卡（黃色＝要確認）→ 直接開啟曲線（#d= 連結，資料不經伺服器）
         → 複診連結存 HIS 註記 / QR 貼手冊
```

## 各部分

### `form/` 手寫填寫單
`growth-form-a4.html` 直接列印（A4、無邊界、100%、關頁首頁尾）。病歷號 8 格、生日民國年、父母身高、
**10 列量測**（9 列歷史＋今日）。四角 7mm 黑方塊是定位標記，右上角下方的小方塊判斷方向。
**改了版面就要重新產生 `template.json`**：開表單頁，在瀏覽器 console 執行 `extract-template.js`，把輸出存檔。舊表單與新範本不相容。

### `growth-tool/` 生長曲線工具
以 twkid.com 線上版為基礎，加了：`#d=<base64url JSON>` 帶入自動繪圖（hash 不會送到伺服器；同分頁換連結也會重載）、
跨欄位合理性檢查（體重>身高、BMI 超出 8–50、身高倒退、年增速離譜 → 擋下要求確認）、
診間匯入面板、**複診免重填**（一鍵複製連結＋QR 貼紙）。
`Growth_Curve_Tool.local.html`＋`vendor/` 是零連網離線版，`python3 make-local.py` 從線上版重新產生；
`pwa/public/tool.html` 是隨伺服器部署的複本，改動後記得同步。

### `pwa/public/` 手機端
`index.html` 拍照→核對卡→開曲線；`room.html` 診間清單（需 Redis，未接時自動停用）；`qr.html` 顯示本站 QR 給手機掃描加入。
相機用 `<input capture>`，照片不進相簿；上傳前壓縮到 2400px（⚙︎ 可改）。伺服器未設 `CLINIC_KEY` 時為內網模式，不需密鑰。

### `server/` 辨識伺服器
```bash
cd server && python run.py        # http://0.0.0.0:8790/，第一次會下載 rapidocr 模型
python -m pytest -q               # 83 tests
python -m growth_ocr.synth out/ --count 20     # 合成表單（印刷數字）
python -m growth_ocr.bench out/ --labels out/labels.csv --backend rapidocr
```
細節、環境變數、契約範例見 [server/README.md](server/README.md)。

## 診所電腦一句指令安裝

伺服器裝好 Claude Code 後貼這一句（細節與備援見 [docs/INSTALL-CLAUDE-CODE.md](docs/INSTALL-CLAUDE-CODE.md)）：

> 請 clone https://github.com/med95Albert/clinic-growth-curve 到 C:\GrowthCurve，然後完整遵照其中 server/deploy/AGENT_DEPLOY.md 執行安裝與驗收，全程遵守鐵律，最後給我完成報告、手機要開的網址與 QR 頁。

## 或：NAS／Docker

診所如果有 **x86 的 Synology NAS**（DSM 套件中心搜得到 **Container Manager**），可以不用另外準備電腦：
NAS 24 小時開機，把 `docker-compose.yml` 貼進 Container Manager 的「專案」，或 SSH 進去下一行指令就好。

```bash
docker compose up -d                      # 啟動（第一次會 pull 映像檔，下載約 0.5GB）
curl http://<NAS IP>:8790/api/health      # 確認：{"ok":true,"backend":"rapidocr","cells":173}
docker compose pull && docker compose up -d   # 更新
```

映像檔由 GitHub Actions 建好推到 `ghcr.io/med95albert/clinic-growth-curve`，**rapidocr 模型已經烤在裡面**，
NAS 啟動時不需要連外網抓模型。步驟、驗收、留底目錄位置、以及「NAS 沒有 Container Manager 怎麼辦」
（＝改走上面的 Windows 路線）見 [docs/DEPLOY-NAS.md](docs/DEPLOY-NAS.md)。
Celeron 等級的 NAS 每張約 3–8 秒，比 i5 電腦慢但可用。

想自己建映像檔：`docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build`。

## 實測與部署

1. **真實手寫實測**（唯一未驗證的環節）：印 30 張、6 人手寫、拍照放 `bench/photos/`、標 `bench/labels.csv`，
   依 [docs/BENCHMARK.md](docs/BENCHMARK.md) 跑 bench。**沉默錯誤（讀錯但沒標黃）必須為 0**，據此定信心閾值與後端。
2. **部署到診所 Windows 伺服器**：Mac 上 `bash scripts/pack-for-clinic.sh` 打包 → 解壓到 `C:\GrowthCurve` →
   `server\deploy\bootstrap.ps1` → `start_growth.bat` → `server\deploy\verify.ps1`。
   完整 runbook：[server/deploy/AGENT_DEPLOY.md](server/deploy/AGENT_DEPLOY.md)（與「診所照片整理」專案同一台伺服器、同一套做法）。
3. **護理站操作**：[docs/SOP.md](docs/SOP.md)。

## 資料流與隱私

| 階段 | 資料在哪 |
|---|---|
| 手寫單 | 紙本；病歷號、生日、性別、身高體重、父母身高；**無姓名** |
| 照片 | 手機不存相簿；伺服器辨識完即丟，不落地 |
| 辨識結果 | 只回到拍照的手機 |
| 曲線連結 | 生日、性別、身高體重、父母身高；**無病歷號**；資料在網址 hash，瀏覽器內計算 |
| 診間清單（選配） | Upstash Redis，30 分鐘後自動刪除 |

## 本機開發

`.claude/launch.json` 有三個設定：`static`（純靜態預覽）、`server`（內網模式）、`server-auth`（含密鑰）。
開發 venv 借用「診所照片整理」專案的 `integration/.venv`（已有 rapidocr、opencv、fastapi）；勿修改該專案。

## 第二階段

用護理師核對過的格子影像訓練小型數字分類模型（`digit_cnn` 介面已留），準確率超過 rapidocr 後替換；
`bench --dump-cells` 匯出的格子 PNG 加上修正後的 labels 就是訓練資料。
