# Mac Handoff: Agent Companion

This repo now has a new main line in `agent_companion/`.

The old `mvp/` and `app2/` folders are retained as references and asset sources. New work should target `agent_companion/`.

## Current State

- Python Agent Core is implemented.
- Tauri/Vue shell is scaffolded but not wired to the Python core yet.
- Core routes the three launch scenarios:
  - game assist: `帮我刷鸣潮日常`
  - watch together: `陪我看当前网页`
  - coding: `修复这个项目 bug 并跑测试`
- Medium/high-risk actions require approval before execution.
- Voice lines are sanitized so the character does not read JSON, paths, command flags, tokens, logs, or task ids.

## Mac Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run_agent_companion_tests.py
```

For model access, use a local secret or environment variable. Do not commit real API keys.

```bash
export DEEPSEEK_API_KEY="sk-your-key"
```

## Run Core

```bash
python -m agent_companion.core.main "陪我看当前网页"
python -m agent_companion.core.main "帮我刷鸣潮日常"
python -m agent_companion.core.main "修复这个项目 bug 并跑测试"
```

Use `--approve` only when you intentionally allow a medium/high-risk adapter to execute.

```bash
python -m agent_companion.core.main "修复这个项目 bug 并跑测试" --approve
```

## Tauri/Vue Shell

The shell lives in:

```text
agent_companion/shell
```

Install prerequisites on Mac:

```bash
brew install node rust
cd agent_companion/shell
npm install
npm run tauri dev
```

The shell currently renders the product frame and protocol types. The next implementation step is the local WebSocket JSON-RPC bridge between `agent_companion.core` and the Tauri shell.

## Next Work

1. Add Python WebSocket JSON-RPC server for `AgentCompanionApp`.
2. Connect `agent_companion/shell/src/App.vue` to the core event stream.
3. Replace placeholder stage art with original character assets.
4. Connect the browser observer to real screenshots/OCR.
5. Connect OK-WW launch flow with approval UI.
6. Add Codex execution cards and log drill-down in the shell.

