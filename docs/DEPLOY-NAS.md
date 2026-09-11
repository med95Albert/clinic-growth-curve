# 部署到診所 NAS（Synology Container Manager / Docker）

> ⚠️ **2026-09-11 實測結論：DS420+（Celeron J4025、1.75 GB RAM、swap 已用 690 MB）不適合跑辨識。**
> 真實照片 175–177 秒、合成表單 260–297 秒，執行緒限制與 SMALL 模型都救不回來（記憶體換頁）。
> 診所請改用 Windows 電腦（i5-12400 以上、16 GB）跑 `server/deploy/AGENT_DEPLOY.md`。
> 本文件保留給記憶體 ≥ 4 GB、有 AVX2 的 NAS 或其他 Linux 主機。


把判讀伺服器跑在診所的 Synology NAS 上，護理師的手機連 `http://<NAS IP>:8790/` 就能用。
NAS 24 小時開機、不必額外準備一台電腦，是最省事的一條路線。

映像檔已經由 GitHub Actions 建好放在 GHCR，**NAS 只負責下載、不負責建置**；
rapidocr 的 ONNX 模型在建置時就烤進映像檔了，所以 NAS 第一次啟動**不需要連外網抓模型**
（只要能連到 ghcr.io 把映像檔拉下來即可，拉完之後整台斷網也照跑）。

Windows 伺服器路線（原本的做法）見 [`../server/deploy/AGENT_DEPLOY.md`](../server/deploy/AGENT_DEPLOY.md)。

---

## 0. 先確認這台 NAS 能不能跑（30 秒）

**判斷方式：DSM → 套件中心 → 搜尋「Container Manager」。**

| 搜尋結果 | 結論 |
|---|---|
| 找得到 Container Manager（DSM 7.2+） | ✅ 可以走這條路線，往下做 |
| 找得到 Docker（DSM 6.x / 7.0–7.1 的舊名字） | ⚠️ 可以跑，但沒有「專案」貼 compose 的介面，請走下面的 **SSH 路線** |
| **搜不到**（搜尋結果沒有這個套件） | ❌ 這台 NAS 不支援容器 → **改用 Windows 伺服器路線** |

搜不到幾乎都是同一個原因：這台是 **ARM 處理器的入門機種**（型號結尾 `j`、`play`，
或 DS223／DS423 這類），Synology 只在 x86（Intel Celeron／Atom／AMD Ryzen）機種上提供容器套件。
不必再找外掛或第三方套件，直接照 `server/deploy/AGENT_DEPLOY.md` 裝在診所的 Windows 電腦上就好。

另外兩個前提：

- **記憶體至少 2GB**，建議 4GB 以上。判讀時 onnxruntime 會載入約 140MB 的模型並吃掉數百 MB 工作記憶體。
- **硬碟留 1.5GB 以上**給映像檔。
- **8790 埠沒被佔用**。被佔的話改 `docker-compose.yml` 裡 `ports` 左邊那個數字（例如 `18790:8790`），
  手機網址跟著改成 `http://<NAS IP>:18790/`。

### 效能預期

Celeron 等級的 NAS 每張表單約 **3–8 秒**（診所 i5 電腦約 1–2 秒），比較慢但護理師等得起；
真的嫌慢就回頭用 Windows 伺服器，不要去調 `RAPIDOCR_REC_WIDTH`（調大只會更慢、調小會掉準確率）。

---

## 1. 部署：Container Manager 介面（DSM 7.2，建議）

1. **套件中心** → 搜尋 **Container Manager** → 安裝。
2. 打開 **File Station**，在某個共用資料夾下建一個資料夾放這個專案，例如
   `docker/growth-curve`（完整路徑會是 `/volume1/docker/growth-curve`）。
3. Container Manager → 左側 **專案** → **新增**：
   - **專案名稱**：`growth-curve`
   - **路徑**：選剛才建的 `docker/growth-curve`
   - **來源**：選「**建立 docker-compose.yml**」，把本專案根目錄 `docker-compose.yml` 的內容整段貼進去
     （或先用 File Station 把檔案上傳到那個資料夾，介面會自動讀到）。
   - 下一步的「網頁入口設定」可以略過，**下一步** → **完成**。
