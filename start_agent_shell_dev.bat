@echo off
title Joi Shell Dev
setlocal
cd /d "%~dp0agent_companion\shell"
if exist "D:\codex游戏\toolchains\node\npm.cmd" (
  "D:\codex游戏\toolchains\node\npm.cmd" run tauri dev
) else (
  npm run tauri dev
)
