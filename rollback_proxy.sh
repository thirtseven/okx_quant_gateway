#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# OKX Gateway — 代理配置回滚脚本
# 恢复 nginx 反代 + 出站代理到备份快照
# ═══════════════════════════════════════════════════════════════

set -e

OKX_HOME="${OKX_HOME:-/home/thirty/okx-gateway}"
BACKUP_DIR="$OKX_HOME/backups/proxy_rollback_20260517"

echo ""
echo "  OKX Gateway — 代理回滚"
echo "  ═══════════════════════"
echo ""

if [ ! -d "$BACKUP_DIR" ]; then
    echo "  错误: 未找到备份快照 $BACKUP_DIR"
    echo ""
    echo "  手动回滚步骤:"
    echo "    1. okx proxy remove       # 移除 nginx 反代"
    echo "    2. okx proxy unset        # 取消出站代理"
    echo "    3. 重启 gateway"
    exit 1
fi

echo "  备份快照: $BACKUP_DIR"
echo "  将恢复以下文件:"
echo "    - nginx.conf"
echo "    - okx_bashrc.sh"
echo "    - setup_nginx.sh"
echo "  同时: 移除 nginx 反代配置 + 取消出站代理"
echo ""

read -rp "  确认回滚? [y/N] " confirm
case "$confirm" in
    [yY]|[yY][eE][sS]) ;;
    *) echo "  已取消"; exit 0 ;;
esac

echo ""
echo "  [1/4] 恢复配置文件..."
cp "$BACKUP_DIR/nginx.conf"       "$OKX_HOME/nginx.conf"
cp "$BACKUP_DIR/okx_bashrc.sh"    "$OKX_HOME/okx_bashrc.sh"
cp "$BACKUP_DIR/setup_nginx.sh"   "$OKX_HOME/setup_nginx.sh"
echo "  [OK] 3 个文件已恢复"

echo "  [2/4] 移除 nginx 反代..."
sudo rm -f /etc/nginx/sites-enabled/okx-gateway /etc/nginx/sites-available/okx-gateway 2>/dev/null
sudo systemctl restart nginx 2>/dev/null && echo "  [OK] nginx 已重启" || echo "  [OK] nginx 未运行"

echo "  [3/4] 取消出站代理..."
rm -f "$OKX_HOME/.proxy_env"
if [ -f "$OKX_HOME/.env" ]; then
    sed -i '/^HTTPS_PROXY=/d' "$OKX_HOME/.env" 2>/dev/null
fi
echo "  [OK] 已恢复直连"

echo "  [4/4] 验证..."
cd "$OKX_HOME"
echo "  当前 nginx.conf MD5:  $(md5sum nginx.conf | cut -d' ' -f1)"
echo "  备份 nginx.conf MD5:  $(md5sum "$BACKUP_DIR/nginx.conf" | cut -d' ' -f1)"
echo "  出站代理: $( [ -f .proxy_env ] && cat .proxy_env || echo '直连' )"

echo ""
echo "  ═══════════════════════════════════════════"
echo "  回滚完成"
echo "  ═══════════════════════════════════════════"
echo "  source ~/.bashrc 或新开终端使 bashrc 别名生效"
echo ""
