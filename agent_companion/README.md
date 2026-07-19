# Joi

Joi is a character-fronted multimodal agent companion.

This directory is the product main line.

## Current Slice

- Python Core with event bus, planner, policy gate, memory store, character harness, and tool registry.
- Tool adapters for Codex, browser observation/search queue, OK-WW dry-run/launch boundary, MCP listing, and safe file reads.
- Tauri/Vue shell scaffold for the modern desktop front-end.

## Run Core Smoke Test

```powershell
cd path\to\Joi
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

`shell/` contains the Tauri/Vue desktop UI. It connects to the Python Core through the local WebSocket JSON-RPC bridge.

### Local Live2D model

The shell renders Joi's Cubism model when local model/runtime assets are available, and falls back to the configured static sprite otherwise. Imported model files and the redistributable Cubism runtime stay out of Git.

By default, `npm run dev` and `npm run build` look for a complete asset tree at `~/Documents/All Joi/public`. To use another location:

```bash
JOI_LIVE2D_SOURCE=/absolute/path/to/public npm run live2d:sync
```

The source directory must contain `live2d/joi/joi.model3.json` and `vendor/live2d/`.