4. 它會開始 pull 映像檔（下載約 400–600MB、在 NAS 上展開後約 700MB，看網速 3–15 分鐘），
   跑完狀態顯示 **執行中**。

> 介面上的按鈕叫「建置」，但因為 compose 寫的是 `image:` 而不是 `build:`，
> 它實際做的事是「下載現成映像檔並啟動」，不會在 NAS 上編譯任何東西。

## 1'. 部署：SSH 路線（舊版 DSM，或你本來就習慣指令）

DSM → 控制台 → 終端機與 SNMP → 勾選「啟動 SSH 功能」，然後：

```bash
ssh <你的NAS帳號>@<NAS IP>
sudo -i
mkdir -p /volume1/docker/growth-curve && cd /volume1/docker/growth-curve
# 把本專案的 docker-compose.yml 放進來（scp、File Station 上傳，或 curl 抓 raw 檔）
docker compose up -d          # 舊版 DSM 若沒有 compose 子指令，改用：docker-compose up -d
docker compose logs -f        # 看它啟動，出現「生長曲線判讀伺服器：http://0.0.0.0:8790/」就對了
```

---

## 2. 確認（每次部署都做一次）

```bash
curl http://<NAS IP>:8790/api/health
# 期待：{"ok":true,"backend":"rapidocr","cells":173}
```

沒有 curl 就直接用瀏覽器開 `http://<NAS IP>:8790/api/health`。接著：

| # | 開這個網址 | 應該看到 |
|---|---|---|
| 1 | `http://<NAS IP>:8790/api/health` | `ok: true`、`backend: rapidocr` |
| 2 | `http://<NAS IP>:8790/` | 手機拍照頁（護理師的主畫面） |
| 3 | `http://<NAS IP>:8790/tool.html` | 生長曲線工具 |
| 4 | `http://<NAS IP>:8790/qr.html` | QR 頁，給護理師手機掃描加到主畫面 |
| 5 | 手機連員工 Wi-Fi 開 `http://<NAS IP>:8790/` | 能拍一張真實表單，出現核對卡 |

`docker ps` 的 STATUS 欄會從 `health: starting` 變成 `healthy`。
**第一次啟動要把 ONNX 模型載進記憶體，NAS 上可能 1～3 分鐘**，這段期間網頁打不開是正常的。

---

## 3. 更新

```bash
cd /volume1/docker/growth-curve
docker compose pull && docker compose up -d
```

Container Manager 介面：專案 → 選 `growth-curve` → **動作** → **重新建置**（它會先 pull 新的 `:latest`）。

要退回舊版就把 compose 裡的 `:latest` 改成某一版的 `:sha-<短碼>`
（短碼在 GitHub Actions 的建置紀錄裡，也可以在 GitHub 右側 Packages 頁面看到），再 `up -d` 一次。

---

## 4. 試用留底目錄在 NAS 的哪裡

**預設是關閉的**，伺服器不落地任何照片——平時就該維持這樣。

要在試用期間蒐集 benchmark 資料時，把 `docker-compose.yml` 裡的 `PILOT_DIR: ""` 改成
`PILOT_DIR: "/data/pilot"`，然後 `docker compose up -d`。之後：

- 容器裡的路徑：`/data/pilot`
- **NAS 上的實際路徑**：compose 檔旁邊的 `pilot/`，也就是 `/volume1/docker/growth-curve/pilot/`
- 裡面會有 `<id>.jpg`（原始照片）、`<id>.ocr.json`（機器讀到什麼）、`<id>.confirmed.json`（護理師確認後的值）

