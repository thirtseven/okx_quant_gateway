### OKX GATEWAY ALIASES (auto-generated) ###
# ── OKX Quantitative Gateway 快捷指令 ──
# 纳入 ~/.bashrc: source /path/to/okx_bashrc.sh

export OKX_HOME="${OKX_HOME:-/home/thirty/okx-gateway}"

# ═══════════════════════════════════════════════════════════════
# 内部工具函数
# ═══════════════════════════════════════════════════════════════

_okx_ensure_dir() { cd "$OKX_HOME" 2>/dev/null || { echo "错误: \$OKX_HOME=$OKX_HOME 不存在"; return 1; }; }

_okx_nginx_conf()  { echo "$OKX_HOME/nginx.conf"; }
_okx_nginx_avail() { echo "/etc/nginx/sites-available/okx-gateway"; }
_okx_nginx_enabled(){ echo "/etc/nginx/sites-enabled/okx-gateway"; }

_okx_proxy_file()  { echo "$OKX_HOME/.proxy_env"; }
_okx_backup_dir()  { echo "$OKX_HOME/backups/proxy_rollback_$(date +%Y%m%d_%H%M%S)"; }

# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

okx() {
    case "${1:-help}" in
        proxy)    shift; _okx_proxy "${@:-help}" ;;
        strategy) shift; _okx_strategy "${@:-help}" ;;
        start)    _okx_ensure_dir && ./start.sh start ;;
        stop)     _okx_ensure_dir && ./start.sh stop ;;
        restart)  _okx_ensure_dir && ./start.sh restart ;;
        status)   _okx_ensure_dir && ./start.sh status ;;
        log)      _okx_ensure_dir && ./start.sh log ;;
        test)     _okx_ensure_dir && python3 test_gateway.py ;;
        backtest) _okx_ensure_dir && python3 backtest.py 2>&1 | tail -50 ;;
        docker-up)    _okx_ensure_dir && docker compose up -d ;;
        docker-down)  _okx_ensure_dir && docker compose down ;;
        docker-log)   _okx_ensure_dir && docker compose logs -f ;;
        docker-build) _okx_ensure_dir && docker compose build ;;
        ui)
            _okx_ensure_dir || return
            ip=$(hostname -I | awk '{print $1}')
            echo "  Web UI:   http://$ip:5000"
            echo "  Settings: http://$ip:5000/settings"
            if pgrep -x nginx >/dev/null 2>&1; then
                echo "  Nginx 反代: http://$ip:80 → :5000"
            fi ;;
        creds)
            _okx_ensure_dir && python3 -c "
from credential_manager import get_credential_status
for k,v in get_credential_status().items():
    print(f'  {k}: {\"OK\" if v else \"--\"}')
" ;;
        pairs)
            _okx_ensure_dir && python3 -c "
from state import state
pairs = state.get('trading_pairs', [])
print(f'Trading pairs ({len(pairs)}):')
for p in sorted(pairs):
    print(f'  {p}')
" ;;
        update)
            _okx_ensure_dir && git pull 2>/dev/null && echo "Updated" || echo "Not a git repo / no updates" ;;
        backup)
            _okx_ensure_dir || return
            mkdir -p backups
            tar -czf "backups/okx_backup_$(date +%Y%m%d_%H%M%S).tar.gz" \
                --exclude='__pycache__' --exclude='*.pyc' --exclude='backups' \
                --exclude='data/*.csv' --exclude='data/*.json' . 2>/dev/null
            echo "Backup: backups/okx_backup_$(date +%Y%m%d_%H%M%S).tar.gz" ;;
        help|*)
            echo ""
            echo "  OKX Gateway v2.00"
            echo "  ═════════════════════"
            echo ""
            echo "  运行:"
            echo "    okx start | stop | restart | status | log | ui"
            echo ""
            echo "  Docker:"
            echo "    okx docker-up | docker-down | docker-log | docker-build"
            echo ""
            echo "  代理 (反代 + 出站):"
            echo "    okx proxy ...      代理管理 (setup/status/set/unset)"
            echo ""
            echo "  策略:"
            echo "    okx strategy auto   自动检测行情"
            echo "    okx strategy bull   强制牛市"
            echo "    okx strategy bear   强制熊市"
            echo "    okx strategy range  强制震荡"
            echo "    okx strategy info   当前策略"
            echo "" ;;
    esac
}

