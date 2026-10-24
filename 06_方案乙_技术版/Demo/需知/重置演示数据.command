#!/bin/bash
# 双击：上台前把一本账恢复成演示用的预置状态（20 条虚构需求、各部门、各状态、部分已对账）。
# 当前数据先整份备份到 data/backup/<时间>/，不会丢；只清空不预置：在终端里加 --empty。
cd "$(dirname "$0")" || exit 1
if [ ! -d .venv ]; then echo "首次运行，安装依赖……"; python3 -m venv .venv && ./.venv/bin/pip install -q -r requirements.txt; fi
./.venv/bin/python tools/demo_seed.py "$@"
echo; echo "请关闭需知后重新打开（旧页面里的需求编号已失效）；按回车关闭本窗口。"; read -r
