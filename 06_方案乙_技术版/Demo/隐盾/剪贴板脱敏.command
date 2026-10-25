#!/bin/bash
# 剪贴板一键（Mac）：先复制原文再双击 → 剪贴板里换成脱敏文本，直接粘贴给大模型；
# 复制大模型的回答再双击 → 剪贴板里换成还原后的文字。只用 Python 标准库，不需要装依赖。
cd "$(dirname "$0")" || exit 1
python3 cli.py clip
sleep 2
