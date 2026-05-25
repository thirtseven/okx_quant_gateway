#!/bin/bash
# OKX Gateway - WSL NAT 端口转发配置
# 用法: ./setup_portforward.sh

echo "🔧 配置 WSL NAT 端口转发..."
echo ""

# 获取 WSL IP
WSL_IP=$(hostname -I | awk '{print $1}')
echo "WSL IP: $WSL_IP"

# 检查网关是否运行
if ! ss -tlnp | grep -q :5000; then
    echo "❌ 网关未运行，请先启动: ./start.sh start"
    exit 1
fi

echo ""
echo "请在 Windows 中以管理员身份运行以下命令:"
echo ""
echo "  netsh interface portproxy add v4tov4 listenport=5000 listenaddress=0.0.0.0 connectport=5000 connectaddress=$WSL_IP"
echo ""
echo "或运行脚本:"
echo "  setup_wsl_portforward.bat"
echo ""
echo "配置完成后访问: http://localhost:5000"
