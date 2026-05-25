#!/bin/bash
#
# OKX Quant Gateway - tmux Persistent Runner
# Keeps the gateway running even after terminal closes
#

set -e

SESSION_NAME="okx-gateway"
GATEWAY_DIR="/mnt/c/Users/Lenovo/Desktop/okx_b_v2"
PROXY=${HTTPS_PROXY:-http://172.26.0.1:7890}

# Export proxy env vars
export HTTPS_PROXY="$PROXY"
export HTTP_PROXY="$PROXY"
export https_proxy="$PROXY"
export http_proxy="$PROXY"

WSL_IP=$(hostname -I | awk '{print $1}')

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

usage() {
    echo "Usage: $0 {start|stop|restart|status|attach|logs}"
    echo ""
    echo "  start   - Start gateway in tmux session (or attach if already running)"
    echo "  stop    - Stop the gateway"
    echo "  restart - Restart the gateway"
    echo "  status  - Check if gateway is running"
    echo "  attach  - Attach to gateway tmux session (see output)"
    echo "  logs    - Show recent gateway logs"
    exit 1
}

is_running() {
    tmux has-session -t "$SESSION_NAME" 2>/dev/null
}

get_status() {
    if is_running; then
        PID=$(pgrep -f "python3.*okx_quant_gateway" 2>/dev/null | head -1)
        if [ -n "$PID" ]; then
            echo -e "${GREEN}✓${NC} Gateway running (PID: $PID, Session: $SESSION_NAME)"
            echo "  Dashboard: http://$WSL_IP:5000"
        else
            echo -e "${YELLOW}△${NC} Session exists but process not found"
        fi
    else
        echo -e "${RED}✗${NC} Gateway not running"
    fi
}

cmd_start() {
    if is_running; then
        echo -e "${YELLOW}Gateway already running in tmux session '$SESSION_NAME'${NC}"
        echo "  Dashboard: http://$WSL_IP:5000"
        echo ""
        echo "Use '$0 attach' to view output, or '$0 restart' to restart"
        return 0
    fi

    echo "Starting OKX Quant Gateway in tmux session '$SESSION_NAME'..."

    # Kill any existing python processes on port 5000
    fuser -k 5000/tcp 2>/dev/null || true
    sleep 1

    # Create detached tmux session using wrapper script (ensures proxy env vars are set)
    tmux new-session -d -s "$SESSION_NAME" "$GATEWAY_DIR/run_gateway.sh"

    sleep 2

    if is_running; then
        echo -e "${GREEN}✓${NC} Gateway started successfully"
        echo "  Session: $SESSION_NAME"
        echo "  Dashboard: http://$WSL_IP:5000"
        echo ""
        echo "View output: tmux attach -t $SESSION_NAME"
        echo "Detach: Ctrl+B then D"
        echo "Stop: $0 stop"
    else
        echo -e "${RED}✗${NC} Failed to start gateway"
        exit 1
    fi
}

cmd_stop() {
    if ! is_running; then
        echo "Gateway not running"
        return 0
    fi

    echo "Stopping gateway..."
    # Kill the python process inside tmux
    tmux send-keys -t "$SESSION_NAME" C-c
    sleep 2

    # Kill any remaining process
    pkill -f "python3.*okx_quant_gateway" 2>/dev/null || true
    fuser -k 5000/tcp 2>/dev/null || true

    # Kill the tmux session
    tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true

    echo -e "${GREEN}✓${NC} Gateway stopped"
}

cmd_restart() {
    cmd_stop
    sleep 2
    cmd_start
}

cmd_attach() {
    if ! is_running; then
        echo -e "${RED}Gateway not running. Start with: $0 start${NC}"
        exit 1
    fi
    echo "Attaching to tmux session '$SESSION_NAME'..."
    echo "Detach with: Ctrl+B then D"
    sleep 1
    tmux attach -t "$SESSION_NAME"
}

cmd_logs() {
    if ! is_running; then
        echo -e "${RED}Gateway not running${NC}"
        return 0
    fi
    tmux capture-pane -t "$SESSION_NAME" -p | tail -50
}

# Main
case "${1:-}" in
    start)
        cmd_start
        ;;
    stop)
        cmd_stop
        ;;
    restart)
        cmd_restart
        ;;
    status)
        get_status
        ;;
    attach)
        cmd_attach
        ;;
    logs)
        cmd_logs
        ;;
    *)
        usage
        ;;
esac