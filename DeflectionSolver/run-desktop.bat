@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

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

rem 某些环境会把 Electron 当 Node 跑 (ELECTRON_RUN_AS_NODE=1), 必须清掉
set "ELECTRON_RUN_AS_NODE="

pushd desktop
call npm start
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%

:make_venv
echo [ShimTuner] Creating Python venv at "%VENV%" ...
where python >nul 2>nul
if errorlevel 1 (
  py -3 -m venv "%VENV%"
) else (
  python -m venv "%VENV%"
)
exit /b 0

:install_python
echo [ShimTuner] Installing Python dependencies ...
"%VENV%\Scripts\python.exe" -m pip install -r "%~dp0requirements-ui.txt"
exit /b 0

:install_node
echo [ShimTuner] Installing Electron ...
pushd desktop
call npm install
popd
exit /b 0

:fail
echo.
echo [ShimTuner] Startup failed. See messages above.
pause
exit /b 1
