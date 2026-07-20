#!/bin/bash
# 一键启动达人分析系统 + ngrok 隧道

echo "🚀 启动达人分析系统..."

# 1. 启动 Flask
cd /workspace/influencer-analysis-system
if ! curl -s -o /dev/null -w "%{http_code}" http://localhost:8504 | grep -q "200"; then
    echo "  → 启动 Flask 服务..."
    nohup python3 server.py > /tmp/flask.log 2>&1 &
    sleep 2
else
    echo "  → Flask 已在运行"
fi

# 2. 启动 ngrok
if ! curl -s http://localhost:4040/api/tunnels 2>/dev/null | grep -q "public_url"; then
    echo "  → 启动 ngrok 隧道..."
    nohup ngrok http 8504 --log=stdout > /tmp/ngrok.log 2>&1 &
    sleep 3
else
    echo "  → ngrok 已在运行"
fi

# 3. 显示 URL
URL=$(curl -s http://localhost:4040/api/tunnels | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['tunnels'][0]['public_url'])")
echo ""
echo "✅ 系统已启动！"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  访问地址: $URL"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📋 日志文件:"
echo "  Flask:  tail -f /tmp/flask.log"
echo "  ngrok:  tail -f /tmp/ngrok.log"
