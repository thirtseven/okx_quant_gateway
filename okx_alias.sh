#!/bin/bash
# OKX 量化网关全局快捷指令
# 用法: okx {start|stop|restart|status|log|tmux|port-forward|settings|nginx|test|backup}

OKX_DIR="/mnt/c/Users/Lenovo/okx_b_v2"

okx() {
    case "$1" in
        start)
            cd "$OKX_DIR" && ./start.sh start
            ;;
        stop)
            cd "$OKX_DIR" && ./start.sh stop
            ;;
        restart)
            cd "$OKX_DIR" && ./start.sh restart
            ;;
        status)
            cd "$OKX_DIR" && ./start.sh status
            ;;
        log|tmux)
            cd "$OKX_DIR" && ./start.sh log
            ;;
        port-forward|pf)
            cd "$OKX_DIR" && ./start.sh port-forward
            ;;
        settings)
            local ip=$(hostname -I | awk '{print $1}')
            echo "设置页面:"
            echo "  http://$ip:5000/settings"
            ;;
        open)
            local ip=$(hostname -I | awk '{print $1}')
            cmd.exe /c "start http://$ip:5000" 2>/dev/null || echo "请手动打开: http://$ip:5000"
            ;;
        open-settings)
            local ip=$(hostname -I | awk '{print $1}')
            cmd.exe /c "start http://$ip:5000/settings" 2>/dev/null || echo "请手动打开: http://$ip:5000/settings"
            ;;
        nginx)
            case "$2" in
                setup) cd "$OKX_DIR" && ./setup_nginx.sh setup ;;
                status) cd "$OKX_DIR" && ./setup_nginx.sh status ;;
                remove) cd "$OKX_DIR" && ./setup_nginx.sh remove ;;
                *) echo "Usage: okx nginx {setup|status|remove}" ;;
            esac
            ;;
        url)
            local ip=$(hostname -I | awk '{print $1}')
            echo "网关地址:"
            echo "  http://okx.local:8080"
            echo "  http://$ip:8080"
            echo "  http://$ip:5000"
            ;;
        test)
            cd "$OKX_DIR" && python3 test_gateway.py 2>&1 | tail -30
            ;;
        backtest)
            cd "$OKX_DIR" && python3 backtest.py 2>&1 | tail -50
            ;;
        backup)
            local backup_dir="$OKX_DIR/backups"
            local timestamp=$(date +%Y%m%d_%H%M%S)
            mkdir -p "$backup_dir"
            tar -czf "$backup_dir/okx_backup_$timestamp.tar.gz" -C "$OKX_DIR" data/ strategy_params.json 2>/dev/null
            echo "Backup: $backup_dir/okx_backup_$timestamp.tar.gz"
            ;;
        network)
            cd "$OKX_DIR" && python3 -c "
from network_manager import NetworkManager
nm = NetworkManager()
status = nm.get_status()
print('Network:')
for k, v in status.items():
    print(f'  {k}: {v}')
"
            ;;
        creds)
            cd "$OKX_DIR" && python3 -c "
from credential_manager import get_credential_status
status = get_credential_status()
print('Credentials:')
for k, v in status.items():
    print(f'  {k}: {\"OK\" if v else \"--\"}')
"
            ;;
        help|*)
            echo "OKX Gateway Manager"
            echo ""
            echo "Usage: okx {command}"
            echo ""
            echo "Commands:"
            echo "  start          Start gateway (tmux persistent)"
            echo "  stop           Stop gateway"
            echo "  restart        Restart gateway"
            echo "  status         Show gateway status"
            echo "  log            Attach to logs (Ctrl+B D to detach)"
            echo "  open           Open gateway in Windows browser"
            echo "  open-settings  Open settings page in Windows browser"
            echo "  port-forward   Configure Windows port forwarding"
            echo "  settings       Show settings page URLs"
            echo "  url            Show all gateway URLs"
            echo "  nginx          Nginx reverse proxy management"
            echo "  test           Run tests"
            echo "  backtest       Run backtest"
            echo "  backup         Backup data"
            echo "  network        Show network status"
            echo "  creds          Show credential status"
            echo ""
            echo "Shorthands: pf=port-forward, tmux=log"
            echo ""
            echo "Examples:"
            echo "  okx start        # Start gateway"
            echo "  okx status       # Check status"
            echo "  okx open         # Open in browser"
            echo "  okx url          # Show all URLs"
            echo "  okx pf           # Setup port forwarding"
            ;;
    esac
}

export -f okx
