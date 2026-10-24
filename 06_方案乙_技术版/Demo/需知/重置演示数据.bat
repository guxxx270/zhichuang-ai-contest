@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
REM 双击：上台前把一本账恢复成演示用的预置状态。当前数据先备份到 data\backup\。需先运行过一次 启动需知.bat。
if not exist ".venv\Scripts\python.exe" (
  echo 请先双击 启动需知.bat 完成首次安装。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" tools\demo_seed.py %*
echo 请关闭需知后重新打开（旧页面里的需求编号已失效）。
pause
