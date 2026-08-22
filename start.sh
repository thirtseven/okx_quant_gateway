#!/usr/bin/env bash
# Quant Trader Gateway - one-command launcher
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUANT_DIR="$DIR"
PORT="${QUANT_PORT:-8321}"

cd "$QUANT_DIR"

if ! python3 -c "import fastapi, uvicorn, requests, pandas, numpy" 2>/dev/null; then
  echo "[quant-trader] installing dependencies..."
  pip3 install --user --quiet fastapi uvicorn requests pandas numpy
fi

echo "[quant-trader] starting gateway on http://localhost:${PORT}"
echo "[quant-trader] dashboard: http://localhost:${PORT}/"
exec python3 -m uvicorn gateway:app --host 127.0.0.1 --port "${PORT}"
