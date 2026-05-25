#!/bin/bash
# OKX Gateway Nginx Reverse Proxy Setup Script
# 用法: ./setup_nginx.sh [setup|status|remove]

set -e

CONF_FILE="nginx.conf"
NGINX_AVAILABLE="/etc/nginx/sites-available/okx-gateway"
NGINX_ENABLED="/etc/nginx/sites-enabled/okx-gateway"

setup() {
    echo "🔧 设置 Nginx 反向代理..."

    if [ ! -f "$CONF_FILE" ]; then
        echo "❌ 配置文件不存在: $CONF_FILE"
        exit 1
    fi

    sudo cp "$CONF_FILE" "$NGINX_AVAILABLE"

    sudo ln -sf "$NGINX_AVAILABLE" "$NGINX_ENABLED"

    sudo rm -f /etc/nginx/sites-enabled/default

    sudo nginx -t
    if [ $? -ne 0 ]; then
        echo "❌ Nginx 配置测试失败"
        exit 1
    fi

    if command -v systemctl &>/dev/null && systemctl is-system-running &>/dev/null 2>&1; then
        sudo systemctl restart nginx
        sudo systemctl enable nginx
    else
        sudo nginx -s reload 2>/dev/null || sudo nginx
    fi

    echo "✅ Nginx 反向代理已配置"
    echo ""
    echo "访问地址:"
    LOCAL_IP=$(hostname -I | awk '{print $1}')
    echo "  本地: http://localhost"
    echo "  局域网: http://$LOCAL_IP"
    echo ""
    echo "日志文件:"
    echo "  访问日志: /var/log/nginx/okx_gateway_access.log"
    echo "  错误日志: /var/log/nginx/okx_gateway_error.log"
}

status() {
    echo "📊 Nginx 状态:"
    echo ""

    if systemctl is-active --quiet nginx; then
        echo "  ✅ Nginx 运行中"
    else
        echo "  ❌ Nginx 未运行"
    fi

    echo ""
    echo "  配置文件:"
    if [ -f "$NGINX_AVAILABLE" ]; then
        echo "    ✅ $NGINX_AVAILABLE"
    else
        echo "    ❌ 未配置"
    fi

    if [ -L "$NGINX_ENABLED" ]; then
        echo "    ✅ $NGINX_ENABLED (已启用)"
    else
        echo "    ❌ 未启用"
    fi

    echo ""
    echo "  端口监听:"
    ss -tlnp | grep -E ":80|:5000" | awk '{print "    " $0}'
}

remove() {
    echo "🧹 移除 Nginx 反向代理..."

    sudo rm -f "$NGINX_ENABLED"
    sudo rm -f "$NGINX_AVAILABLE"

    sudo systemctl restart nginx

    echo "✅ Nginx 反向代理已移除"
}

case "${1:-setup}" in
    setup)
        setup
        ;;
    status)
        status
        ;;
    remove)
        remove
        ;;
    *)
        echo "用法: $0 {setup|status|remove}"
        exit 1
        ;;
esac
