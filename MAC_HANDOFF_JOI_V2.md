# Joi v2 macOS Desktop Companion: Handover & Architecture Handoff

Welcome to the ultimate Joi v2 platform handoff. This document summarizes all architectural changes, native macOS adaptations, current runtime states, and direct engineering next steps to hand over to **Hermes**.

---

## 📍 Local Project Information

*   **Local Project Address**: `/Users/liujialuo/.gemini/antigravity/scratch/Joi`
*   **Git Active Branch**: `main` (staged changes checked out from `origin/win-desktop-fixes` for the frontend shell).
*   **Target Upstream Fixes Branch**: `origin/win-desktop-fixes` (fully merged and integrated).
*   **Active Python Environment**: `/Users/liujialuo/.gemini/antigravity/scratch/Joi/.venv`
*   **Active Backend Server Process**: Spanned via `.venv/bin/python3 -m agent_companion.core.main --serve` in the background (active on port `8765`).

---

## 🎨 Front-End Redesign: Route A Glassmorphism & macOS Adapters

We replaced the local shell with the latest optimized Windows frontend fixes and adapted it flawlessly for macOS:

1.  **Spotlight-style Drag Handle (`styles.css` & `App.vue`)**
    *   Added `-webkit-app-region: drag;` directly to Joi's `.titlebar` class.
    *   **Mac Native Integration**: This unlocks native macOS **Three-Finger Drag (三指拖移)**, trackpad tap-dragging, and native window double-click titlebar zoom.
    *   Interactive elements (Traffic lights close/minimize/zoom, Speak toggles, etc.) have `-webkit-app-region: no-drag;` to maintain clickability.
2.  **Zero-Frame Frameless Transparency (`tauri.conf.json` & `Cargo.toml`)**
    *   Enabled `"macOSPrivateApi": true` in `tauri.conf.json` under `"app"`.
    *   Enabled the `"macos-private-api"` feature flag under `tauri` dependencies inside Rust `Cargo.toml`.
    *   **Visual Outcome**: Completely eliminates ugly solid margins/black window corners, giving a transparent canvas overlay.
3.  **Scroll Overflows Patched**
    *   Rewrote Flexbox containers in `styles.css` using `flex: 1 1 auto; min-height: 0; overflow-y: auto;` rules. All cabins (Chat, Tasks, Inspector) now scroll independently; the input field remains visible.
4.  **Mascot Click-Dragging**
    *   In compact mode, quick clicks toggle Joi's mini-dashboard. Click-and-drag movements trigger Tauri's `getCurrentWindow().startDragging()` after a 6px mouse movement, allowing smooth pet movement.

---

## ⚙️ Back-End Refactoring: Thread-Safe macOS Native Computer Use

We replaced Windows-first dependencies with clean, high-performance native macOS adapters using a **Platform Factory Pattern**:

1.  **Platform Abstraction Factory (`platform_factory.py`)**
    *   Dynamically resolves OS backends via `sys.platform`. Integrates transparently into `computer.py`, `targeting.py`, and `screen_observe.py`.
2.  **Thread-Safe macOS Clipboard (`computer_use/mac.py`)**
    *   **Crucial Fix**: Removed all PySide6/Qt GUI references from `mac.py`. Joi's WebSocket server handles requests asynchronously on a background thread. Instantiating PySide6 GUI elements on a non-main thread was causing Cocoa to crash with a fatal `NSInternalInconsistencyException`.
    *   **Native Interop**: We now exclusively use standard macOS **`pbcopy`** and **`pbpaste`** via `subprocess`. This is 100% thread-safe, fast, and eliminates all Qt thread-assertion risks.
3.  **System-Native Spotlight Application Launcher (`planner.py`)**
    *   Added `_looks_like_open_app` and `_parse_app_name` to Joi's rule-based planner.
    *   When the user says *"打开 Safari"*, Joi builds an elegant multi-step OS keyboard automation plan:
        1.  Presses `cmd+space` (Spotlight search) using AppleScript virtual keycode `49` (Spacebar).
        2.  Types the app name `safari`.
        3.  Presses `enter` to launch it.
4.  **Retina Screen Capture & Focus Observes (`vision/mac.py` & `windows_focus_mac.py`)**
    *   Captures desktop views silently using native macOS `screencapture -x`.
    *   Temporarily hides Joi's Tauri shell via AppleScript (`set visible of process "Joi" to false`) to snapshot the background, restoring Joi instantly to focus on completion.

---

## 🧪 Verification Commands

Use these commands in Joi's folder to check consistency:

```bash
# 1. Run full PySide6-free Python core test suite
.venv/bin/python run_agent_companion_tests.py

# 2. Check Tauri Rust compile integrity
cd agent_companion/shell/src-tauri
cargo check

# 3. Check Vue 3 / TS Frontend compilation
cd ../
npm run build
```

---

## 🚀 Hermes Next-Step Checklist

Here are the direct follow-ups recommended for **Hermes** to build on top of our stable foundation:

*   `[ ]` **Refine Active Window Cropping**: In `mac.py`, further optimize active-window obs coordinates on multi-monitor or notch configurations.
*   `[ ]` **Add Application Launching Fallback**: If Spotlight does not open the app (e.g. customized hotkeys), fall back to `subprocess.Popen(["open", "-a", app_name])` as a backup.
*   `[ ]` **Settings UI Integration**: Connect the Speak Toggle and Accessories closets directly to `runtime_config` for persistent session storage.
*   `[ ]` **Codex Engineering Verification**: Further integrate the auditable pipeline so Codex can run shell commands behind visual approvals in the Task cabin.

---

Joi v2 is primed, stable, and ready for Hermes to take it to the next level!
