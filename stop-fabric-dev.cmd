@echo off
setlocal

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

echo Stopping Fabric processes...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\stop-fabric-dev.ps1"
echo Done.
