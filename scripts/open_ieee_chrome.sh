#!/usr/bin/env bash
# Abre Chrome para IEEE Xplore — queda vivo aunque cierres Cursor o termine el chat.
# Uso: ./scripts/open_ieee_chrome.sh

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="$ROOT/data/ieee_chrome_cdp"
PORT=9222
LOG="/tmp/lcda_ieee_chrome.log"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

mkdir -p "$PROFILE"

if curl -sf "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
  echo "Chrome IEEE ya está corriendo (puerto $PORT)."
  echo "Si no ves la ventana, búscala en el Dock o usa Cmd+Tab."
  exit 0
fi

nohup "$CHROME" \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE" \
  "https://ieeexplore.ieee.org" \
  >>"$LOG" 2>&1 &

disown -h $! 2>/dev/null || true

sleep 2
if curl -sf "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
  echo "Chrome IEEE abierto (puerto $PORT). Log: $LOG"
  echo "Inicia sesión UdeC y deja esta ventana abierta."
else
  echo "No se pudo verificar Chrome. Revisa: $LOG"
  exit 1
fi
