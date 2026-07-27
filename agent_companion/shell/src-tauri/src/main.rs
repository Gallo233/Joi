#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::fs::{File, OpenOptions};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;
use tauri::Manager;

#[cfg(target_os = "macos")]
use objc2::MainThreadMarker;
#[cfg(target_os = "macos")]
use objc2_app_kit::{NSModalResponseOK, NSOpenPanel, NSWindow, NSWindowButton};

const CORE_PROTOCOL_VERSION: u32 = 1;

#[derive(Clone, Serialize)]
struct CoreConnectionInfo {
    url: String,
    token: String,
    protocol_version: u32,
    instance_id: String,
    status: String,
    error: Option<String>,
}

impl Default for CoreConnectionInfo {
    fn default() -> Self {
        Self {
            url: String::new(),
            token: String::new(),
            protocol_version: CORE_PROTOCOL_VERSION,
            instance_id: String::new(),
            status: "idle".to_string(),
            error: None,
        }
    }
}

#[derive(Default)]
struct CoreRuntime {
    child: Mutex<Option<Child>>,
    connection: Mutex<CoreConnectionInfo>,
    ready_file: Mutex<Option<PathBuf>>,
    data_home: Mutex<Option<PathBuf>>,
    lifecycle: Mutex<()>,
}

#[derive(Deserialize)]
struct CoreReadyFile {
    product: String,
    protocol_version: u32,
    instance_id: String,
    ready: bool,
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            core_connection_info,
            restart_core,
            pick_attachments,
            set_macos_window_controls_visible
        ])
        .manage(CoreRuntime::default())
        .setup(|app| {
            let state = app.state::<CoreRuntime>();
            if std::env::var("JOI_DISABLE_AUTO_CORE").is_ok() {
                let url = std::env::var("JOI_CORE_URL")
                    .unwrap_or_else(|_| "ws://127.0.0.1:8765".to_string());
                *state
                    .connection
                    .lock()
                    .expect("core connection mutex poisoned") = CoreConnectionInfo {
                    url,
                    token: std::env::var("JOI_CORE_SESSION_TOKEN").unwrap_or_default(),
                    protocol_version: CORE_PROTOCOL_VERSION,
                    instance_id: std::env::var("JOI_CORE_INSTANCE_ID").unwrap_or_default(),
                    status: "external".to_string(),
                    error: None,
                };
                return Ok(());
            }

            let data_home = std::env::var_os("JOI_DATA_HOME")
                .filter(|value| !value.is_empty())
                .map(PathBuf::from)
                .unwrap_or_else(|| {
                    app.path()
                        .app_data_dir()
                        .unwrap_or_else(|_| workspace_dir().join("data").join("agent_companion"))
                });
            std::fs::create_dir_all(&data_home)?;
            *state
                .data_home
                .lock()
                .expect("core data-home mutex poisoned") = Some(data_home.clone());
            if let Err(error) = start_core(&state, &data_home) {
                set_core_error(&state, error);
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                if let Some(state) = window.try_state::<CoreRuntime>() {
                    let _guard = state
                        .lifecycle
                        .lock()
                        .expect("core lifecycle mutex poisoned");
                    stop_core(&state);
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Joi shell");
}

#[tauri::command]
fn core_connection_info(state: tauri::State<'_, CoreRuntime>) -> CoreConnectionInfo {
    refresh_core_status(&state);
    state
        .connection
        .lock()
        .expect("core connection mutex poisoned")
        .clone()
}

#[tauri::command]
fn restart_core(state: tauri::State<'_, CoreRuntime>) -> CoreConnectionInfo {
    let _guard = state
        .lifecycle
        .lock()
        .expect("core lifecycle mutex poisoned");
    stop_core(&state);
    let data_home = state
        .data_home
        .lock()
        .expect("core data-home mutex poisoned")
        .clone();
    match data_home {
        Some(data_home) => {
            if let Err(error) = start_core(&state, &data_home) {
                set_core_error(&state, error);
            }
        }
        None => set_core_error(&state, "Joi Core 数据目录不可用。".to_string()),
    }
    state
        .connection
        .lock()
        .expect("core connection mutex poisoned")
        .clone()
}

fn start_core(state: &CoreRuntime, data_home: &Path) -> Result<(), String> {
    let logs_dir = data_home.join("logs");
    std::fs::create_dir_all(&logs_dir).map_err(|error| error.to_string())?;
    let ready_file = data_home.join("core-ready.json");
    let _ = std::fs::remove_file(&ready_file);
    *state
        .ready_file
        .lock()
        .expect("core ready-file mutex poisoned") = Some(ready_file.clone());

    let (port, _asset_port) = allocate_loopback_port_pair()
        .map_err(|error| format!("无法分配 Joi Core 本地端口：{error}"))?;
    let token = random_hex(32);
    let instance_id = format!("core-{}-{}", std::process::id(), random_hex(8));
    *state
        .connection
        .lock()
        .expect("core connection mutex poisoned") = CoreConnectionInfo {
        url: format!("ws://127.0.0.1:{port}"),
        token: token.clone(),
        protocol_version: CORE_PROTOCOL_VERSION,
        instance_id: instance_id.clone(),
        status: "starting".to_string(),
        error: None,
    };

    let stdout = open_log(&logs_dir.join("joi-core.out.log"))?;
    let stderr = open_log(&logs_dir.join("joi-core.err.log"))?;
    let mut command = core_command(
        data_home,
        port,
        &token,
        &instance_id,
        &ready_file,
        stdout,
        stderr,
    )?;
    let child = command
        .spawn()
        .map_err(|error| format!("Joi Core 无法启动：{error}"))?;
    *state.child.lock().expect("core process mutex poisoned") = Some(child);
    Ok(())
}

fn refresh_core_status(state: &CoreRuntime) {
    let child_exit = {
        let mut child_guard = state.child.lock().expect("core process mutex poisoned");
        match child_guard
            .as_mut()
            .and_then(|child| child.try_wait().ok().flatten())
        {
            Some(status) => {
                *child_guard = None;
                Some(status.to_string())
            }
            None => None,
        }
    };
    if let Some(status) = child_exit {
        set_core_error(
            state,
            format!("Joi Core 启动失败（{status}）。请关闭其他 Joi 后重新连接。"),
        );
        return;
    }

    let ready_file = state
        .ready_file
        .lock()
        .expect("core ready-file mutex poisoned")
        .clone();
    let Some(ready_file) = ready_file else { return };
    let Ok(raw) = std::fs::read_to_string(ready_file) else {
        return;
    };
    let Ok(ready) = serde_json::from_str::<CoreReadyFile>(&raw) else {
        return;
    };
    let mut connection = state
        .connection
        .lock()
        .expect("core connection mutex poisoned");
    if ready.ready
        && ready.product == "joi-core"
        && ready.protocol_version == connection.protocol_version
        && ready.instance_id == connection.instance_id
    {
        connection.status = "ready".to_string();
        connection.error = None;
    }
}

fn set_core_error(state: &CoreRuntime, error: String) {
    let mut info = state
        .connection
        .lock()
        .expect("core connection mutex poisoned");
    info.status = "error".to_string();
    info.error = Some(error);
}

fn stop_core(state: &CoreRuntime) {
    if let Some(child) = state
        .child
        .lock()
        .expect("core process mutex poisoned")
        .take()
    {
        terminate_core_tree(child);
    }
    if let Some(ready_file) = state
        .ready_file
        .lock()
        .expect("core ready-file mutex poisoned")
        .take()
    {
        let _ = std::fs::remove_file(ready_file);
    }
}

fn terminate_core_tree(mut child: Child) {
    let process_group = child.id() as i32;
    #[cfg(unix)]
    unsafe {
        let _ = libc::kill(-process_group, libc::SIGTERM);
    }
    #[cfg(not(unix))]
    {
        let _ = child.kill();
    }

    for _ in 0..40 {
        let _ = child.try_wait();
        #[cfg(unix)]
        if !process_group_is_alive(process_group) {
            return;
        }
        #[cfg(not(unix))]
        if child.try_wait().ok().flatten().is_some() {
            return;
        }
        thread::sleep(Duration::from_millis(50));
    }

    #[cfg(unix)]
    unsafe {
        let _ = libc::kill(-process_group, libc::SIGKILL);
    }
    let _ = child.kill();
    let _ = child.wait();
}

#[cfg(unix)]
fn process_group_is_alive(process_group: i32) -> bool {
    let result = unsafe { libc::kill(-process_group, 0) };
    if result == 0 {
        return true;
    }
    std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

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

fn core_command(
    data_home: &Path,
    port: u16,
    token: &str,
    instance_id: &str,
    ready_file: &Path,
    stdout: File,
    stderr: File,
) -> Result<Command, String> {
    if let Some(sidecar) = installed_sidecar() {
        let mut command = Command::new(sidecar);
        command.args(core_args(data_home, port, instance_id, ready_file));
        command.env("JOI_CORE_SESSION_TOKEN", token);
        configure_core_process(&mut command, data_home, stdout, stderr);
        return Ok(command);
    }

    #[cfg(debug_assertions)]
    {
        let workspace = workspace_dir();
        let python = python_bin(&workspace);
        let mut command = Command::new(python);
        command.args(["-m", "agent_companion.core.main"]);
        command.args(core_args(&workspace, port, instance_id, ready_file));
        command.env("JOI_CORE_SESSION_TOKEN", token);
        command.current_dir(&workspace);
        command.env("PYTHONPATH", workspace.to_string_lossy().to_string());
        configure_core_process(&mut command, data_home, stdout, stderr);
        return Ok(command);
    }

    #[cfg(not(debug_assertions))]
    {
        let _ = (data_home, stdout, stderr);
        Err("joi_core_sidecar_missing: reinstall Joi from an official release".to_string())
    }
}

fn core_args(workspace: &Path, port: u16, instance_id: &str, ready_file: &Path) -> Vec<String> {
    vec![
        "--workspace".to_string(),
        workspace.to_string_lossy().to_string(),
        "--serve".to_string(),
        "--host".to_string(),
        "127.0.0.1".to_string(),
        "--port".to_string(),
        port.to_string(),
        "--instance-id".to_string(),
        instance_id.to_string(),
        "--ready-file".to_string(),
        ready_file.to_string_lossy().to_string(),
        "--parent-pid".to_string(),
        std::process::id().to_string(),
    ]
}

fn configure_core_process(command: &mut Command, data_home: &Path, stdout: File, stderr: File) {
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    command
        .env("JOI_DATA_HOME", data_home)
        .env_remove("XPC_SERVICE_NAME")
        .env_remove("__CFBundleIdentifier")
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr));
}

fn installed_sidecar() -> Option<PathBuf> {
    if let Some(path) = std::env::var_os("JOI_CORE_BIN").map(PathBuf::from) {
        if path.is_file() {
            return Some(path);
        }
    }
    let name = if cfg!(windows) {
        "joi-core.exe"
    } else {
        "joi-core"
    };
    std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(|parent| parent.join(name)))
        .filter(|path| path.is_file())
}

#[cfg(debug_assertions)]
fn python_bin(workspace: &Path) -> PathBuf {
    let unix_venv = workspace.join(".venv").join("bin").join("python");
    if unix_venv.is_file() {
        return unix_venv;
    }
    let windows_venv = workspace.join(".venv").join("Scripts").join("python.exe");
    if windows_venv.is_file() {
        return windows_venv;
    }
    PathBuf::from(if cfg!(windows) { "python" } else { "python3" })
}

fn open_log(path: &Path) -> Result<File, String> {
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|error| format!("unable to open {}: {error}", path.display()))
}

fn allocate_loopback_port_pair() -> std::io::Result<(u16, u16)> {
    for _ in 0..32 {
        let first = TcpListener::bind(("127.0.0.1", 0))?;
        let port = first.local_addr()?.port();
        if port == u16::MAX {
            continue;
        }
        if let Ok(second) = TcpListener::bind(("127.0.0.1", port + 1)) {
            drop(second);
            drop(first);
            return Ok((port, port + 1));
        }
    }
    Err(std::io::Error::new(
        std::io::ErrorKind::AddrNotAvailable,
        "could not allocate adjacent loopback ports",
    ))
}

fn random_hex(byte_count: usize) -> String {
    let mut bytes = vec![0_u8; byte_count];
    rand::fill(bytes.as_mut_slice());
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[cfg(debug_assertions)]
fn workspace_dir() -> PathBuf {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest
        .parent()
        .and_then(|shell| shell.parent())
        .and_then(|agent_companion| agent_companion.parent())
        .map(PathBuf::from)
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")))
}

#[cfg(not(debug_assertions))]
fn workspace_dir() -> PathBuf {
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}
