#!/usr/bin/env bash
# 在 Mac 上把專案打包成可搬到診所伺服器的 zip（排除 venv、快取、Vercel 殘留）。
# 用法：bash scripts/pack-for-clinic.sh  → 產生 ~/Desktop/GrowthCurve_<日期>.zip
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$HOME/Desktop/GrowthCurve_$(date +%Y%m%d).zip"
STAGE="$(mktemp -d)/GrowthCurve"
mkdir -p "$STAGE"
# 伺服器只需要這四個資料夾；growth-tool 帶著是為了離線版備援
rsync -a --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' --exclude '.vercel' \
      --exclude 'node_modules' --exclude '.DS_Store' \
      "$ROOT/server" "$ROOT/form" "$ROOT/pwa/public" "$ROOT/growth-tool" "$ROOT/docs" "$STAGE/"
# pwa/public 要落在 pwa/public（api.py 用相對路徑找它）
mkdir -p "$STAGE/pwa" && mv "$STAGE/public" "$STAGE/pwa/public"
# zip 根目錄放一份 runbook，讓「桌面有 zip」這句指令也能一路走完
cp "$ROOT/server/deploy/AGENT_DEPLOY.md" "$STAGE/INSTALL.md"
rm -f "$OUT"
(cd "$(dirname "$STAGE")" && zip -qr "$OUT" GrowthCurve)
echo "打包完成：$OUT（$(du -h "$OUT" | cut -f1)）"
echo "→ 複製到 NAS 或隨身碟，解壓到診所伺服器 C:\\GrowthCurve"
