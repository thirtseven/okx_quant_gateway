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

    WSL_IP=$(hostname -I | awk '{print $1}')
    if [ -z "$WSL_IP" ]; then
        echo "❌ 无法获取 WSL IP"
        exit 1
    fi

    cat > "$CONF_FILE" << EOF
pid /tmp/nginx.pid;
error_log /tmp/nginx_error.log;
events { worker_connections 1024; }
http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    access_log /tmp/nginx_access.log;
    server {
        listen 8080;
        server_name okx.local localhost;
        client_max_body_size 10m;
        location / {
            proxy_pass http://${WSL_IP}:5000;
            proxy_set_header Host \$host;
            proxy_http_version 1.1;
            proxy_connect_timeout 30s;
            proxy_read_timeout 60s;
        }
    }
}
EOF

    sudo cp "$CONF_FILE" "$NGINX_AVAILABLE"

    sudo ln -sf "$NGINX_AVAILABLE" "$NGINX_ENABLED"

    sudo rm -f /etc/nginx/sites-enabled/default

    sudo nginx -t
    if [ $? -ne 0 ]; then
        echo "❌ Nginx 配置测试失败"
        exit 1
    fi

    unset LD_PRELOAD PROXYCHAINS_CONF_FILE http_proxy https_proxy HTTP_PROXY HTTPS_PROXY

    if command -v systemctl &>/dev/null && systemctl is-system-running &>/dev/null 2>&1; then
        sudo systemctl restart nginx
        sudo systemctl enable nginx
    else
        sudo nginx -s reload 2>/dev/null || sudo nginx
    fi

    echo "✅ Nginx 反向代理已配置"
    echo ""
    echo "访问地址:"
    echo "  http://okx.local:8080"
    echo "  http://$WSL_IP:8080"
    echo ""
    echo "日志文件:"
    echo "  访问日志: /tmp/nginx_access.log"
    echo "  错误日志: /tmp/nginx_error.log"
}

status() {
    echo "📊 Nginx 状态:"
    echo ""

    if pgrep -x nginx >/dev/null; then
        echo "  ✅ Nginx 运行中"
    else
        echo "  ❌ Nginx 未运行"
    fi

    echo ""
    echo "  配置文件:"
    if [ -f "$CONF_FILE" ]; then
        echo "    ✅ $CONF_FILE"
    else
        echo "    ❌ 未配置"
    fi

    echo ""
    echo "  端口监听:"
    ss -tlnp | grep -E ":8080|:5000" | awk '{print "    " $0}'
}

remove() {
    echo "🧹 移除 Nginx 反向代理..."

    sudo rm -f "$NGINX_ENABLED"
    sudo rm -f "$NGINX_AVAILABLE"

    pkill nginx 2>/dev/null || true

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
