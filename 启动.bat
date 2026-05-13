@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Shinsekai 启动器

echo.
echo ===== Shinsekai MVP 启动器 =====
echo.
echo [1] 启动 MVP2 Codex 助手
echo [2] 启动旧版聊天主窗
echo [3] 打开设置中心
echo [4] 创建桌面快捷方式
echo.
choice /c 1234 /n /m "请选择 [1-4]: "

if errorlevel 4 (
  call "%~dp0创建桌面快捷方式.bat"
  exit /b %errorlevel%
)

if errorlevel 3 (
  call "%~dp0start_settings.bat"
  exit /b %errorlevel%
)

if errorlevel 2 (
  call "%~dp0start_chat.bat"
  exit /b %errorlevel%
)

if errorlevel 1 (
  call "%~dp0start_chat2.bat"
  exit /b %errorlevel%
)