# ── 入站反代 ─────────────────────────────────────────────

_proxy_setup() {
    _okx_ensure_dir || return 1

    if ! command -v nginx &>/dev/null; then
        echo "安装 nginx..."
        sudo apt-get update -qq && sudo apt-get install -y nginx
    fi

    local conf=$(_okx_nginx_conf)
    local avail=$(_okx_nginx_avail)
    local enabled=$(_okx_nginx_enabled)

    echo "配置 nginx 反向代理: $conf → $avail"
    sudo cp "$conf" "$avail"
    sudo ln -sf "$avail" "$enabled"
    sudo rm -f /etc/nginx/sites-enabled/default
    sudo nginx -t && sudo nginx -s reload 2>/dev/null || sudo nginx

    local ip=$(hostname -I | awk '{print $1}')
    echo ""
    echo "  反向代理已配置:"
    echo "    http://localhost     → :5000"
    echo "    http://$ip           → :5000"
    echo "  日志: /var/log/nginx/okx_gateway_*.log"
}

_proxy_remove() {
    _okx_ensure_dir || return 1
    sudo rm -f "$(_okx_nginx_enabled)" "$(_okx_nginx_avail)"
    sudo nginx -s reload 2>/dev/null && echo "nginx 反代已移除" || echo "nginx 反代已移除 (nginx 可能未运行)"
}

_proxy_status() {
    echo ""
    echo "  ── nginx 反向代理 ──"
    if pgrep -x nginx >/dev/null 2>&1; then
        echo "  状态:   运行中"
        echo "  配置:   $([ -f "$(_okx_nginx_enabled)" ] && echo '已启用' || echo '未配置')"
        echo "  端口:"
        ss -tlnp 2>/dev/null | grep -E ":80|:5000" | awk '{print "    " $0}' || echo "    (需要 sudo)"
    else
        echo "  状态:   未运行 (okx proxy setup 安装)"
    fi

    echo ""
    echo "  ── 出站 API 代理 ──"
    if [ -f "$(_okx_proxy_file)" ]; then
        echo "  状态:   已设置 → $(cat $(_okx_proxy_file))"
    else
        echo "  状态:   直连 (okx proxy set <url> 设置)"
    fi
}

_proxy_test() {
    _okx_ensure_dir || return 1
    echo "测试反向代理..."
    local ok=0

    echo -n "  Gateway :5000 ... "
    curl -sf --max-time 3 http://localhost:5000/api/health >/dev/null 2>&1 && { echo "OK"; ok=$((ok+1)); } || echo "FAIL"

    echo -n "  Nginx :80    ... "
    curl -sf --max-time 3 http://localhost/api/health >/dev/null 2>&1 && { echo "OK"; ok=$((ok+1)); } || echo "FAIL (run: okx proxy setup)"

    echo ""
    if [ $ok -eq 2 ]; then
        echo "  全部通过: 反代正常工作"
    elif [ $ok -eq 1 ]; then
        echo "  Gateway 在线，反代未配置 → okx proxy setup"
    else
        echo "  Gateway 未运行 → okx start"
    fi
}

# ── 出站代理 ─────────────────────────────────────────────

