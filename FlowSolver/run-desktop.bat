@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo [FlowSolver] Launching Flow Studio ...

set "VENV=%~dp0.venv"
if exist "%~dp0..\.venv\Scripts\python.exe" set "VENV=%~dp0..\.venv"

if not exist "%VENV%\Scripts\python.exe" call :make_venv
if not exist "%VENV%\Scripts\python.exe" goto :fail

"%VENV%\Scripts\python.exe" -c "import numpy, scipy, fastapi, uvicorn" >nul 2>nul
if errorlevel 1 call :install_python
"%VENV%\Scripts\python.exe" -c "import numpy, scipy, fastapi, uvicorn" >nul 2>nul
if errorlevel 1 goto :fail

if not exist "desktop\node_modules\electron\package.json" call :install_node
if not exist "desktop\node_modules\electron\package.json" goto :fail
if not exist "desktop\main.js" goto :fail

rem Some environments set ELECTRON_RUN_AS_NODE=1 which makes Electron run as Node.
set "ELECTRON_RUN_AS_NODE="

pushd desktop
call npm start
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" (
  echo.
  echo [FlowSolver] Electron exited with code %RC%.
  pause
)
exit /b %RC%

:make_venv
echo [FlowSolver] Creating Python venv at "%VENV%" ...
where python >nul 2>nul
if errorlevel 1 (
  py -3 -m venv "%VENV%"
) else (
  python -m venv "%VENV%"
)
exit /b 0

:install_python
echo [FlowSolver] Installing Python dependencies ...
"%VENV%\Scripts\python.exe" -m pip install -r "%~dp0requirements-ui.txt"
exit /b 0

:install_node
echo [FlowSolver] Installing Electron (first run, may take a while) ...
pushd desktop
call npm install
popd
exit /b 0

:fail
echo.
echo [FlowSolver] Startup failed. See messages above.
pause
exit /b 1