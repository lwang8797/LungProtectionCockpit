@echo off
REM 肺保护驾驶舱 - 后端一键启动 (Windows)
REM 自动创建虚拟环境、安装依赖、设置 PYTHONPATH 并启动服务
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo [1/3] 创建虚拟环境 .venv ...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    echo [2/3] 安装依赖 ...
    python -m pip install -r lung_protection_cockpit/requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

echo [3/3] 启动服务（回填最近 24h + API，端口默认 8080）...
set PYTHONPATH=.
python -m lung_protection_cockpit.main all --hours 24

pause
