# 生長曲線判讀伺服器 — 一鍵安裝（Windows PowerShell 5.1+）
# 前提：專案已解壓到 C:\GrowthCurve，Python 3.12 已裝（py launcher 可用）。
# 與照片整理專案 bootstrap.ps1 同一套寫法：原生 exe 的失敗要自己檢查 $LASTEXITCODE。
$ErrorActionPreference = 'Stop'
$Root   = 'C:\GrowthCurve'
$Server = Join-Path $Root 'server'
$Venv   = Join-Path $Server '.venv'
$Py     = Join-Path $Venv 'Scripts\python.exe'
$Port   = 8790

function Assert-ExitCode($what) { if ($LASTEXITCODE -ne 0) { throw "失敗：$what（exit $LASTEXITCODE）" } }

Write-Host "== 1/6 檢查 Python 3.12 =="
py -3.12 --version; Assert-ExitCode 'py -3.12 --version（Python 3.12 未安裝，或 App Execution Alias 擋在前面）'
if (-not (Test-Path (Join-Path $Server 'run.py')))          { throw "找不到 $Server\run.py：專案是否解壓到 $Root？" }
if (-not (Test-Path (Join-Path $Root 'form\template.json'))) { throw "找不到 form\template.json" }

Write-Host "== 2/6 建立 venv 並安裝依賴 =="
if (-not (Test-Path $Py)) { py -3.12 -m venv $Venv; Assert-ExitCode '建立 venv' }
& $Py -m pip install --upgrade pip --quiet; Assert-ExitCode 'pip 升級'
& $Py -m pip install -r (Join-Path $Server 'requirements.txt'); Assert-ExitCode 'pip install -r requirements.txt'
& $Py -c "import fastapi, rapidocr, onnxruntime, cv2, PIL; print('deps OK')"; Assert-ExitCode 'import 檢查（onnxruntime DLL 錯誤 → 裝 VC++ Redistributable x64）'
# PS 5.1 的 > 會寫 UTF-16；lock 檔要給 pip 與人讀，統一用 ASCII
& $Py -m pip freeze | Set-Content -Encoding ASCII (Join-Path $Server 'requirements.lock')

Write-Host "== 3/6 合成表單 benchmark（同時下載 rapidocr 模型，需連外網一次）=="
$Synth = Join-Path $env:TEMP 'growth_synth'
if (Test-Path $Synth) { Remove-Item -Recurse -Force $Synth }
Push-Location $Server
try {
  & $Py -m growth_ocr.synth $Synth --count 5 --seed 1; Assert-ExitCode '產生合成表單'
  & $Py -m growth_ocr.bench $Synth --labels (Join-Path $Synth 'labels.csv') --backend rapidocr; Assert-ExitCode 'benchmark（每格必須 100%、沉默錯誤 0；否則先別上線）'
} finally { Pop-Location }

Write-Host "== 4/6 寫啟動腳本 =="
# .bat 用 ASCII 寫、不放中文，避免主控台代碼頁問題；閾值依 docs/BENCHMARK.md 結果在此調整
@"
@echo off
cd /d $Server
set PORT=$Port
set OCR_BACKEND=rapidocr
set OCR_CONF_THRESHOLD=0.85
set CLINIC_KEY=
rem set GOOGLE_VISION_API_KEY=
rem set PILOT_DIR=$Root\pilot
.venv\Scripts\python.exe run.py
"@ | Set-Content -Encoding ASCII (Join-Path $Root 'start_growth.bat')

Write-Host "== 5/6 防火牆（僅 private/domain；需系統管理員）=="
$rule = Get-NetFirewallRule -DisplayName 'GrowthCurve 8790' -ErrorAction SilentlyContinue
if (-not $rule) {
  try {
    New-NetFirewallRule -DisplayName 'GrowthCurve 8790' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Private,Domain | Out-Null
  } catch { Write-Warning "防火牆規則未能建立（可能不是系統管理員）。請以系統管理員執行：`n  New-NetFirewallRule -DisplayName 'GrowthCurve 8790' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Private,Domain" }
}

Write-Host "== 6/6 開機自啟捷徑 =="
$startup = [Environment]::GetFolderPath('Startup')
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut((Join-Path $startup 'GrowthCurve.lnk'))
$sc.TargetPath = (Join-Path $Root 'start_growth.bat'); $sc.WorkingDirectory = $Server; $sc.Save()

Write-Host ""
Write-Host "完成。接著：" -ForegroundColor Green
Write-Host "  1) $Root\start_growth.bat   （首次手動啟動，之後開機自動）"
Write-Host "  2) $Server\deploy\verify.ps1 （驗收）"
