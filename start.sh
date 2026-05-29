#!/bin/bash
# OKX 量化网关 - 快捷启动脚本 (tmux 持久化)
# 用法: ./start.sh [start|stop|restart|status|log|port-forward|network]

PROJECT_DIR="/mnt/c/Users/Lenovo/Desktop/okx_b_v2"
TMUX_SESSION="okx-gateway"
ENGINE_SESSION="okx-engine"
LOG_FILE="/tmp/okx_gateway.log"
ENGINE_LOG="/tmp/okx_engine.log"
NETWORK_INTERFACE="okx-gw0"
NETWORK_SUBNET="10.99.0.0/24"
NETWORK_IP="10.99.0.1"

create_network() {
    echo "🌐 创建网络子网..."
    PARENT_IF=$(ip route show default | awk '{print $5}' | head -1)
    [ -z "$PARENT_IF" ] && PARENT_IF="eth0" && echo "⚠️  未检测到默认接口，使用 $PARENT_IF"
    if ip link show "$NETWORK_INTERFACE" &>/dev/null; then
        echo "🔄 删除已存在的接口 $NETWORK_INTERFACE"
        ip link set "$NETWORK_INTERFACE" down 2>/dev/null
        ip link delete "$NETWORK_INTERFACE" 2>/dev/null
    fi
    ip link add link "$PARENT_IF" name "$NETWORK_INTERFACE" type macvlan mode bridge || { echo "❌ 创建网络接口失败"; return 1; }
    ip addr add "$NETWORK_IP/24" dev "$NETWORK_INTERFACE"
    ip link set "$NETWORK_INTERFACE" up
    echo "✅ 网络子网已创建  接口: $NETWORK_INTERFACE  IP: $NETWORK_IP"
}

cleanup_network() {
    if ip link show "$NETWORK_INTERFACE" &>/dev/null; then
        echo "🧹 清理网络接口..."
        ip link set "$NETWORK_INTERFACE" down 2>/dev/null
        ip link delete "$NETWORK_INTERFACE" 2>/dev/null
        echo "✅ 已清理"
    fi
}

start() {
    tmux has-session -t "$TMUX_SESSION" 2>/dev/null && { echo "⚠️  服务器已在运行中 (tmux session: $TMUX_SESSION)"; status; return 1; }

    echo "🚀 启动 OKX 量化网关..."
    cd "$PROJECT_DIR" || { echo "❌ 项目目录不存在: $PROJECT_DIR"; return 1; }
    echo "📝 启动模式: 无凭证 (可通过 Web 界面配置)"

    # Start signal engine first
    tmux has-session -t "$ENGINE_SESSION" 2>/dev/null || {
        tmux new-session -d -s "$ENGINE_SESSION" "python3 engine.py 2>&1 | tee $ENGINE_LOG"
        echo "🔧 信号引擎已启动 (port 5001)"
    }

    # 使用 tmux 保证进程持久化
    tmux new-session -d -s "$TMUX_SESSION" "python3 okx_quant_gateway.py 2>&1 | tee $LOG_FILE"
    
    echo "⏳ 等待服务就绪..."
    WSL_IP=$(hostname -I | awk '{print $1}')
    for i in $(seq 1 60); do
        if ss -tln | grep -q 5000; then
            curl -s --max-time 2 http://$WSL_IP:5000/api/health >/dev/null 2>&1 && break
        fi
        sleep 1
    done
    if ss -tln | grep -q 5000; then
            echo "✅ 服务已启动"
            echo ""
            echo "📊 本地访问:    http://$WSL_IP:5000"
            echo "🔗 WSL IP:     http://$WSL_IP:5000"
            echo ""
            echo "⚠️  Windows 访问需配置端口转发:"
            echo "   方法 A: 运行 setup_wsl_portforward.bat (管理员)"
            echo "   方法 B: $0 port-forward"
            echo ""
            echo "🔧 设置页面:   http://$WSL_IP:5000/settings"
            echo "📝 日志: tmux attach -t $TMUX_SESSION"
            echo "🔑 PID: $(pgrep -f 'python3.*okx_quant_gateway' | head -1)"
            return 0
    fi
    
    echo "❌ 启动超时，查看日志: tmux capture-pane -t $TMUX_SESSION -p -S -50"
    return 1
}

stop() {
    if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
        echo "🛑 停止服务器..."
        tmux send-keys -t "$TMUX_SESSION" C-c
        sleep 3
        tmux kill-session -t "$TMUX_SESSION" 2>/dev/null
        pkill -f "python3.*okx_quant_gateway" 2>/dev/null
        echo "✅ 服务器已停止"
    else
        pkill -f "python3.*okx_quant_gateway" 2>/dev/null && echo "✅ 服务器已停止" || echo "ℹ️  服务器未运行"
    fi
}

restart() { echo "🔄 重启..."; stop; sleep 2; start; }

