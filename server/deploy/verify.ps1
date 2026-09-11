# 生長曲線判讀伺服器 — 驗收（伺服器啟動後執行）
$ErrorActionPreference = 'Stop'
$Root = 'C:\GrowthCurve'; $Server = Join-Path $Root 'server'; $Py = Join-Path $Server '.venv\Scripts\python.exe'
$Base = 'http://127.0.0.1:8790'
$fail = 0
function Check($name, $ok, $detail) { if ($ok) { Write-Host ("  [OK]   {0}  {1}" -f $name, $detail) } else { Write-Host ("  [FAIL] {0}  {1}" -f $name, $detail) -ForegroundColor Red; $script:fail++ } }

Write-Host "== 端點 =="
try { $h = Invoke-RestMethod "$Base/api/health"; Check 'health' ($h.ok -eq $true) "backend=$($h.backend) cells=$($h.cells)" } catch { Check 'health' $false $_.Exception.Message }
try { $c = Invoke-RestMethod "$Base/api/config"; Check 'config' ($null -ne $c.growthToolUrl) "authRequired=$($c.authRequired) backend=$($c.backend)" } catch { Check 'config' $false "$($_.Exception.Message)（設了 CLINIC_KEY 時 401 屬正常）" }
foreach ($p in '/', '/tool.html', '/room.html', '/qr.html') {
  try { $r = Invoke-WebRequest -UseBasicParsing "$Base$p"; Check "static $p" ($r.StatusCode -eq 200) '' } catch { Check "static $p" $false $_.Exception.Message }
}

Write-Host "== 合成表單真實辨識 =="
$Synth = Join-Path $env:TEMP 'growth_verify'
if (Test-Path $Synth) { Remove-Item -Recurse -Force $Synth }
Push-Location $Server
try {
  & $Py -m growth_ocr.synth $Synth --count 1 --seed 42 | Out-Null
  $jpg = Get-ChildItem $Synth -Filter *.jpg | Select-Object -First 1
  $b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($jpg.FullName))
  $body = @{ image = "data:image/jpeg;base64,$b64"; today = (Get-Date -Format 'yyyy-MM-dd') } | ConvertTo-Json -Compress
  $headers = @{}
  if ($env:CLINIC_KEY) { $headers['x-clinic-key'] = $env:CLINIC_KEY }
  $sw = [Diagnostics.Stopwatch]::StartNew()
  $r = Invoke-RestMethod -Method Post -Uri "$Base/api/ocr" -ContentType 'application/json' -Body $body -Headers $headers
  $sw.Stop()
  $d = $r.data
  Check 'ocr 回應' ($null -ne $d.birthDate) ("backend=$($r.backend) 伺服器耗時=$($r.ms)ms 總耗時=$($sw.ElapsedMilliseconds)ms")
  Check 'ocr 無標黃' ($d.uncertain.Count -eq 0) ("uncertain=" + ($d.uncertain -join ','))
  Check 'ocr 有量測列' ($d.measurements.Count -ge 1) ("rows=$($d.measurements.Count) 第一列=$($d.measurements[0].measureDate) $($d.measurements[0].height)/$($d.measurements[0].weight)")
  # 與 labels.csv 比對生日（民國→西元）
  $lab = Import-Csv (Join-Path $Synth 'labels.csv') | Where-Object { $_.photo -eq $jpg.Name }
  $y = ($lab | ? { $_.path -like 'birth.y*' } | Sort-Object path | % { $_.value }) -join ''
  $m = ($lab | ? { $_.path -like 'birth.m*' } | Sort-Object path | % { $_.value }) -join ''
  $dd = ($lab | ? { $_.path -like 'birth.d*' } | Sort-Object path | % { $_.value }) -join ''
  if ($y -and $m -and $dd) { $expect = '{0:d4}-{1}-{2}' -f ([int]$y + 1911), $m, $dd; Check '生日比對' ($d.birthDate -eq $expect) "got=$($d.birthDate) expect=$expect" }
} catch { Check 'ocr' $false $_.Exception.Message } finally { Pop-Location }

Write-Host "== 手機要開的網址 =="
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -like '192.168.*' -or $_.IPAddress -like '10.*' } | ForEach-Object {
  Write-Host ("  http://{0}:8790/        （首頁，護理師手機）" -f $_.IPAddress)
  Write-Host ("  http://{0}:8790/qr.html （在診間電腦開，手機掃 QR 加入）" -f $_.IPAddress)
}
Write-Host ""
if ($fail -eq 0) { Write-Host "全部通過" -ForegroundColor Green } else { Write-Host "$fail 項失敗" -ForegroundColor Red; exit 1 }
