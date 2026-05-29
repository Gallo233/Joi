# Joi Windows First-Run Checklist

This checklist is for a fresh Windows machine or a desktop shortcut that opens a blank browser page, cannot connect to `127.0.0.1`, or cannot start the Joi shell.

## 1. Run Doctor

Create a local config from the checked-in example when needed:

```powershell
.\start_joi.bat -Setup
```

```powershell
.\start_joi.bat -Doctor
```

Setup creates `config.yaml` only if it is missing, and it never writes secrets. The doctor checks the local Python environment, required Python packages, optional OCR/audio packages, Node/Rust frontend tooling, shell build state, `config.yaml`, Tesseract availability, and whether the Core port is already in use.

For machine-readable output:

```powershell
.\.venv\Scripts\python.exe tools\joi_doctor.py --json
```

## 2. Prepare Python

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Optional OCR:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-ocr.txt
```

Optional system-audio transcription:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-audio.txt
```

OCR also needs the Tesseract executable and the language packs you plan to use.

## 3. Prepare Config

```powershell
Copy-Item config.example.yaml config.yaml
```

Keep credentials in environment variables or `secrets.yaml`. Do not commit `config.yaml`, `secrets.yaml`, screenshots, audio, logs, or local memory data.

At minimum:

- `llm.use_mock: true` can start a limited local demo.
- Real chat needs `llm.use_mock: false`, a model, base URL, and API key.
- Voice input needs `asr.enabled: true` plus ASR provider credentials.
- Voice output needs `tts.enabled: true` plus a reachable TTS provider.
- OCR needs `requirements-ocr.txt`, Tesseract, and `ocr.tesseract_cmd` when Tesseract is not in PATH.

## 4. Prepare Shell

```powershell
cd agent_companion\shell
npm install
npm run build
npm run tauri -- build --debug
```

If a release shell exists, `start_joi.bat` opens it directly. If no built shell exists but npm/Rust are available, the launcher falls back to Tauri dev mode.

Packaging metadata smoke:

```powershell
.\.venv\Scripts\python.exe tools\packaging_smoke.py
```

This also checks the Windows release privacy policy so local `config.yaml`, `secrets.yaml`, runtime data, logs, dependency folders, and build caches are not eligible for release packaging.

Setup wizard dry-run:

```powershell
.\.venv\Scripts\python.exe tools\windows_setup_wizard.py
```

Add `--apply` to create `config.yaml` from `config.example.yaml` when it is missing.

MVP demo readiness:

```powershell
.\.venv\Scripts\python.exe tools\mvp_demo_check.py
```

This prints safe prompts and readiness notes for the Watch Together, Codex coding, and OK-WW game-skill demos. It does not click, type, start Codex, or launch OK-WW.

Offline provider preflight:

```powershell
.\.venv\Scripts\python.exe tools\provider_preflight.py
```

This prints only sanitized provider state for text, vision, expression, ASR, TTS, OCR, Computer Use, and audit/verification. It does not test network endpoints or print API keys, URLs, paths, logs, or model files.

Aggregated release readiness:

```powershell
.\.venv\Scripts\python.exe tools\windows_release_check.py --allow-missing-exe
```

Remove `--allow-missing-exe` after the Tauri release shell exists to require a publishable portable package.

Portable release zip:

```powershell
npm run tauri -- build
cd ..\..
.\.venv\Scripts\python.exe tools\package_windows_release.py
```

## 5. Launch

```powershell
.\start_joi.bat
```

Core logs are written to `logs\joi_core.out.log` and `logs\joi_core.err.log`.
