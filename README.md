# Joi

Joi is a Windows-first multimodal agent companion: a character-fronted assistant for coding, watching screen content, and launching auditable local skills.

The current repository is intentionally focused on the Joi main line.

## Current Scope

- Python Agent Core: planner, policy gate, event bus, memory store, character harness, tool registry.
- Tool adapters: Codex, browser observation/search queue, OK-WW game skill, MCP discovery, safe file reads.
- Tauri/Vue Shell: product UI, character stage, chat stream, task cards, approval actions, developer event view.
- Voice path: optional GPT-SoVITS bridge through local `config.yaml`; no fallback to system TTS unless explicitly implemented later.

## Project Docs

- [Roadmap](docs/ROADMAP.md)
- [Changelog](docs/CHANGELOG.md)
- [Known Issues](docs/KNOWN_ISSUES.md)
- [Feedback Log](docs/FEEDBACK_LOG.md)
- [Hermes Handoff](docs/HERMES_TASKS.md)
- [Architecture](agent_companion/docs/architecture.md)
- [Windows Toolchain and Bridge](agent_companion/docs/windows_toolchain_and_bridge.md)

## Quick Start

```powershell
cd path\to\Joi
.\start_joi.bat
```

The launcher starts the Python Core on `ws://127.0.0.1:8765` and opens the Joi desktop shell. Core logs are written to `logs/joi_core.out.log` and `logs/joi_core.err.log`.

## Development

Install Python dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

Run core tests:

```powershell
.\.venv\Scripts\python.exe run_agent_companion_tests.py
```

Run a single core request:

```powershell
.\.venv\Scripts\python.exe -m agent_companion.core.main "修复这个项目 bug 并跑测试"
.\.venv\Scripts\python.exe -m agent_companion.core.main "陪我看当前网页"
.\.venv\Scripts\python.exe -m agent_companion.core.main "帮我刷鸣潮日常"
```

Run the desktop shell during development:

```powershell
cd agent_companion\shell
npm install
npm run tauri dev
```

## Local Configuration

Public Git does not include personal API keys, model paths, GPT-SoVITS paths, imported character packs, or generated runtime data.

Copy `config.example.yaml` to `config.yaml` for local model and voice configuration:

```powershell
Copy-Item config.example.yaml config.yaml
```

Use environment variables or `secrets.yaml` for private credentials. Both `config.yaml` and `secrets.yaml` are ignored by Git.

## Repository Layout

```text
agent_companion/          Python Core and Tauri/Vue shell
docs/                     Roadmap, changelog, known issues, feedback log
tools/                    Setup, smoke test, and launcher helpers
run_agent_companion_tests.py
start_joi.bat
config.example.yaml
```