_proxy_outbound_set() {
    local url="${1:-}"
    if [ -z "$url" ]; then
        echo "用法: okx proxy set <代理地址>"
        echo "示例: okx proxy set http://127.0.0.1:7890"
        echo "      okx proxy set http://host.docker.internal:7890"
        return 1
    fi

    _okx_ensure_dir || return 1

    # 持久化到 .proxy_env
    echo "$url" > "$(_okx_proxy_file)"
    export HTTPS_PROXY="$url"
    export HTTP_PROXY="$url"

    # 写入当前环境同时注入 .env (供 docker compose 读取)
    if [ -f "$OKX_HOME/.env" ]; then
        if grep -q "^HTTPS_PROXY=" "$OKX_HOME/.env" 2>/dev/null; then
            sed -i "s|^HTTPS_PROXY=.*|HTTPS_PROXY=$url|" "$OKX_HOME/.env"
        else
            echo "HTTPS_PROXY=$url" >> "$OKX_HOME/.env"
        fi
    fi

    echo "出站代理: $url"
    echo ""
    echo "  已生效于当前 Shell (子进程自动继承)"
    echo "  Docker: 下次 docker compose up -d 生效"
    echo "  持久化: \$OKX_HOME/.proxy_env"
}

_proxy_outbound_unset() {
    _okx_ensure_dir || return 1

    rm -f "$(_okx_proxy_file)"
    unset HTTPS_PROXY HTTP_PROXY

    if [ -f "$OKX_HOME/.env" ]; then
        sed -i '/^HTTPS_PROXY=/d' "$OKX_HOME/.env"
    fi

    echo "出站代理已取消，恢复直连"
}

_proxy_info() {
    _okx_ensure_dir || return 1
    echo ""
    echo "  ── 出站代理配置 ──"
    echo "  环境变量 HTTPS_PROXY: ${HTTPS_PROXY:-(未设置)}"
    echo "  持久化文件:           $([ -f "$(_okx_proxy_file)" ] && cat "$(_okx_proxy_file)" || echo '(未设置)')"
    echo ""
    echo "  ── Python constants.py ──"
    python3 -c "from constants import PROXY; print(f'  PROXY: {PROXY}')" 2>/dev/null || echo "  (无法读取)"
    echo ""
    echo "  ── 当前 Shell 代理 ──"
    env | grep -i proxy | sort || echo "  (无代理环境变量)"
}

# ── 自定义域名 ───────────────────────────────────────────

_proxy_domain() {
    case "${1:-help}" in
        add)    _domain_add "${2:-}" ;;
        remove) _domain_remove "${2:-}" ;;
        list)   _domain_list ;;
        setup)  _domain_setup ;;
        help|*)
            echo ""
            echo "  okx proxy domain — 本地自定义域名管理"
            echo "  ═══════════════════════════════════════"
            echo ""
            echo "    okx proxy domain setup          配置默认 okx.local"
            echo "    okx proxy domain add <name>     添加自定义域名"
            echo "    okx proxy domain remove <name>  移除自定义域名"
            echo "    okx proxy domain list           列出所有域名"
            echo ""
            echo "  示例:"
            echo "    okx proxy domain setup              → http://okx.local"
            echo "    okx proxy domain add quant.home     → http://quant.home"
            echo "    okx proxy domain remove okx.local" ;;
    esac
}

_domain_setup() {
    _okx_ensure_dir || return 1
    local domain="${1:-okx.local}"
    local wsl_ip=$(hostname -I | awk '{print $1}')

    echo "配置本地域名: $domain"
    echo ""

    # 1. WSL /etc/hosts
    _domain_add "$domain"

    # 2. Windows hosts (浏览器需要)
    _domain_add_windows "$domain"

    # 3. Windows portproxy (转发 :80 → WSL)
    echo "配置 Windows 端口转发 :80 → WSL $wsl_ip:80 ..."
    cmd.exe /c "netsh interface portproxy add v4tov4 listenport=80 listenaddress=0.0.0.0 connectport=80 connectaddress=$wsl_ip" 2>/dev/null
    echo "  [OK] portproxy :80"

    # 4. 部署 nginx
    if [ ! -f "$(_okx_nginx_enabled)" ]; then
        echo "部署 nginx 反代配置..."
        sudo cp "$(_okx_nginx_conf)" "$(_okx_nginx_avail)"
        sudo ln -sf "$(_okx_nginx_avail)" "$(_okx_nginx_enabled)"
        sudo rm -f /etc/nginx/sites-enabled/default
        sudo nginx -t && sudo nginx -s reload 2>/dev/null || sudo nginx
    else
        sudo nginx -t && sudo nginx -s reload 2>/dev/null || sudo nginx
    fi

    # 5. 验证
    echo ""
    echo "  ✅ 自定义域名配置完成"
    echo ""
    echo "  访问地址:"
    echo "    http://$domain        (Windows 浏览器)"
    echo "    http://okx.gateway    (备用)"
    echo "    http://localhost      (传统)"
    echo "    http://$wsl_ip:5000   (直连)"

    echo ""
    echo -n "  测试 http://$domain/api/health ... "
    sleep 1
    if curl -sf --max-time 3 "http://$domain/api/health" >/dev/null 2>&1; then
        echo "OK"
    else
        echo "FAIL — 检查: gateway 是否运行?  → okx start"
    fi
}

