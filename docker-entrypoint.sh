#!/bin/bash
set -e

# Nightingale 启动脚本

echo "🚀 启动 Nightingale (事件聚合版)"
echo "📦 版本信息: $(cat /opt/nightingale/etc/version.txt 2>/dev/null || echo 'unknown')"
echo "⏰ 时区: ${TZ}"
echo ""

# 等待依赖服务（如果配置了）
if [ -n "$WAIT_FOR" ]; then
    echo "⏳ 等待依赖服务: $WAIT_FOR"
    for service in $WAIT_FOR; do
        host="${service%%:*}"
        port="${service##*:}"
        timeout=60
        while ! nc -z "$host" "$port" 2>/dev/null; do
            timeout=$((timeout - 1))
            if [ $timeout -le 0 ]; then
                echo "❌ 等待 $service 超时"
                exit 1
            fi
            sleep 1
        done
        echo "✅ $service 已就绪"
    done
fi

# 根据命令启动不同组件
case "$1" in
    n9e)
        echo "🔧 启动 n9e (核心服务 + 事件聚合)"
        exec /opt/nightingale/n9e
        ;;
    n9e-pushgw)
        echo "📤 启动 n9e-pushgw (推送网关)"
        exec /opt/nightingale/n9e-pushgw
        ;;
    *)
        echo "📋 可用命令:"
        echo "  - n9e         (核心服务，含事件聚合)"
        echo "  - n9e-pushgw  (推送网关)"
        echo ""
        exec "$@"
        ;;
esac
