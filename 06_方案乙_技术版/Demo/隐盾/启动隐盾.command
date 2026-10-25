#!/bin/bash
# 双击启动隐盾网页版（Mac）。首次会自动建虚拟环境并装依赖（约 1 分钟），之后秒开。只在本机 127.0.0.1 监听。
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/streamlit ]; then
  echo "首次启动：正在创建虚拟环境并安装依赖……"
  python3 -m venv .venv && ./.venv/bin/pip install -q -r requirements.txt || { echo "安装依赖失败，请检查网络后重试"; read -r; exit 1; }
fi
./.venv/bin/streamlit run app.py
