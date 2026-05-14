@echo off
title Joi Core
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m agent_companion.core.main --serve --workspace "%cd%"
) else (
  py -3 -m agent_companion.core.main --serve --workspace "%cd%"
)
