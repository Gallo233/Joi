@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto missing_python
if "%~1"=="" goto no_args

".venv\Scripts\python.exe" -m agent_companion.core.main %*
exit /b %errorlevel%

:no_args
".venv\Scripts\python.exe" -m agent_companion.core.main "陪我看当前画面"
exit /b %errorlevel%

:missing_python
echo Missing .venv\Scripts\python.exe. Please install dependencies first.
pause
exit /b 1