# ── Windows hosts 管理 (通过 cmd.exe) ─────────────────────

_win_hosts() { echo "/mnt/c/Windows/System32/drivers/etc/hosts"; }

_domain_add_windows() {
    local domain="${1:-okx.local}"
    local win_hosts=$(_win_hosts)

    if [ ! -f "$win_hosts" ]; then
        echo "  [跳过] 无法访问 Windows hosts (非 WSL 环境)"
        return 0
    fi

    if grep -q "$domain" "$win_hosts" 2>/dev/null; then
        echo "  [已存在] Windows hosts: $domain"
        return 0
    fi

    echo "添加 $domain 到 Windows hosts ..."
    echo "127.0.0.1 $domain okx.gateway  # OKX Gateway" | sudo tee -a "$win_hosts" >/dev/null 2>&1
    echo "  [OK] Windows hosts: $domain → 127.0.0.1"
}

# ── 域名增删查 ───────────────────────────────────────────

_domain_add() {
    local domain="${1:-}"
    [ -z "$domain" ] && { echo "用法: okx proxy domain add <域名>"; return 1; }
    if grep -q "$domain" /etc/hosts 2>/dev/null; then
        echo "  [已存在] WSL hosts: $domain"
        return 0
    fi
    echo "127.0.0.1 $domain okx.gateway  # OKX Gateway" | sudo tee -a /etc/hosts >/dev/null
    echo "  [OK] WSL hosts: $domain → 127.0.0.1"
}

_domain_remove() {
    local domain="${1:-}"
    [ -z "$domain" ] && { echo "用法: okx proxy domain remove <域名>"; return 1; }
    sudo sed -i "/$domain.*OKX Gateway/d" /etc/hosts 2>/dev/null
    sudo sed -i "/$domain/d" /etc/hosts 2>/dev/null
    echo "  [OK] 已移除: $domain"

    local win_hosts="/mnt/c/Windows/System32/drivers/etc/hosts"
    [ -f "$win_hosts" ] && sudo sed -i "/$domain/d" "$win_hosts" 2>/dev/null
}

_domain_list() {
    echo ""
    echo "  ── 本地自定义域名 ──"
    echo ""
    echo "  WSL /etc/hosts:"
    grep "OKX Gateway" /etc/hosts 2>/dev/null | sed 's/^/    /' || echo "    (无)"
    echo ""
    local win_hosts="/mnt/c/Windows/System32/drivers/etc/hosts"
    echo "  Windows hosts:"
    if [ -f "$win_hosts" ]; then
        grep "OKX Gateway" "$win_hosts" 2>/dev/null | sed 's/^/    /' || echo "    (无)"
    else
        echo "    (非 WSL 环境)"
    fi
    echo ""
    echo "  ── DNS 解析测试 ──"
    for domain in okx.local okx.gateway; do
        if getent hosts "$domain" &>/dev/null; then
            echo "    $domain → $(getent hosts $domain | awk '{print $1}')"
        fi
    done
}

# ── Windows 端口转发 ─────────────────────────────────────

