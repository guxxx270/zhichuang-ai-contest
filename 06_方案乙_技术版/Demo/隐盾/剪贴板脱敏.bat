@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
REM 剪贴板一键（Windows）：先复制原文再双击 → 剪贴板里换成脱敏文本；复制大模型的回答再双击 → 换成还原文本。
REM 只用 Python 标准库（3.8+），不需要装依赖。
where py >nul 2>&1
if not errorlevel 1 (
  py -3 cli.py clip
) else (
  python cli.py clip
)
timeout /t 3 >nul
