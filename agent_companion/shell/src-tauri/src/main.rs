#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tauri::Manager;

fn main() {
    tauri::Builder::default()
        .manage(CoreProcess(Mutex::new(None)))
        .setup(|app| {
            if std::env::var("JOI_DISABLE_AUTO_CORE").is_ok() {
                return Ok(());
            }
            let workspace = workspace_dir();
            let python = python_bin(&workspace);
            let child = Command::new(python)
                .args([
                    "-m",
                    "agent_companion.core.main",
                    "--workspace",
                    workspace.to_string_lossy().as_ref(),
                    "--serve",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8765",
                ])
                .current_dir(&workspace)
                .env("PYTHONPATH", workspace.to_string_lossy().to_string())
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn();
            if let Ok(child) = child {
                if let Some(state) = app.try_state::<CoreProcess>() {
                    *state.0.lock().expect("core process mutex poisoned") = Some(child);
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                if let Some(state) = window.try_state::<CoreProcess>() {
                    if let Some(mut child) = state.0.lock().expect("core process mutex poisoned").take() {
                        let _ = child.kill();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Joi shell");
}

struct CoreProcess(Mutex<Option<Child>>);

fn python_bin(workspace: &PathBuf) -> PathBuf {
    let venv_python = workspace.join(".venv").join("bin").join("python");
    if venv_python.is_file() {
        return venv_python;
    }
    PathBuf::from("python3")
}

fn workspace_dir() -> PathBuf {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest
        .parent()
        .and_then(|shell| shell.parent())
        .and_then(|agent_companion| agent_companion.parent())
        .map(PathBuf::from)
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")))
}
