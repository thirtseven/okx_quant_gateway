#!/bin/bash
# OKX Gateway Docker Image Backup Script
# 用法: ./docker_backup.sh [build|backup|restore|list]

set -e

IMAGE_NAME="okx-quant-gateway"
IMAGE_TAG="v2.00"
BACKUP_DIR="/mnt/c/Users/Lenovo/okx_b_v2/backups"
BACKUP_FILE="${BACKUP_DIR}/${IMAGE_NAME}_${IMAGE_TAG}_$(date +%Y%m%d_%H%M%S).tar"

mkdir -p "$BACKUP_DIR"

build_image() {
    echo "🔨 构建 Docker 镜像..."
    docker build -t "${IMAGE_NAME}:${IMAGE_TAG}" .
    docker tag "${IMAGE_NAME}:${IMAGE_TAG}" "${IMAGE_NAME}:latest"
    echo "✅ 镜像构建完成: ${IMAGE_NAME}:${IMAGE_TAG}"
}

backup_image() {
    echo "📦 备份 Docker 镜像..."
    
    if ! docker images | grep -q "${IMAGE_NAME}"; then
        echo "❌ 镜像不存在，请先构建: $0 build"
        exit 1
    fi
    
    docker save -o "$BACKUP_FILE" "${IMAGE_NAME}:${IMAGE_TAG}" "${IMAGE_NAME}:latest"
    
    if [ -f "$BACKUP_FILE" ]; then
        SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
        echo "✅ 备份完成: $BACKUP_FILE ($SIZE)"
        
        # 创建最新备份的软链接
        ln -sf "$BACKUP_FILE" "${BACKUP_DIR}/${IMAGE_NAME}_latest.tar"
        echo "📎 最新备份链接: ${BACKUP_DIR}/${IMAGE_NAME}_latest.tar"
    else
        echo "❌ 备份失败"
        exit 1
    fi
}

restore_image() {
    local restore_file="$1"
    
    if [ -z "$restore_file" ]; then
        restore_file="${BACKUP_DIR}/${IMAGE_NAME}_latest.tar"
    fi
    
    if [ ! -f "$restore_file" ]; then
        echo "❌ 备份文件不存在: $restore_file"
        echo "可用备份:"
        list_backups
        exit 1
    fi
    
    echo "📥 恢复 Docker 镜像: $restore_file"
    docker load -i "$restore_file"
    echo "✅ 镜像恢复完成"
    
    echo "当前镜像:"
    docker images | grep "${IMAGE_NAME}"
}

list_backups() {
    echo "📋 可用备份:"
    if [ -d "$BACKUP_DIR" ]; then
        ls -lh "${BACKUP_DIR}"/*.tar 2>/dev/null || echo "  无备份文件"
    else
        echo "  备份目录不存在"
    fi
}

clean_old_backups() {
    local keep=${1:-5}
    echo "🧹 清理旧备份 (保留最近 $keep 个)..."
    
    if [ -d "$BACKUP_DIR" ]; then
        cd "$BACKUP_DIR"
        ls -t *.tar 2>/dev/null | tail -n +$((keep + 1)) | xargs -r rm -v
        echo "✅ 清理完成"
    fi
}

show_help() {
    echo "OKX Gateway Docker 镜像备份工具"
    echo ""
    echo "用法: $0 {build|backup|restore|list|clean}"
    echo ""
    echo "命令:"
    echo "  build          - 构建 Docker 镜像"
    echo "  backup         - 备份当前镜像"
    echo "  restore [file] - 恢复镜像 (默认使用最新备份)"
    echo "  list           - 列出所有备份"
    echo "  clean [N]      - 清理旧备份 (默认保留5个)"
    echo ""
    echo "示例:"
    echo "  $0 build && $0 backup    # 构建并备份"
    echo "  $0 restore               # 恢复最新备份"
    echo "  $0 restore backup.tar    # 恢复指定备份"
}

case "${1:-help}" in
    build)
        build_image
        ;;
    backup)
        backup_image
        ;;
    restore)
        restore_image "$2"
        ;;
    list)
        list_backups
        ;;
    clean)
        clean_old_backups "$2"
        ;;
    help|*)
        show_help
        ;;
esac
