@echo off
setlocal EnableExtensions
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

REM Windows 双击启动隐盾网页版。首次会自动建虚拟环境并装依赖，之后秒开。只在本机 127.0.0.1 监听。
REM Streamlit 需要 Python 3.9+，优先用 py 启动器找新版本。

set "PYLAUNCH="
where py >nul 2>&1
if errorlevel 1 goto :try_python
for %%V in (3.13 3.12 3.11 3.10 3.9) do (
  py -%%V -c "import sys" >nul 2>&1
  if not errorlevel 1 (
    set "PYLAUNCH=py -%%V"
    goto :have_py
  )
)

:try_python
python -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 9) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PYLAUNCH=python"
  goto :have_py
)
echo 未找到 Python 3.9 或更高版本。请安装 Python 3.12，并勾选 "Add python.exe to PATH"。
pause
exit /b 1

:have_py
if not exist ".venv\Scripts\streamlit.exe" (
  echo 首次启动：正在用 %PYLAUNCH% 创建虚拟环境并安装依赖……
  %PYLAUNCH% -m venv .venv
  if errorlevel 1 (
    echo 创建虚拟环境失败。
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo 安装依赖失败。请检查网络（或配置公司内网 pip 镜像）后重新双击本脚本。
    pause
    exit /b 1
  )
)

echo 正在启动隐盾，浏览器打开后即可使用。关闭本窗口或按 Ctrl+C 可停止。
".venv\Scripts\python.exe" -m streamlit run app.py
if errorlevel 1 pause
