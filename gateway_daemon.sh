#!/usr/bin/env bash
# Robust gateway daemon - survives bash session teardown
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec setsid nohup python3 -m uvicorn gateway:app \
  --app-dir "$DIR" \
  --host 127.0.0.1 --port 8321 \
  > /tmp/quant-gateway.log 2>&1 < /dev/null &
echo $!
