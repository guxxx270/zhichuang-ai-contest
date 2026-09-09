#!/bin/bash
# 双击启动需知 Demo（Mac）。首次会自动装依赖。运行日志写入 data/logs/启动_最近一次.log
cd "$(dirname "$0")" || exit 1
mkdir -p data/logs
if [ ! -d .venv ]; then python3 -m venv .venv && ./.venv/bin/pip install -q -r requirements.txt; fi
./.venv/bin/pip install -q -r requirements.txt 2>/dev/null
./.venv/bin/streamlit run app.py 2>&1 | tee data/logs/启动_最近一次.log
