#!/bin/bash
# 双击启动「需知 · 企微入口」（Mac）。首次会自动装依赖。
# 启动后在企业微信里 @需知 发一段需求即可；Ctrl+C 或关窗口退出。
# 注意：同一个智能机器人同一时间只允许一条长连接，别和别的脚本同时跑。
cd "$(dirname "$0")" || exit 1
mkdir -p data/logs
if [ ! -d .venv ]; then python3 -m venv .venv && ./.venv/bin/pip install -q -r requirements-wecom.txt; fi
./.venv/bin/pip install -q -r requirements-wecom.txt 2>/dev/null
./.venv/bin/python -m xuzhi.channels.wecom.app 2>&1 | tee data/logs/企微入口_最近一次.log
