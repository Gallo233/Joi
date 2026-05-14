# Mac Handoff: Joi

Joi now has a new main line in `agent_companion/`.

## Current State

- Python Agent Core is implemented.
- Tauri/Vue shell is wired to the Python core through the local WebSocket bridge.
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

The shell renders the product frame, task cards, dialogue stream, approval actions, and developer event view.

## Next Work

1. Replace placeholder stage art with original Joi character assets.
2. Connect the browser observer to real screenshots/OCR.
3. Improve OK-WW status callbacks beyond process launch.
4. Add Codex permission requests back into the Joi approval UI.
5. Split model routing for text, vision, and expression.
