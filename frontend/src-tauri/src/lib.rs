mod backend;
mod window;

use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;

use tauri::{AppHandle, Manager, RunEvent, State};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

use backend::{BackendInfo, ExitInfo, LaunchSpec, Launched, Supervisor};

pub const GET_BACKEND_INFO: &str = "get_backend_info";

#[derive(Default)]
struct BackendSlot {
    info: Mutex<Option<BackendInfo>>,
    supervisor: Mutex<Option<Supervisor>>,
}

static SHUTTING_DOWN: AtomicBool = AtomicBool::new(false);

#[tauri::command]
fn get_backend_info(slot: State<'_, BackendSlot>) -> Result<BackendInfo, String> {
    let info = slot
        .info
        .lock()
        .map_err(|_| "后端状态不可用".to_string())?;
    info.clone().ok_or_else(|| "后端尚未就绪".to_string())
}

#[tauri::command]
fn open_chat_window(app: AppHandle) -> Result<(), String> {
    window::open_chat_window(&app).map_err(|error| error.to_string())
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .manage(BackendSlot::default())
        .invoke_handler(tauri::generate_handler![get_backend_info, open_chat_window])
        .setup(|app| {
            start_backend(app.handle().clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("桌面壳构建失败");
    app.run(handle_run_event);
}

fn start_backend(app: AppHandle) {
    thread::spawn(move || match launch_backend() {
        Ok(launched) => on_backend_ready(app, launched),
        Err(message) => {
            show_error(&app, "后端启动失败", &message);
            app.exit(1);
        }
    });
}

fn on_backend_ready(app: AppHandle, launched: Launched) {
    let info = launched.info.clone();
    let exit_app = app.clone();
    let supervisor = Supervisor::new(launched, move |exit: ExitInfo| {
        eprintln!("后端意外退出：代码 {:?}", exit.code);
        show_error(
            &exit_app,
            "后端已退出",
            "后端意外退出，应用将关闭。",
        );
        exit_app.exit(1);
    });
    let slot = app.state::<BackendSlot>();
    if let Ok(mut current) = slot.supervisor.lock() {
        *current = Some(supervisor);
    }
    if let Ok(mut current) = slot.info.lock() {
        *current = Some(info);
    }
    if let Err(error) = window::create_pet_window(&app) {
        show_error(&app, "窗口创建失败", &error.to_string());
        app.exit(1);
    }
}

fn launch_backend() -> Result<Launched, String> {
    let backend_dir = backend_dir();
    let spec = LaunchSpec {
        command: python_path(&backend_dir),
        args: vec!["main.py".to_string()],
        cwd: backend_dir,
        env: Vec::new(),
    };
    backend::launch(&spec, backend::STARTUP_TIMEOUT)
}

fn backend_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("backend")
}

fn python_path(backend_dir: &Path) -> PathBuf {
    if let Some(custom) = std::env::var_os("PET_PYTHON") {
        return PathBuf::from(custom);
    }
    let venv = backend_dir.join(".venv");
    if cfg!(windows) {
        venv.join("Scripts").join("python.exe")
    } else {
        venv.join("bin").join("python")
    }
}

fn show_error(app: &AppHandle, title: &str, message: &str) {
    app.dialog()
        .message(message)
        .title(title)
        .kind(MessageDialogKind::Error)
        .blocking_show();
}

fn handle_run_event(app: &AppHandle, event: RunEvent) {
    if let RunEvent::ExitRequested { code, api, .. } = event {
        if SHUTTING_DOWN.swap(true, Ordering::SeqCst) {
            return;
        }
        api.prevent_exit();
        let app = app.clone();
        let exit_code = code.unwrap_or(0);
        thread::spawn(move || {
            stop_backend(&app);
            app.exit(exit_code);
        });
    }
}

fn stop_backend(app: &AppHandle) {
    let supervisor = app
        .state::<BackendSlot>()
        .supervisor
        .lock()
        .ok()
        .and_then(|mut slot| slot.take());
    if let Some(supervisor) = supervisor {
        supervisor.stop(backend::STOP_TIMEOUT);
    }
}

#[cfg(test)]
mod tests {
    use serde_json::Value;

    const CAPABILITY: &str = include_str!("../capabilities/default.json");
    const CONFIG: &str = include_str!("../tauri.conf.json");

    const ALLOWED_PERMISSIONS: &[&str] = &[
        "core:default",
        "core:window:allow-start-dragging",
        "allow-get-backend-info",
        "allow-open-chat-window",
    ];
    const FORBIDDEN_PREFIXES: &[&str] = &[
        "fs:",
        "shell:",
        "process:",
        "http:",
        "opener:",
        "dialog:",
        "clipboard-manager:",
        "global-shortcut:",
        "autostart:",
    ];

    #[test]
    fn pet_window_only_gets_the_allowed_commands_and_dragging() {
        let capability: Value = serde_json::from_str(CAPABILITY).expect("capability json");
        let windows: Vec<&str> = capability["windows"]
            .as_array()
            .expect("windows")
            .iter()
            .map(|value| value.as_str().expect("window label"))
            .collect();
        assert_eq!(windows, vec!["pet"]);

        let permissions: Vec<&str> = capability["permissions"]
            .as_array()
            .expect("permissions")
            .iter()
            .map(|value| value.as_str().expect("permission"))
            .collect();
        for permission in &permissions {
            assert!(
                ALLOWED_PERMISSIONS.contains(permission),
                "悬浮窗不应拥有权限：{permission}"
            );
            assert!(
                !FORBIDDEN_PREFIXES
                    .iter()
                    .any(|prefix| permission.starts_with(prefix)),
                "悬浮窗不应拥有权限：{permission}"
            );
        }
    }

    #[test]
    fn windows_are_created_by_the_shell_after_the_backend_is_ready() {
        let config: Value = serde_json::from_str(CONFIG).expect("tauri config json");
        let windows = config["app"]["windows"].as_array().expect("windows");
        assert!(windows.is_empty(), "窗口须在后端就绪后由代码创建");
    }

    #[test]
    fn content_security_policy_is_declared() {
        let config: Value = serde_json::from_str(CONFIG).expect("tauri config json");
        let csp = config["app"]["security"]["csp"].as_str().expect("csp");
        assert!(csp.contains("default-src 'self'"));
        assert!(!csp.contains("unsafe-eval"));
    }
}
