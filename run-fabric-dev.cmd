@echo off
setlocal

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "BACKEND_DIR=%ROOT%\backend"
set "AGENT_DIR=%ROOT%\agent"
set "FRONTEND_DIR=%ROOT%\frontend"

set "BACKEND_PY=%BACKEND_DIR%\.venv\Scripts\python.exe"
set "AGENT_PY=%AGENT_DIR%\.venv\Scripts\python.exe"
set "BACKEND_WS_URL=ws://127.0.0.1:8000/ws/v1/agent/"
set "DEV_USERNAME=fabric-admin"
set "DEV_PASSWORD=fabric-password"
set "DEV_AGENT_NAME=demo-agent"

echo [1/7] Checking backend virtual environment...
if not exist "%BACKEND_PY%" (
  echo Creating backend virtual environment...
  pushd "%BACKEND_DIR%"
  python -m venv .venv || goto :fail
  "%BACKEND_PY%" -m pip install -e ".[dev]" || goto :fail
  popd
)

echo [2/7] Checking agent virtual environment...
if not exist "%AGENT_PY%" (
  echo Creating agent virtual environment...
  pushd "%AGENT_DIR%"
  python -m venv .venv || goto :fail
  "%AGENT_PY%" -m pip install -e . || goto :fail
  popd
)

echo [3/7] Checking frontend dependencies...
if not exist "%FRONTEND_DIR%\node_modules" (
  pushd "%FRONTEND_DIR%"
  call npm.cmd install || goto :fail
  popd
)

echo [4/7] Preparing backend data...
pushd "%BACKEND_DIR%"
"%BACKEND_PY%" manage.py migrate || goto :fail
"%BACKEND_PY%" manage.py create_dev_user --username "%DEV_USERNAME%" --password "%DEV_PASSWORD%" --staff || goto :fail

set "AGENT_ENV_FILE=%TEMP%\fabric-agent-env-%RANDOM%%RANDOM%.txt"
"%BACKEND_PY%" manage.py ensure_dev_agent --name "%DEV_AGENT_NAME%" --owner "%DEV_USERNAME%" > "%AGENT_ENV_FILE%" || goto :fail

for /f "usebackq tokens=1,* delims==" %%A in ("%AGENT_ENV_FILE%") do (
  if /I "%%A"=="FABRIC_AGENT_ID" set "FABRIC_AGENT_ID=%%B"
  if /I "%%A"=="FABRIC_AGENT_TOKEN" set "FABRIC_AGENT_TOKEN=%%B"
)
del "%AGENT_ENV_FILE%" >nul 2>&1
popd

if not defined FABRIC_AGENT_ID (
  echo Failed to obtain FABRIC_AGENT_ID.
  goto :fail
)

if not defined FABRIC_AGENT_TOKEN (
  echo Failed to obtain FABRIC_AGENT_TOKEN.
  goto :fail
)

echo [5/7] Starting backend...
start "Fabric Backend" cmd /k "cd /d ""%BACKEND_DIR%"" && ""%BACKEND_PY%"" manage.py runserver 127.0.0.1:8000"

echo [6/7] Starting frontend...
start "Fabric Frontend" cmd /k "cd /d ""%FRONTEND_DIR%"" && npm.cmd start -- --host 127.0.0.1 --port 4200"

echo [7/7] Starting agent...
start "Fabric Agent" cmd /k "cd /d ""%AGENT_DIR%"" && set FABRIC_SERVER_WS_URL=%BACKEND_WS_URL% && set FABRIC_AGENT_ID=%FABRIC_AGENT_ID% && set FABRIC_AGENT_TOKEN=%FABRIC_AGENT_TOKEN% && ""%AGENT_PY%"" -m fabric_agent"

echo.
echo Fabric started.
echo UI:       http://127.0.0.1:4200
echo Backend:  http://127.0.0.1:8000
echo Username: %DEV_USERNAME%
echo Password: %DEV_PASSWORD%
echo Agent:    %DEV_AGENT_NAME%
echo.
echo If old processes already occupy ports 8000 or 4200, close them and run this file again.
goto :eof

:fail
echo.
echo Fabric launcher failed.
exit /b 1