_proxy_portfw() {
    local wsl_ip=$(hostname -I | awk '{print $1}')
    local ports="${*:-80 5000}"

    case "${1:-setup}" in
        setup|add)
            for port in $ports; do
                case "$port" in
                    ''|*[!0-9]*) continue ;;  # skip non-numeric
                esac
                echo "添加端口转发: Windows :$port → WSL $wsl_ip:$port"
                cmd.exe /c "netsh interface portproxy add v4tov4 listenport=$port listenaddress=0.0.0.0 connectport=$port connectaddress=$wsl_ip" 2>/dev/null
            done
            echo ""
            echo "当前转发:"
            cmd.exe /c "netsh interface portproxy show v4tov4" 2>/dev/null | grep -E "Port|$wsl_ip" || echo "  (无)" ;;
        remove)
            for port in $ports; do
                case "$port" in
                    ''|*[!0-9]*) continue ;;
                esac
                echo "移除端口转发: $port"
                cmd.exe /c "netsh interface portproxy delete v4tov4 listenport=$port listenaddress=0.0.0.0" 2>/dev/null
            done ;;
        status|show)
            cmd.exe /c "netsh interface portproxy show v4tov4" 2>/dev/null ;;
        help|*)
            echo "  okx proxy portfw setup          配置 :80 + :5000 转发"
            echo "  okx proxy portfw add <port>     添加单个端口"
            echo "  okx proxy portfw remove <port>  移除端口"
            echo "  okx proxy portfw status         查看转发状态" ;;
    esac
}

# ── 回滚 ─────────────────────────────────────────────────

_proxy_rollback() {
    _okx_ensure_dir || return 1

    local backup_dir="$OKX_HOME/backups/proxy_rollback_20260517"
    if [ ! -d "$backup_dir" ]; then
        echo "错误: 未找到备份 $backup_dir"
        echo "手动回滚:"
        echo "  1. okx proxy remove          (移除 nginx 反代)"
        echo "  2. okx proxy unset           (取消出站代理)"
        return 1
    fi

    echo "回滚到: $backup_dir"
    echo "将恢复: okx_bashrc.sh, setup_nginx.sh, nginx.conf"
    echo -n "确认? [y/N] "
    read -r confirm
    case "$confirm" in
        [yY]|[yY][eE][sS])
            cp "$backup_dir/okx_bashrc.sh"    "$OKX_HOME/okx_bashrc.sh"
            cp "$backup_dir/setup_nginx.sh"   "$OKX_HOME/setup_nginx.sh"
            cp "$backup_dir/nginx.conf"       "$OKX_HOME/nginx.conf"
            _proxy_outbound_unset
            _proxy_remove
            echo "回滚完成。source ~/.bashrc 或重开终端生效"
            ;;
        *)
            echo "已取消" ;;
    esac
}

# ── 策略切换 ─────────────────────────────────────────────────

_okx_strategy() {
    _okx_ensure_dir || return 1
    case "${1:-info}" in
        auto|bull|bear|range)
            python3 -c "
from strategy_manager import set_regime_mode, get_params_summary
if set_regime_mode('${1}'):
    s = get_params_summary()
    print(f'Regime: {s[\"regime_mode\"]}')
    for r in ('bull', 'bear', 'range'):
        print(f'  {r}: score_min={s[r][\"score_min\"]}')
else:
    print('Invalid regime')
" ;;
        info)
            python3 -c "
from strategy_manager import get_params_summary
s = get_params_summary()
print(f'V262 — WR={s[\"win_rate\"]:.1f}% Sharpe={s[\"sharpe\"]:.1f}')
print(f'Regime mode: {s[\"regime_mode\"]}')
for r in ('bull', 'bear', 'range'):
    print(f'  {r}: score_min={s[r][\"score_min\"]} atr_sl={s[r][\"atr_sl\"]} atr_tp={s[r][\"atr_tp\"]}')
" ;;
        *)
            echo "  V262 Regime Switching"
            echo "  ═════════════════════"
            echo ""
            echo "  okx strategy auto    自动检测行情"
            echo "  okx strategy bull    强制牛市模式"
            echo "  okx strategy bear    强制熊市模式"
            echo "  okx strategy range   强制震荡模式"
            echo "  okx strategy info    查看当前策略" ;;
    esac
}
