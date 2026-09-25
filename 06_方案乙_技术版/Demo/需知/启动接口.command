#!/bin/bash
# 双击启动「需知 · REST API」（Mac）。首次会自动装依赖。
# 启动后打开 http://127.0.0.1:8770/docs 看接口文档；Ctrl+C 或关窗口退出。
# 默认只监听本机；要对外必须在 .env 里设 XUZHI_API_HOST 与 XUZHI_API_TOKEN（见 sandbox.yaml api）。
# MCP 服务另起：./.venv/bin/python mcp_server.py（stdio）或加 --transport streamable-http --port 8771
cd "$(dirname "$0")" || exit 1
mkdir -p data/logs
if [ ! -d .venv ]; then python3 -m venv .venv && ./.venv/bin/pip install -q -r requirements-api.txt; fi
./.venv/bin/pip install -q -r requirements-api.txt 2>/dev/null
./.venv/bin/python api.py 2>&1 | tee data/logs/接口_最近一次.log
