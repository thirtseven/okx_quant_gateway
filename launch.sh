#!/usr/bin/env bash
# Launch quant gateway detached
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1
nohup python3 -m uvicorn gateway:app --host 127.0.0.1 --port 8321 \
  > /tmp/quant-gateway.log 2>&1 &
echo $! > /tmp/quant-gateway.pid
echo "gateway pid=$(cat /tmp/quant-gateway.pid)"
