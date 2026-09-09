@echo off
REM ============================================================
REM  肺保护驾驶舱 - 模拟数据演示 (Windows 一键启动)
REM ============================================================
REM  用途：用「模拟生成的连续分钟数据」跑通前端全链路演示，
REM       展示双环剂量计、累积暴露、24h 滚动窗口、滑动斜率/CUSUM
REM       变化点、G0-G3 分级防抖、顺应性分层、交接班摘要等 URS 效果
REM       （真实设备数据稀疏，这些功能基本看不出来，模拟数据能看清）。
REM
REM  数据只会写入模拟设备 SIM900000001（不会污染真实设备 1788936676 等）。
REM
REM  步骤：
REM    1) 播种最近 24h「surge 越限」历史场景（越限累积明显，效果最好）
REM    2) 用模拟设备 ID 启动后端（回填 + 聚合守护 + API，端口 8090）
REM    3) 浏览器打开 http://localhost:8090/ 即可查看
REM
REM  可选：想看数值实时跳动，再开一个窗口跑
REM        scripts\sim_live_feed.py --scenario surge --interval 5
REM ============================================================
cd /d "%~dp0"

echo [1/2] 清理旧模拟指标（避免叠加残留）...
set PYTHONPATH=.
.venv\Scripts\python.exe scripts\seed_sim_device.py --clean --device SIM900000001

echo [2/2] 播种模拟设备 SIM900000001（最近 24h surge 越限场景）并启动后端（端口 8090）...
set PYTHONPATH=.
set COCKPIT_PORT=8090
set COCKPIT_DEVICE_ID=SIM900000001
.venv\Scripts\python.exe scripts\seed_sim_device.py --hours 24 --scenario surge
set PYTHONPATH=.
set COCKPIT_PORT=8090
set COCKPIT_DEVICE_ID=SIM900000001
.venv\Scripts\python.exe -m lung_protection_cockpit.main all --hours 24

echo.
echo ============================================================
echo  已启动：请用浏览器打开  http://localhost:8090/
echo  实时跳动（可选）：另开窗口跑 scripts\sim_live_feed.py
echo ============================================================
pause
