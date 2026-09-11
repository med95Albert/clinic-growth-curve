#!/usr/bin/env bash
# 生長曲線判讀伺服器 — macOS（Mac mini 專用機）一鍵安裝
#
#   bash macos-install.sh                 # 裝到 ~/growth，並以 LaunchDaemon 常駐（需 sudo）
#   bash macos-install.sh --no-daemon     # 只裝不常駐（遠端 Claude Code 跑這個：不需要 sudo）
#   sudo bash macos-install.sh --daemon-only  # 只做常駐設定（人自己敲一次，補上 sudo 那段）
#   bash macos-install.sh --prefix DIR    # 換安裝目錄
#
# 可重複執行：已裝的部分會跳過或更新（git pull）。全程不需要 Homebrew：Python 由 uv 下載。
set -euo pipefail

REPO="https://github.com/med95Albert/clinic-growth-curve"
PREFIX="$HOME/growth"
PORT="${PORT:-8790}"
DAEMON=1
DAEMON_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --no-daemon) DAEMON=0 ;;
    --daemon-only) DAEMON_ONLY=1 ;;
    --prefix=*) PREFIX="${1#--prefix=}" ;;
    --prefix) shift; PREFIX="${1:?--prefix 需要目錄}" ;;
    *) echo "未知參數：$1" >&2; exit 2 ;;
  esac
  shift
done
APP="$PREFIX/clinic-growth-curve"
LABEL="com.clinic.growth-curve"
PLIST="/Library/LaunchDaemons/$LABEL.plist"
LOGDIR="$PREFIX/logs"

step() { printf '\n== %s ==\n' "$1"; }
fail() { printf '✗ %s\n' "$1" >&2; exit 1; }

if [ "$DAEMON_ONLY" = "1" ]; then
  [ -x "$APP/server/.venv/bin/python" ] || fail "找不到 $APP/server/.venv，請先跑不帶 --daemon-only 的安裝"
  # sudo 執行時 $HOME 會變成 /var/root；用 SUDO_USER 找回真正的安裝者
  [ -n "${SUDO_USER:-}" ] && PREFIX="$(eval echo "~$SUDO_USER")/growth" && APP="$PREFIX/clinic-growth-curve" && LOGDIR="$PREFIX/logs"
fi
if [ "$DAEMON_ONLY" != "1" ]; then
step "1/6 uv（Python 管理器）"
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || fail "uv 安裝失敗（需要網路）"
fi
export PATH="$HOME/.local/bin:$PATH"
uv --version || fail "uv 不可用"

step "2/6 取得程式（$APP）"
mkdir -p "$PREFIX" "$LOGDIR"
if [ -d "$APP/.git" ]; then
  git -C "$APP" pull -q --ff-only || fail "git pull 失敗"
else
  git clone -q "$REPO" "$APP" || fail "git clone 失敗（需要網路；若 repo 已改私有，先執行 gh auth login）"
fi
[ -f "$APP/server/run.py" ] || fail "找不到 server/run.py"
[ -f "$APP/form/template.json" ] || fail "找不到 form/template.json"

step "3/6 Python 3.12 虛擬環境與套件"
cd "$APP/server"
[ -d .venv ] || uv venv -q --python 3.12 .venv
# 只裝執行期套件：requirements 用 TEST-ONLY-BELOW 標記分隔
sed '/TEST-ONLY-BELOW/,$d' requirements.txt > /tmp/growth-req.txt
uv pip install -q --python .venv/bin/python -r /tmp/growth-req.txt || fail "套件安裝失敗"
.venv/bin/python -c "import fastapi, rapidocr, onnxruntime, cv2, PIL; print('deps OK')"

step "4/6 下載 OCR 模型並自我測試（合成表單必須每格 100%）"
.venv/bin/python -c "from growth_ocr.recognizers.base import get_recognizer; get_recognizer('rapidocr').warmup(); print('models OK')" 2>&1 | grep -v "INFO\|RapidOCR" || true
SYN="$(mktemp -d)/synth"
.venv/bin/python -m growth_ocr.synth "$SYN" --count 3 --seed 1 >/dev/null 2>&1
.venv/bin/python -m growth_ocr.bench "$SYN" --labels "$SYN/labels.csv" --backend rapidocr 2>&1 | grep -E "每格準確率|沉默錯誤數|每張耗時" || fail "自我測試失敗"
.venv/bin/python -m growth_ocr.bench "$SYN" --labels "$SYN/labels.csv" --backend rapidocr >/dev/null 2>&1 || fail "自我測試未達標（每格必須 100%、沉默錯誤 0）"

fi  # DAEMON_ONLY
step "5/6 常駐（LaunchDaemon，開機即啟動、當掉自動重啟）"
if [ "$DAEMON" = "1" ]; then
  USER_NAME="${SUDO_USER:-$(id -un)}"
  TMP_PLIST="$(mktemp)"
  cat > "$TMP_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$APP/server/.venv/bin/python</string>
    <string>$APP/server/run.py</string>
  </array>
  <key>WorkingDirectory</key><string>$APP/server</string>
  <key>UserName</key><string>$USER_NAME</string>
  <key>EnvironmentVariables</key><dict>
    <key>PORT</key><string>$PORT</string>
    <key>OCR_CONF_THRESHOLD</key><string>0.85</string>
    <key>PILOT_DIR</key><string></string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOGDIR/server.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/server.log</string>
</dict></plist>
EOF
  echo "需要 sudo 寫入 $PLIST 並載入（會問一次密碼）"
  sudo cp "$TMP_PLIST" "$PLIST" && sudo chown root:wheel "$PLIST" && sudo chmod 644 "$PLIST"
  sudo launchctl bootout system "$PLIST" >/dev/null 2>&1 || true
  sudo launchctl bootstrap system "$PLIST" || fail "launchctl 載入失敗"
  echo "已載入 $LABEL"
else
  echo "（--no-daemon：略過常駐設定）"
fi

step "6/6 驗證"
if [ "$DAEMON" = "1" ]; then
  for i in $(seq 1 20); do
    if curl -s -m 3 "http://127.0.0.1:$PORT/api/health" | grep -q '"ok":true'; then
      echo "健康檢查通過：$(curl -s "http://127.0.0.1:$PORT/api/health")"; break
    fi
    sleep 3
    [ "$i" = 20 ] && fail "服務沒有起來，看 $LOGDIR/server.log"
  done
  IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo '<這台的IP>')"
  HOST="$(scutil --get LocalHostName 2>/dev/null || hostname -s)"
  echo
  echo "手機（同一個 Wi-Fi）開："
  echo "  http://$IP:$PORT/           或  http://$HOST.local:$PORT/"
  echo "  http://$IP:$PORT/qr.html    ← 在診間電腦開，手機掃 QR 加入主畫面"
  echo "留底（試用模式）：編輯 $PLIST 的 PILOT_DIR 後 sudo launchctl kickstart -k system/$LABEL"
else
  echo "手動啟動：cd $APP/server && PORT=$PORT .venv/bin/python run.py"
fi
