#!/bin/bash
# OKX 量化网关 - 快捷重启脚本
# 用法: ./restart.sh

PROJECT_DIR="/mnt/c/Users/Lenovo/Desktop/okx_b_v2"

echo "🔄 重启 OKX 量化网关..."
"$PROJECT_DIR/start.sh" restart