> **隱私（重要）**：這個目錄裡有**整張表單照片＋病歷號**，等於一份可識別身分的病歷資料。
> 放在 NAS 上時務必確認這個資料夾：
> **① 沒有被設成共用資料夾對外分享 ② 沒有被 Synology Drive／Cloud Sync／Hyper Backup 同步到雲端**。
> 試用結束、跑完報表就整個刪掉。詳見 [`../server/README.md`](../server/README.md)「試用模式」。

報表（在 NAS 上跑）：

```bash
docker compose exec growth python -m growth_ocr.pilot_report /data/pilot
```

若出現寫檔權限錯誤（log 會看到「試用留底寫檔失敗」），在 NAS 上執行
`chown -R 1000:1000 /volume1/docker/growth-curve/pilot`——容器裡是用 uid 1000 的非 root 使用者跑的。

---

## 5. 疑難排解

| 症狀 | 處理 |
|---|---|
| `docker compose pull` 回 `denied` / `unauthorized` | GHCR 上的 package 還是 private。到 GitHub → Packages → `clinic-growth-curve` → Package settings → Change visibility → Public（只要做一次） |
| 手機打不開網址 | 手機是否在同一個 Wi-Fi；NAS 防火牆（DSM → 控制台 → 安全性 → 防火牆）是否擋了 8790；AP 隔離 |
| 狀態一直 `health: starting` 然後 `unhealthy` | `docker compose logs` 看錯誤。多半是記憶體不足（NAS 只有 1–2GB 又同時跑很多套件） |
| 每張要 10 秒以上 | NAS CPU 被其他套件（相片索引、備份、Plex）吃掉了；錯開時段，或改用 Windows 伺服器 |
| 「找不到定位塊」 | 四角黑方塊要完整入鏡、紙攤平、避開反光——這是拍照問題，不是 NAS 問題 |
| 改了表單版面之後讀不出來 | 要重新產生 `form/template.json` 並**重建映像檔**（template 是烤進映像檔的），見 `docs/ARCHITECTURE-v2.md` 附錄 |
| 想在 NAS 上自己建映像檔 | 不建議（慢、吃記憶體）。真的要的話：把整個 repo 放到 NAS，`docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build`，建置過程需要外網 |

## 實際部署踩到的坑（2026-09-11，DS420+）

1. **`/volume1/docker` 一般帳號寫不進去** → 整段用 `sudo sh -c '...'`，並用 `ssh -t` 讓 sudo 能問密碼。
2. **sudo 的 PATH 沒有 docker** → `export PATH=/usr/local/bin:/var/packages/ContainerManager/target/usr/bin:$PATH`。
3. **Synology 不會自動建立 bind mount 的資料夾** → 啟動前 `mkdir -p .../pilot && chown 1000:1000 .../pilot`，否則 `Bind mount failed: ... does not exist`。
4. 第一次呼叫 `/api/ocr` 會載入模型，比之後慢；若每張都要幾十秒以上，先看 `docker stats`（記憶體是否吃滿、換頁）與 `OCR_THREADS` 是否等於核心數，再考慮 `RAPIDOCR_MODEL_TYPE=SMALL`。

從 Mac 一行完成部署（把帳號換成你的 DSM 帳號；會問 SSH 與 sudo 密碼各一次）：

```bash
ssh -t <DSM帳號>@<NAS IP> "sudo sh -c 'export PATH=/usr/local/bin:/var/packages/ContainerManager/target/usr/bin:\$PATH; mkdir -p /volume1/docker/growth/pilot && chown 1000:1000 /volume1/docker/growth/pilot && cd /volume1/docker/growth && curl -fsSL https://raw.githubusercontent.com/med95Albert/clinic-growth-curve/main/docker-compose.yml -o docker-compose.yml && (docker compose pull; docker compose up -d) && docker ps --filter name=growth-curve'"
```

診斷一行（CPU／記憶體／容器資源／log）：

```bash
ssh -t <DSM帳號>@<NAS IP> "sudo sh -c 'export PATH=/usr/local/bin:\$PATH; nproc; grep -m1 \"model name\" /proc/cpuinfo; free -m; docker stats --no-stream growth-curve; docker logs --tail 20 growth-curve'"
```