status() {
    HOST_IP=$(hostname -I | awk '{print $1}')
    if tmux has-session -t "$TMUX_SESSION" 2>/dev/null && ss -tln | grep -q 5000; then
        PID=$(pgrep -f 'python3.*okx_quant_gateway' | head -1)
        echo "✅ 服务器运行中 (tmux: $TMUX_SESSION)"
        echo "🔑 PID: $PID"
        echo "📊 本地访问: http://$HOST_IP:5000"
        echo "🔗 WSL IP:  http://$HOST_IP:5000"
        echo "🔧 设置页面: http://$HOST_IP:5000/settings"
        
        HEALTH=$(curl -s --max-time 3 http://$HOST_IP:5000/api/health 2>/dev/null)
        [ -n "$HEALTH" ] && echo "💚 健康状态: $HEALTH" || echo "⚠️  健康检查: 无响应"
        
        CREDS=$(curl -s --max-time 3 http://$HOST_IP:5000/api/credentials/status 2>/dev/null)
        [ -n "$CREDS" ] && echo "🔐 凭证状态: $CREDS"
        
        echo "📝 查看实时日志: tmux attach -t $TMUX_SESSION"
    elif ss -tln | grep -q 5000; then
        echo "⚠️  端口 5000 已绑定，但 tmux session 不存在 (进程可能由其他方式启动)"
    else
        echo "❌ 服务器未运行"
    fi
}

log() {
    if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
        tmux attach -t "$TMUX_SESSION"
    else
        echo "❌ tmux session '$TMUX_SESSION' 不存在"
        echo "   启动: $0 start"
    fi
}

port_forward() {
    echo "🔌 配置 Windows 端口转发..."
    WSL_IP=$(hostname -I | awk '{print $1}')
    echo "   WSL IP: $WSL_IP"
    echo "   端口: 5000 -> $WSL_IP:5000"
    echo ""
    
    if command -v cmd.exe &>/dev/null; then
        # 从 WSL 尝试配置端口转发 (需要 Windows 管理员权限)
        echo "   正在尝试通过 cmd.exe 配置..."
        cmd.exe /c "netsh interface portproxy delete v4tov4 listenport=5000 listenaddress=0.0.0.0 >nul 2>&1"
        cmd.exe /c "netsh interface portproxy add v4tov4 listenport=5000 listenaddress=0.0.0.0 connectport=5000 connectaddress=$WSL_IP"
        if [ $? -eq 0 ]; then
            echo "   ✅ 端口转发已配置"
            cmd.exe /c "netsh advfirewall firewall add rule name='OKX Gateway' dir=in action=allow protocol=TCP localport=5000 >nul 2>&1"
            echo "   ✅ 防火墙规则已添加"
            echo ""
            echo "   访问: http://localhost:5000"
            return 0
        else
            echo "   ❌ 配置失败 (需要管理员权限)"
        fi
    fi
    
    echo ""
    echo "   ⚠️  请在 Windows 中以管理员身份运行:"
    echo "   ========================================"
    echo "   setup_wsl_portforward.bat"
    echo "   ========================================"
    echo ""
    echo "   或手动执行:"
    echo "   netsh interface portproxy add v4tov4"
    echo "        listenport=5000 listenaddress=0.0.0.0"
    echo "        connectport=5000 connectaddress=$WSL_IP"
    return 1
}

case "$1" in
    start) start ;;
    stop) stop ;;
    restart) restart ;;
    status) status ;;
    log) log ;;
    port-forward|portforward) port_forward ;;
    install-service)
        echo "Installing systemd service..."
        sudo cp "$PROJECT_DIR/okx-gateway.service" /etc/systemd/system/
        sudo systemctl daemon-reload
        sudo systemctl enable okx-gateway
        echo "Service installed. Use: sudo systemctl start okx-gateway"
        ;;
    network)
        case "$2" in
            create) create_network ;;
            cleanup) cleanup_network ;;
            *) echo "用法: $0 network {create|cleanup}" ;;
        esac
        ;;
    *)
        echo "OKX 量化网关管理脚本 (tmux 持久化)"
        echo ""
        echo "用法: $0 {start|stop|restart|status|log|port-forward|install-service|network}"
        echo ""
        echo "  start            - 启动服务器 (tmux 持久化)"
        echo "  stop             - 停止服务器"
        echo "  restart          - 重启服务器"
        echo "  status           - 查看服务器状态"
        echo "  log              - 查看实时日志 (Ctrl+B, D 脱离)"
        echo "  port-forward     - 配置 Windows 端口转发"
        echo "  install-service  - 安装 systemd 服务 (开机自启)"
        echo "  network          - 网络管理 (create|cleanup)"
        echo ""
        echo "访问地址: http://localhost:5000"
        echo "设置页面: http://localhost:5000/settings"
        ;;
esac
