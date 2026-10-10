// Desktop shell: starts the Python backend, hands its port/token to the UI and
// makes sure it goes away with the window. The backend binds its own children
// (AI CLIs, MCP servers) to a Windows job object, so killing it cleans up all.
// On macOS the backend leads its own process group, which is ended as a whole.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use tauri::{Manager, RunEvent, State};

#[derive(Clone, Serialize, serde::Deserialize)]
struct BackendInfo {
    port: u16,
    token: String,
}

#[derive(Default)]
struct Backend {
    child: Option<Child>,
    info: Option<BackendInfo>,
    error: Option<String>,
}

type Shared = Arc<Mutex<Backend>>;

#[derive(Serialize)]
struct ShellStatus {
    info: Option<BackendInfo>,
    error: Option<String>,
}

#[cfg(windows)]
const LOG_HINT: &str = "ログ: %APPDATA%\\RoxyBlenderAI\\logs\\app.log";
#[cfg(target_os = "macos")]
const LOG_HINT: &str = "ログ: ~/Library/Application Support/RoxyBlenderAI/logs/app.log";
#[cfg(not(any(windows, target_os = "macos")))]
const LOG_HINT: &str = "ログ: ~/.local/share/RoxyBlenderAI/logs/app.log";

const SIDECAR: &str = if cfg!(windows) { "roxy-ai-backend.exe" } else { "roxy-ai-backend" };

/// Where the backend comes from, in order:
/// 1. ROXY_BACKEND_PYTHON (a python that has the backend installed)
/// 2. roxy-ai-backend(.exe) next to this executable (bundled PyInstaller sidecar)
/// 3. the development checkout's backend/.venv
fn backend_command() -> Result<Command, String> {
    let pid = std::process::id().to_string();
    if let Ok(py) = std::env::var("ROXY_BACKEND_PYTHON") {
        let mut c = Command::new(py);
        c.args(["-m", "roxy_ai", "--parent-pid", &pid]);
        return Ok(c);
    }
    if let Some(dir) = std::env::current_exe().ok().and_then(|p| p.parent().map(|d| d.to_path_buf())) {
        let sidecar = dir.join(SIDECAR);
        if sidecar.is_file() {
            let mut c = Command::new(sidecar);
            c.args(["--parent-pid", &pid]);
            return Ok(c);
        }
    }
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..").join("backend");
    let py = if cfg!(windows) {
        dev.join(".venv").join("Scripts").join("python.exe")
    } else {
        dev.join(".venv").join("bin").join("python")
    };
    if py.is_file() {
        let mut c = Command::new(py);
        c.current_dir(&dev).args(["-m", "roxy_ai", "--parent-pid", &pid]);
        return Ok(c);
    }
    Err("バックエンドが見つかりません。README の「セットアップ」に従って backend/.venv を作成するか、ROXY_BACKEND_PYTHON を設定してください。".into())
}

fn spawn_backend(shared: &Shared) {
    let mut cmd = match backend_command() {
        Ok(c) => c,
        Err(e) => {
            shared.lock().unwrap().error = Some(e);
            return;
        }
    };
    cmd.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0); // the backend and everything it starts share one group
    }
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            shared.lock().unwrap().error = Some(format!("バックエンドを起動できませんでした: {e}"));
            return;
        }
    };
    let stdout = child.stdout.take();
    {
        let mut b = shared.lock().unwrap();
        b.child = Some(child);
        b.info = None;
        b.error = None;
    }
    let shared = shared.clone();
    std::thread::spawn(move || {
        if let Some(out) = stdout {
            // Keep draining stdout for the backend's whole life so it never blocks.
            for line in BufReader::new(out).lines().map_while(Result::ok) {
                if let Some(json) = line.strip_prefix("ROXY_READY ") {
                    if let Ok(info) = serde_json::from_str::<BackendInfo>(json) {
                        shared.lock().unwrap().info = Some(info);
                    }
                }
            }
        }
        let mut b = shared.lock().unwrap();
        if b.info.is_none() && b.error.is_none() {
            b.error = Some(format!("バックエンドが起動途中で終了しました。{LOG_HINT}"));
        }
    });
}

fn kill_backend(shared: &Shared) {
    let mut b = shared.lock().unwrap();
    if let Some(mut child) = b.child.take() {
        #[cfg(unix)]
        {
            // Let the backend shut its AI sessions down, then end whatever is left in its group.
            let pid = child.id().to_string();
            let _ = Command::new("kill").args(["-TERM", &pid]).status();
            for _ in 0..30 {
                if matches!(child.try_wait(), Ok(Some(_))) {
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
            let _ = Command::new("kill").args(["-KILL", "--", &format!("-{pid}")]).status();
        }
        let _ = child.kill();
        let _ = child.wait();
    }
    b.info = None;
}

#[tauri::command]
fn backend_info(state: State<'_, Shared>) -> ShellStatus {
    let mut b = state.lock().unwrap();
    // Notice a backend that died after starting.
    if let Some(child) = b.child.as_mut() {
        if let Ok(Some(status)) = child.try_wait() {
            b.child = None;
            b.info = None;
            b.error = Some(format!("バックエンドが終了しました（{status}）。{LOG_HINT}"));
        }
    }
    ShellStatus { info: b.info.clone(), error: b.error.clone() }
}

#[tauri::command]
fn restart_backend(state: State<'_, Shared>) {
    kill_backend(&state);
    spawn_backend(&state);
}

fn main() {
    let shared: Shared = Arc::new(Mutex::new(Backend::default()));
    spawn_backend(&shared);
    let app = tauri::Builder::default()
        .manage(shared)
        .invoke_handler(tauri::generate_handler![backend_info, restart_backend])
        .build(tauri::generate_context!())
        .expect("error while building Roxy Blender AI");
    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            kill_backend(&handle.state::<Shared>());
        }
    });
}
