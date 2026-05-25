#!/bin/bash
# OKX 量化网关 - 全局快捷指令
# 安装到 /usr/local/bin 后可在任意目录使用

okx() {
    local PROJECT_DIR="/mnt/c/Users/Lenovo/Desktop/okx_b"
    local CMD="$1"
    
    case "$CMD" in
        start)
            "$PROJECT_DIR/start.sh" start
            ;;
        stop)
            "$PROJECT_DIR/start.sh" stop
            ;;
        restart)
            "$PROJECT_DIR/start.sh" restart
            ;;
        status)
            "$PROJECT_DIR/start.sh" status
            ;;
        log)
            "$PROJECT_DIR/start.sh" log
            ;;
        test)
            cd "$PROJECT_DIR" && python3 test_gateway.py
            ;;
        pairs)
            curl -s http://localhost:5000/api/state 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'交易对: {len(d.get(\"trading_pairs\",[]))} 个'); print(f'持仓: {len(d.get(\"open_positions\",[]))} 个'); print(f'指标缓存: {len(d.get(\"indicator_cache\",{}))} 个')"
            ;;
        signals)
            curl -s http://localhost:5000/api/signals 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); sigs=d.get('signals',{}); longs=[k for k,v in sigs.items() if v.get('signal')=='LONG']; shorts=[k for k,v in sigs.items() if v.get('signal')=='SHORT']; print(f'信号: {len(sigs)} 个 (LONG: {len(longs)}, SHORT: {len(shorts)})')"
            ;;
        prices)
            curl -s http://localhost:5000/api/prices 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); prices=d.get('prices',{}); print(f'价格: {len(prices)} 个'); [print(f'  {k}: {v.get(\"last\")}') for k,v in list(prices.items())[:5]]"
            ;;
        health)
            curl -s http://localhost:5000/api/health 2>/dev/null || echo "❌ 服务未运行"
            ;;
        help|"")
            echo "OKX 量化网关管理工具"
            echo ""
            echo "用法: okx <命令>"
            echo ""
            echo "服务管理:"
            echo "  start    - 启动服务器"
            echo "  stop     - 停止服务器"
            echo "  restart  - 重启服务器"
            echo "  status   - 查看状态"
            echo "  log      - 实时日志"
            echo ""
            echo "数据查看:"
            echo "  pairs    - 查看交易对和持仓"
            echo "  signals  - 查看信号状态"
            echo "  prices   - 查看价格数据"
            echo "  health   - 健康检查"
            echo ""
            echo "测试:"
            echo "  test     - 运行测试套件"
            echo ""
            echo "访问地址: http://localhost:5000"
            ;;
        *)
            echo "未知命令: $CMD"
            echo "运行 'okx help' 查看帮助"
            ;;
    esac
}
