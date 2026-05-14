# Joi Windows Toolchain and Bridge

## Install

Run from the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\install_windows_toolchain.ps1
```

The script installs portable Node.js with npm under `D:\codex游戏\toolchains\node`, installs Rust through rustup for the current user, and runs `npm install` in `agent_companion/shell`.

If Tauri reports that MSVC is missing, install the Windows C++ Build Tools:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\install_windows_msvc_build_tools.ps1
```

## Run

Start the Python Core bridge:

```powershell
.\start_agent_core.bat
```

Start the Tauri shell in another terminal:

```powershell
.\start_agent_shell_dev.bat
```

Joi shell connects to `ws://127.0.0.1:8765`.

For a one-shot build check:

```powershell
cd .\agent_companion\shell
D:\codex游戏\toolchains\node\npm.cmd run build
D:\codex游戏\toolchains\node\npm.cmd run tauri -- build --debug
```

## JSON-RPC

Send a user command:

```json
{"jsonrpc":"2.0","id":"1","method":"user.message","params":{"text":"陪我看当前网页"}}
```

Approve a gated action:

```json
{"jsonrpc":"2.0","id":"2","method":"approval.resolve","params":{"task_id":"task-xxx","approved":true}}
```

The server emits `agent.event` notifications. Each event separates:

- `agent_state`: internal structured state for planning.
- `display_card`: human-readable task card.
- `voice_line`: short line for role speech.
- `agent_state.voice_audio_path`: optional GPT-SoVITS audio output for Tauri playback.
