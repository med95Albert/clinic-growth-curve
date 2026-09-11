#!/usr/bin/env bash
# 線上部署驗證：頁面 → 設定端點 → 真實打一次判讀 API（會花約 NT$0.5）。
# 用法：
#   export CLINIC_KEY=你的診所密鑰   （或先 cd pwa && vercel env pull --environment production .env.production.local）
#   bash scripts/verify-live.sh [BASE_URL]
set -u
BASE="${1:-https://growth-form-ocr.vercel.app}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

if [ -z "${CLINIC_KEY:-}" ] && [ -f "$HERE/pwa/.env.production.local" ]; then
  CLINIC_KEY="$(grep -E '^CLINIC_KEY=' "$HERE/pwa/.env.production.local" | cut -d= -f2- | tr -d '"')"
fi
[ -z "${CLINIC_KEY:-}" ] && { echo "✗ 沒有 CLINIC_KEY（export 或 vercel env pull）"; exit 1; }

echo "== 頁面 =="
for p in / /tool.html /room.html /manifest.webmanifest; do
  printf "  %-24s %s\n" "$p" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE$p")"
done

echo "== /api/config（帶密鑰）=="
curl -s -H "x-clinic-key: $CLINIC_KEY" "$BASE/api/config"; echo

echo "== /api/ocr（真實呼叫）=="
IMG="$HERE/scripts/test-form.png"
[ -f "$IMG" ] || python3 "$HERE/scripts/make-test-form.py" "$IMG" >/dev/null
B64="$(base64 < "$IMG" | tr -d '\n')"
TODAY="$(date +%F)"
printf '{"image":"data:image/png;base64,%s","today":"%s"}' "$B64" "$TODAY" > /tmp/ocr-body.json
START=$(date +%s)
curl -s -o /tmp/ocr-resp.json -w "  HTTP %{http_code}, %{time_total}s\n" \
  -H "Content-Type: application/json" -H "x-clinic-key: $CLINIC_KEY" \
  --data-binary @/tmp/ocr-body.json "$BASE/api/ocr"
python3 - <<'PY'
import json
try:
    r = json.load(open('/tmp/ocr-resp.json'))
except Exception as e:
    print('  回應不是 JSON：', e); raise SystemExit(1)
if 'error' in r:
    print('  ✗ 錯誤：', r['error']); raise SystemExit(1)
d = r['data']
print('  性別/生日：', d.get('gender'), d.get('birthDate'))
print('  父/母身高：', d.get('fatherHeight'), d.get('motherHeight'))
for m in d.get('measurements', []):
    print('  ', m.get('measureDate'), m.get('height'), m.get('weight'))
print('  不確定欄位：', d.get('uncertain'))
print('  備註：', d.get('notes'))
print('  token：', r.get('usage'))
print('  預期：male 2019-03-05 / 175 162 / 2024-08-22 112 19 / 2025-08-22 118.5 21.5 / 2026-08-22 123.5 24')
PY
rm -f /tmp/ocr-body.json
