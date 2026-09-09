@echo off
REM ============================================================
REM  Lung Protection Cockpit - Simulated Data Demo (Windows)
REM ============================================================
REM  Purpose: demo full URS features with synthetic continuous
REM  minute data (real device data is too sparse to see them):
REM    dual-ring dosimeter / cumulative exposure / 24h rolling
REM    window / slope + CUSUM change points / G0-G3 debounce /
REM    compliance stratification / shift summary.
REM  All synthetic data goes to SIM900000001 only, never touching
REM  real devices. Clean with: seed_sim_device.py --clean.
REM ============================================================
cd /d "%~dp0"

echo [1/3] cleaning old SIM data ...
set PYTHONPATH=.
.venv\Scripts\python.exe scripts\seed_sim_device.py --clean --device SIM900000001

echo [2/3] seeding SIM device SIM900000001 (last 24h, surge scenario) ...
set PYTHONPATH=.
.venv\Scripts\python.exe scripts\seed_sim_device.py --hours 24 --scenario surge

echo [3/3] starting backend with SIM device on port 8090 ...
set PYTHONPATH=.
set COCKPIT_PORT=8090
set COCKPIT_DEVICE_ID=SIM900000001
.venv\Scripts\python.exe -m lung_protection_cockpit.main all --hours 24

echo.
echo ============================================================
echo  Demo running. Open http://localhost:8090/ in your browser.
echo  Optional live ticking: in another window run
echo    scripts\sim_live_feed.py --scenario surge --interval 5
echo  To stop: close this window (Ctrl+C).
echo ============================================================
pause
