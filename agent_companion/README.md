# Joi

Joi is a character-fronted multimodal agent companion.

This directory is the new main line. The old `mvp/` and `app2/` directories are retained as references and asset sources only.

## Current Slice

- Python Core with event bus, planner, policy gate, memory store, character harness, and tool registry.
- Tool adapters for Codex, browser observation/search queue, OK-WW dry-run/launch boundary, MCP listing, and safe file reads.
- Tauri/Vue shell scaffold for the modern desktop front-end.

## Run Core Smoke Test

```powershell
cd D:\codex游戏\shinsekai_mvp
.\.venv\Scripts\python.exe run_agent_companion_tests.py
```

## Run One Core Request

```powershell
.\.venv\Scripts\python.exe -m agent_companion.core.main "修复这个项目 bug 并跑测试"
.\.venv\Scripts\python.exe -m agent_companion.core.main "陪我看当前网页"
.\.venv\Scripts\python.exe -m agent_companion.core.main "帮我刷鸣潮日常"
```

Use `--approve` only when you intentionally allow medium-risk tool execution.

## Shell

`shell/` contains the planned Tauri/Vue UI. This machine currently has Node but no `npm`/Rust toolchain, so the shell is scaffolded but not installed.
