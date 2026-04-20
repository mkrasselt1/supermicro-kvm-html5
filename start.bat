@echo off
REM Supermicro iKVM Web Console Launcher for Windows
REM Usage: start.bat [BMC_HOST] [USERNAME] [PASSWORD]

set BMC_HOST=%1
set USERNAME=%2
set PASSWORD=%3

if "%BMC_HOST%"=="" set BMC_HOST=192.0.2.11
if "%USERNAME%"=="" set USERNAME=ADMIN
if "%PASSWORD%"=="" set PASSWORD=ADMIN

pip install -q websockets 2>nul

echo Starting Supermicro iKVM Web Console...
echo BMC: %BMC_HOST%  User: %USERNAME%
echo.

python "%~dp0server.py" --bmc-host %BMC_HOST% --username %USERNAME% --password %PASSWORD%
