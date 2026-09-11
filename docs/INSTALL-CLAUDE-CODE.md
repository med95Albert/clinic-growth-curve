# 診所電腦一句指令安裝（給 Claude Code）

與「診所照片整理」專案同一套做法：程式放 GitHub，診所伺服器上的 Claude Code 一句話 clone 下來、照 runbook 安裝並驗收。

## 事前準備（一次）

| 誰 | 做什麼 |
|---|---|
| Albert（Mac） | `gh auth login` 後建 repo 並 push（見下方「發佈」） |
| 診所伺服器（.21 那台常開的 Windows） | 裝好 Claude Code（它會要求 Git for Windows）；有 Python 3.12 更好，沒有的話 agent 會請你允許 UAC 用 winget 裝 |
| 網路 | 伺服器連線設定檔要是「私人」網路；安裝當下要能連外網一次（下載套件與 140MB 模型），之後可離線 |

## 一句指令（貼進診所伺服器的 Claude Code）

> 請 clone https://github.com/med95Albert/clinic-growth-curve 到 C:\GrowthCurve，然後完整遵照其中 server/deploy/AGENT_DEPLOY.md 執行安裝與驗收，全程遵守鐵律，最後給我完成報告、手機要開的網址與 QR 頁。

agent 會自己：建 venv、裝套件、下載模型、用合成表單自測（每格必須 100%）、寫啟動腳本、開防火牆 8790、設開機自啟、啟動、跑 `verify.ps1`、印出網址。**它會停下來問你的只有兩件事**：需要系統管理員權限的步驟（防火牆、winget）請你點 UAC；網路若被判成「公用」請你改「私人」。

## 完成後（人工，5 分鐘）

1. 診間電腦開 `http://<伺服器IP>:8790/qr.html`，同仁手機掃 QR → 加入主畫面。
2. 拍攝台：支架固定、A4 框畫好；板夾綁黑色 0.7mm 以上的筆。
3. 護理站照 `docs/SOP.md` 試用；試用留底在 `C:\GrowthCurve\pilot\`。

## 沒有 GitHub 時的備援：zip

Mac 上 `bash scripts/pack-for-clinic.sh` 產生 zip（根目錄含 `INSTALL.md`＝同一份 runbook），用 NAS／隨身碟／LINE 傳到伺服器桌面，指令改成：

> 桌面有 GrowthCurve 的 zip，請解壓到 C:\GrowthCurve（解開後 C:\GrowthCurve\server 要直接存在），然後照 C:\GrowthCurve\INSTALL.md 完整執行安裝與驗收，最後給我手機要開的網址。

## 更新版本

之後改了程式：Mac 上 commit + push；診所伺服器的 Claude Code 貼「請到 C:\GrowthCurve 執行 git pull，然後重跑 server/deploy/AGENT_DEPLOY.md 的 Step 3 到 Step 4」。改了表單版面（template.json 變了）要一併重印表單並作廢舊的。

## 發佈（Albert，一次）

```bash
gh auth login
```

```bash
cd "/Users/albertm1pro/Desktop/AI skill/生長門診優化" && gh repo create clinic-growth-curve --private --source=. --push
```

要公開（像照片整理專案一樣）就把 `--private` 換成 `--public`。repo 已排除 `bench/`（實拍照片）、`pilot/`（試用留底）與所有 `.env`，不含任何病人資料；文件內只有私有網段 IP。
