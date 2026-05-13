@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist "logs" mkdir "logs"
set "LOG=logs\chat_startup.log"

if not exist "chat.py" goto missing_chat
if not exist ".venv\Scripts\python.exe" goto missing_python
if "%SHINSEKAI_DRY_RUN%"=="1" echo startup script OK: chat.py
if "%SHINSEKAI_DRY_RUN%"=="1" exit /b 0

echo [%date% %time%] starting chat.py > "%LOG%"
".venv\Scripts\python.exe" "chat.py" >> "%LOG%" 2>&1
set "EXITCODE=%errorlevel%"
if "%EXITCODE%"=="0" exit /b 0

echo.
echo Shinsekai 聊天主窗启动失败，错误日志：
echo %cd%\%LOG%
echo.
type "%LOG%"
pause
exit /b %EXITCODE%

:missing_chat
echo 未找到 chat.py，请确认当前目录是 Shinsekai MVP 项目根目录。
pause
exit /b 1

:missing_python
echo 未找到 .venv\Scripts\python.exe，请先完成依赖安装：
echo py -m venv .venv
echo .\.venv\Scripts\pip install -r requirements.txt
pause
exit /b 1
