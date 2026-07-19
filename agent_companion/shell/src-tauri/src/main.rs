#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tauri::Manager;

#[cfg(target_os = "macos")]
use objc2::MainThreadMarker;
#[cfg(target_os = "macos")]
use objc2_app_kit::{NSModalResponseOK, NSOpenPanel, NSWindow, NSWindowButton};

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            pick_attachments,
            set_macos_window_controls_visible
        ])
        .manage(CoreProcess(Mutex::new(None)))
        .setup(|app| {
            if std::env::var("JOI_DISABLE_AUTO_CORE").is_ok() {
                return Ok(());
            }
            if core_is_listening() {
                return Ok(());
            }
            let workspace = workspace_dir();
            let python = python_bin(&workspace);
            let data_home = app
                .path()
                .app_data_dir()
                .unwrap_or_else(|_| workspace.join("data").join("agent_companion"));
            let _ = std::fs::create_dir_all(&data_home);
            let child = Command::new("/bin/zsh")
                .args([
                    "-c",
                    "export XPC_SERVICE_NAME=0; unset __CFBundleIdentifier; exec \"$@\"",
                    "joi-core",
                ])
                .arg(python)
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
                .env("JOI_DATA_HOME", data_home.to_string_lossy().to_string())
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
                    if let Some(mut child) =
                        state.0.lock().expect("core process mutex poisoned").take()
                    {
                        let _ = child.kill();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Joi shell");
}

struct CoreProcess(Mutex<Option<Child>>);

#[tauri::command]
fn set_macos_window_controls_visible(
    window: tauri::WebviewWindow,
    visible: bool,
) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        let _mtm = MainThreadMarker::new()
            .ok_or_else(|| "window_controls_require_main_thread".to_string())?;
        let native_window = window
            .ns_window()
            .map_err(|error| format!("native_window_unavailable:{error}"))?;
        let native_window = unsafe { &*native_window.cast::<NSWindow>() };
        for kind in [
            NSWindowButton::CloseButton,
            NSWindowButton::MiniaturizeButton,
            NSWindowButton::ZoomButton,
        ] {
            if let Some(button) = native_window.standardWindowButton(kind) {
                button.setHidden(!visible);
            }
        }
        return Ok(());
    }

    #[cfg(not(target_os = "macos"))]
    {
        let _ = (window, visible);
        Ok(())
    }
}

#[tauri::command]
fn pick_attachments(kind: String) -> Result<Vec<String>, String> {
    #[cfg(target_os = "macos")]
    {
        let choose_folders = match kind.as_str() {
            "file" => false,
            "folder" => true,
            _ => return Err("unsupported_attachment_kind".to_string()),
        };
        let mtm = MainThreadMarker::new()
            .ok_or_else(|| "attachment_picker_requires_main_thread".to_string())?;
        let panel = NSOpenPanel::openPanel(mtm);
        panel.setCanChooseFiles(!choose_folders);
        panel.setCanChooseDirectories(choose_folders);
        panel.setAllowsMultipleSelection(true);
        if panel.runModal() != NSModalResponseOK {
            return Ok(Vec::new());
        }
        return Ok(panel
            .URLs()
            .to_vec()
            .into_iter()
            .filter_map(|url| url.path())
            .map(|path| path.to_string())
            .collect());
    }

    #[cfg(not(target_os = "macos"))]
    {
        let _ = kind;
        Err("attachment_picker_is_not_available_on_this_platform".to_string())
    }
}

fn python_bin(workspace: &PathBuf) -> PathBuf {
    let venv_python = workspace.join(".venv").join("bin").join("python");
    if venv_python.is_file() {
        return venv_python;
    }
    PathBuf::from("python3")
}

fn core_is_listening() -> bool {
    TcpListener::bind("127.0.0.1:8765").is_err()
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
