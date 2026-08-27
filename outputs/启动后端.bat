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

REM 默认端口 8090（8080 常被本机 ApplicationWebServer.exe 占用，会报 WinError 10048）。
REM 如需改用 8080，请先关闭占用 8080 的程序，或改 COCKPIT_PORT=8080。
echo [3/3] 启动服务（回填最近 24h + API，端口默认 8090）...
set PYTHONPATH=.
set COCKPIT_PORT=8090
python -m lung_protection_cockpit.main all --hours 24

pause
