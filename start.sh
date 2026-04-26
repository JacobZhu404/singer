#!/usr/bin/env bash
# 智能选股系统 - 一键启动脚本

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT=5188
PYTHON=/usr/local/bin/python3

echo "╔════════════════════════════════════════╗"
echo "║      🎯 歌者  智能选股系统             ║"
echo "║   凝视资本市场的暗流                   ║"
echo "╚════════════════════════════════════════╝"

# 检查依赖
echo "→ 检查依赖..."
$PYTHON -m pip install -q Flask flask-cors requests pandas numpy

# 检查端口是否已占用
if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "→ 端口 $PORT 已被占用，先停止旧进程..."
  lsof -ti :$PORT | xargs kill -9 2>/dev/null || true
  sleep 1
fi

echo "→ 启动服务..."
cd "$(dirname "$SCRIPT_DIR")"
$PYTHON -c "
import sys
sys.path.insert(0, '.')
from stock_screener.core.server import run_server
run_server(host='0.0.0.0', port=$PORT, debug=False)
" &

PID=$!
sleep 2

if kill -0 $PID 2>/dev/null; then
  echo "✅ 服务已启动！"
  echo "   访问地址: http://127.0.0.1:$PORT"
  echo "   PID: $PID"
  # macOS 自动打开浏览器
  if command -v open &>/dev/null; then
    open "http://127.0.0.1:$PORT"
  fi
  wait $PID
else
  echo "❌ 服务启动失败，请查看日志"
  exit 1
fi
