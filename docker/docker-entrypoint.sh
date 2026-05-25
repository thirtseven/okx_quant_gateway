#!/bin/bash
# Docker entrypoint for OKX Quant Gateway
# 1. Wait for MySQL to be ready
# 2. Ensure .env creds are loaded
# 3. Start the gateway

set -e

echo "=== OKX Gateway Docker Entrypoint ==="

# Wait for MySQL (non-critical — gateway handles DB failures gracefully)
if [ -n "$DB_HOST" ]; then
    echo "Waiting for MySQL at $DB_HOST:${DB_PORT:-3306}..."
    for i in $(seq 1 30); do
        if python3 -c "
import pymysql
try:
    conn = pymysql.connect(
        host='$DB_HOST', port=${DB_PORT:-3306},
        user='${DB_USER:-okx_trader}',
        password='${DB_PASSWORD:?DB_PASSWORD not set}',
        database='${DB_NAME:-okx_gateway}',
        connect_timeout=3
    )
    conn.close()
    exit(0)
except:
    exit(1)
" 2>/dev/null; then
            echo "MySQL ready (attempt $i)"
            break
        fi
        echo "  attempt $i/30..."
        sleep 3
    done
fi

echo "Starting OKX Quant Gateway..."
exec python3 okx_quant_gateway.py
