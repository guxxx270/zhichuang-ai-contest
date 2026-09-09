@echo off
setlocal EnableExtensions
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

REM Windows 双击启动需知 Demo。首次会自动建虚拟环境并装依赖。
REM 本机 PATH 上的 python 可能是 3.7，Streamlit / pandas 需要 3.10+，所以优先用 py 启动器。

set "PYLAUNCH="
where py >nul 2>&1
if errorlevel 1 goto :try_python

py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 (
  set "PYLAUNCH=py -3.12"
  goto :have_py
)
py -3.10 -c "import sys" >nul 2>&1
if not errorlevel 1 (
  set "PYLAUNCH=py -3.10"
  goto :have_py
)

:try_python
python -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PYLAUNCH=python"
  goto :have_py
)

if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
  set "PYLAUNCH=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  goto :have_py
)

echo 未找到 Python 3.10 或更高版本。
echo 请安装 Python 3.12，并勾选 "Add python.exe to PATH"，或确保 py 启动器可用。
echo 不要用系统里的 Python 3.7：本 Demo 的 streamlit / pandas 跑不起来。
pause
exit /b 1

:have_py
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>&1
  if errorlevel 1 (
    echo 现有 .venv 的 Python 版本过低，正在删除并重建...
    rmdir /s /q .venv
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo 首次启动：正在用 %PYLAUNCH% 创建虚拟环境并安装依赖...
  %PYLAUNCH% -m venv .venv
  if errorlevel 1 (
    echo 创建虚拟环境失败。
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install -U pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo 安装依赖失败。请检查网络后重新双击本脚本。
    pause
    exit /b 1
  )
)

echo 正在启动需知 Demo，浏览器打开后即可使用。关闭本窗口或按 Ctrl+C 可停止。
".venv\Scripts\python.exe" -m streamlit run app.py
if errorlevel 1 pause
