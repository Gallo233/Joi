# Joi

Joi is a Windows-first multimodal agent companion: a character-fronted assistant for coding, watching screen content, and launching auditable local skills.

The current repository is intentionally focused on the Joi main line.

## Current Scope

- Python Agent Core: planner, policy gate, event bus, memory store, character harness, tool registry.
- Tool adapters: Codex, browser observation/search queue, OK-WW game skill, MCP discovery, safe file reads.
- Tauri/Vue Shell: product UI, character stage, chat stream, task cards, approval actions, developer event view.
- Computer Use audit: developer mode shows sanitized observe/target/approval/action/verification timelines with before/after screenshots and local image-change verification for confirmed local computer actions.
- Runtime provider status: developer mode shows read-only ASR, TTS, OCR, text/vision/expression model, Computer Use, and audit/verification status without exposing secrets, endpoints, logs, screenshot/audio filenames, or local model paths.
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

Optional OCR support for visible text grounding:

```powershell
.\.venv\Scripts\pip install -r requirements-ocr.txt
```

OCR also needs the Tesseract executable installed on the system. Joi does not bundle Tesseract or its language packs. For Chinese OCR, install the appropriate Tesseract language data when you configure OCR for Chinese screenshots. If OCR is not installed or times out, Joi still saves screenshots and continues Watch Together with the available visual summary.

The developer runtime status reflects both Python package availability and local Tesseract runtime availability; OCR is shown as ready only after Pillow, `pytesseract`, the `tesseract` executable, and a lightweight version probe all pass.

The lightweight visual detector and Computer Use image-diff verifier use local screenshot data only. Pillow is used for real PNG/JPEG screenshots when installed; synthetic PPM fixtures keep the committed regression suite dependency-light and reproducible.

Visual detector and image verification fixture workflow:

```powershell
.\.venv\Scripts\python.exe tools\generate_visual_fixtures.py
.\.venv\Scripts\python.exe tools\eval_visual_detector.py
```

Local private visual evals can live in `data/local_visual_eval/visual_cases.local.json`, and local private Computer Use image-diff evals can live in `data/local_visual_eval/image_diff_cases.local.json`, with images next to those files. This directory is ignored by Git and should be used for real game/browser screenshots that may contain private account or browsing data. If either local manifest is missing, the eval reports it as skipped instead of failing.

Optional Windows accessibility-tree support for UI control grounding:

```powershell
.\.venv\Scripts\pip install -r requirements-accessibility.txt
```

This enables best-effort Windows UI Automation snapshots for the active window. It is read-only and used to improve semantic target candidates such as buttons and links. If the package is not installed, Joi falls back to screenshot/OCR grounding.

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

Developer mode includes a read-only runtime status panel for provider availability and last safe error categories. Change providers in local config files for now; the shell does not write provider settings or secrets.

## Repository Layout

```text
agent_companion/          Python Core and Tauri/Vue shell
docs/                     Roadmap, changelog, known issues, feedback log
tools/                    Setup, smoke test, and launcher helpers
run_agent_companion_tests.py
start_joi.bat
config.example.yaml
```
