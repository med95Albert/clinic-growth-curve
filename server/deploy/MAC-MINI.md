# Mac mini 專用機部署（建議路線）

整條辨識管線是在 macOS 上開發、benchmark、驗證的，Mac mini 是零平台差異的選擇。
流程分兩段：**在家先裝好、用手機測完 → 帶去診所插網路線**。

## 一、在家（約 15 分鐘，需要網路）

1. Mac mini 接螢幕鍵盤（或開「遠端登入」後從筆電 SSH），登入一個管理者帳號。
2. 打開終端機，貼這一行（會裝 uv、clone 程式、建虛擬環境、下載模型、合成自測、寫 LaunchDaemon；中途要輸入一次 sudo 密碼）：

```bash
curl -fsSL https://raw.githubusercontent.com/med95Albert/clinic-growth-curve/main/server/deploy/macos-install.sh | bash
```

   或者交給 Mac mini 上的 Claude Code：
   > 請執行 https://raw.githubusercontent.com/med95Albert/clinic-growth-curve/main/server/deploy/macos-install.sh 這個安裝腳本（bash），完成後把它印出的網址與驗證結果給我；若失敗，讀 ~/growth/logs/server.log 診斷後回報。

3. 腳本最後會印出 `http://<IP>:8790/`。用手機（同一 Wi-Fi）開它，拍一張表單走完核對卡→曲線。
4. **系統設定（讓它像伺服器）**：
   - 系統設定 → 能源：關閉「顯示器關閉時讓電腦進入睡眠」、勾「發生斷電後自動啟動」；或終端機 `sudo pmset -a sleep 0 disksleep 0 autorestart 1`。
   - 系統設定 → 一般 → 軟體更新 → 自動更新：**關閉「自動安裝 macOS 更新」**（避免半夜重開卡在登入畫面；LaunchDaemon 不需要登入，但更新中斷會停機）。
   - 系統設定 → 網路 → 防火牆：關閉，或允許 python 接受連線（首次啟動會跳提示，按允許）。
   - 系統設定 → 一般 → 共享 → 「遠端登入」開啟（之後從筆電 SSH 維護，不用接螢幕）。
   - 主機名稱改成 `growth-curve`（共享 → 本機主機名稱）：手機也可以用 `http://growth-curve.local:8790/`。

## 二、在診所（5 分鐘）

1. 插網路線（有線比 Wi-Fi 穩；診所 Wi-Fi 有瞬斷紀錄）。
2. 設固定 IP：系統設定 → 網路 → 乙太網路 → 詳細資訊 → TCP/IP → 手動。
   建議 `192.168.1.22`（緊鄰照片整理專案的 .21；設之前先在任一台電腦 `ping 192.168.1.22` 確認沒人用），
   子網路遮罩 `255.255.255.0`、路由器 `192.168.1.254`、DNS `192.168.1.254`。
3. 診間電腦開 `http://192.168.1.22:8790/qr.html`，同仁手機掃 QR 加入主畫面；`/staff.html` 是同仁操作卡。
4. 從你的筆電驗證：`curl http://192.168.1.22:8790/api/health` 應回 `{"ok":true,...}`。

## 日常

| 事 | 指令（在 Mac mini 或 SSH 進去） |
|---|---|
| 看狀態 | `sudo launchctl print system/com.clinic.growth-curve \| head -20` |
| 看 log | `tail -f ~/growth/logs/server.log` |
| 重啟 | `sudo launchctl kickstart -k system/com.clinic.growth-curve` |
| 更新程式 | 重跑安裝那一行（會 git pull、更新套件、重新自測、重載服務） |
| 開啟試用留底 | `sudo nano /Library/LaunchDaemons/com.clinic.growth-curve.plist` 把 `PILOT_DIR` 改成 `/Users/<帳號>/growth/pilot`，`mkdir -p ~/growth/pilot`，然後重啟 |
| 試用報表 | `cd ~/growth/clinic-growth-curve/server && .venv/bin/python -m growth_ocr.pilot_report ~/growth/pilot` |

留底目錄含病歷號與整張表單照片：只放在這台機器、報表跑完就刪；Mac mini 開啟 FileVault 全碟加密（系統設定 → 隱私權與安全性）。

## 為什麼不是其他機器

| 主機 | 結論 |
|---|---|
| NAS DS420+ | 實測 177–260 秒（1.75 GB RAM 換頁、無 AVX），淘汰 |
| Windows i5-12400（.21） | 可用（1–2 秒），runbook 見 `AGENT_DEPLOY.md`；缺點是要人在機器前安裝、Windows Update 會重開 |
| Ultra 5 225V 迷你主機 | 效能足夠，但它們是櫃檯／診間的工作機，會與 HIS 和同仁操作互相干擾，不建議兼任 |
| **Mac mini M4** | 專用、安靜、低功耗、與開發環境一致、可在家裝好再帶去 |
