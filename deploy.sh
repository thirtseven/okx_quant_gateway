#!/bin/bash
# OKX Gateway - 一键部署脚本
# 用法: ./deploy.sh [start|stop|status|setup-hosts]

set -e

PROJECT_DIR="/mnt/c/Users/Lenovo/Desktop/okx_b_v2"
NGINX_CONF="$PROJECT_DIR/nginx.conf"
GATEWAY_HOST="127.0.0.1"
GATEWAY_PORT="5000"
NGINX_PORT="8080"
DOMAIN="okx.local"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 检查服务状态
check_status() {
    echo "=== 服务状态 ==="
    
    # 检查网关
    if curl -s --noproxy '*' --max-time 3 "http://$GATEWAY_HOST:$GATEWAY_PORT/api/health" > /dev/null 2>&1; then
        echo -e "网关 ($GATEWAY_PORT): ${GREEN}运行中${NC}"
    else
        echo -e "网关 ($GATEWAY_PORT): ${RED}未运行${NC}"
    fi
    
    # 检查nginx
    if curl -s --noproxy '*' --max-time 3 "http://127.0.0.1:$NGINX_PORT/api/health" > /dev/null 2>&1; then
        echo -e "nginx ($NGINX_PORT): ${GREEN}运行中${NC}"
    else
        echo -e "nginx ($NGINX_PORT): ${RED}未运行${NC}"
    fi
    
    echo ""
    echo "=== 访问地址 ==="
    echo "本地访问: http://localhost:$NGINX_PORT"
    echo "域名访问: http://$DOMAIN:$NGINX_PORT"
    echo "直接访问: http://$GATEWAY_HOST:$GATEWAY_PORT"
}

# 启动nginx
start_nginx() {
    log_info "启动 nginx..."
    
    # 停止现有nginx
    pkill -9 nginx 2>/dev/null || true
    sleep 1
    
    # 清除proxychains环境变量
    unset LD_PRELOAD HTTP_PROXY HTTPS_PROXY http_proxy https_proxy PROXYCHAINS_CONF_FILE
    
    # 启动nginx
    nginx -c "$NGINX_CONF" &
    sleep 2
    
    # 验证启动
    if curl -s --noproxy '*' --max-time 3 "http://127.0.0.1:$NGINX_PORT/api/health" > /dev/null 2>&1; then
        log_info "nginx 启动成功"
    else
        log_error "nginx 启动失败"
        return 1
    fi
}

# 停止nginx
stop_nginx() {
    log_info "停止 nginx..."
    pkill -9 nginx 2>/dev/null || true
    log_info "nginx 已停止"
}

# 配置Windows hosts文件
setup_hosts() {
    log_info "配置 Windows hosts 文件..."
    
    local hosts_file="/mnt/c/Windows/System32/drivers/etc/hosts"
    
    # 检查是否已存在
    if grep -q "$DOMAIN" "$hosts_file" 2>/dev/null; then
        log_warn "$DOMAIN 已存在于 hosts 文件中"
        return 0
    fi
    
    # 添加hosts条目
    echo "" >> "$hosts_file"
    echo "# OKX Gateway" >> "$hosts_file"
    echo "127.0.0.1 $DOMAIN" >> "$hosts_file"
    
    log_info "已添加 $DOMAIN 到 hosts 文件"
    log_info "现在可以通过 http://$DOMAIN:$NGINX_PORT 访问"
}

# 显示帮助
show_help() {
    echo "OKX Gateway 部署脚本"
    echo ""
    echo "用法: $0 [命令]"
    echo ""
    echo "命令:"
    echo "  start        启动 nginx 反向代理"
    echo "  stop         停止 nginx"
    echo "  restart      重启 nginx"
    echo "  status       显示服务状态"
    echo "  setup-hosts  配置 Windows hosts 文件"
    echo "  deploy       完整部署 (启动nginx + 配置hosts)"
    echo "  help         显示此帮助信息"
    echo ""
    echo "访问地址:"
    echo "  http://localhost:$NGINX_PORT"
    echo "  http://$DOMAIN:$NGINX_PORT (需要配置hosts)"
    echo "  http://$GATEWAY_HOST:$GATEWAY_PORT (直接访问)"
}

# 主逻辑
case "${1:-help}" in
    start)
        start_nginx
        check_status
        ;;
    stop)
        stop_nginx
        ;;
    restart)
        stop_nginx
        start_nginx
        check_status
        ;;
    status)
        check_status
        ;;
    setup-hosts)
        setup_hosts
        ;;
    deploy)
        start_nginx
        setup_hosts
        check_status
        ;;
    help|*)
        show_help
        ;;
esac
