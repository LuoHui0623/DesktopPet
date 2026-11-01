mod backend;
mod menu;
mod perception;
mod window;

use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;

use tauri::{AppHandle, Manager, RunEvent, State};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};
use tauri_plugin_window_state::StateFlags;

use backend::{BackendInfo, ExitInfo, LaunchSpec, Launched, Supervisor};

pub const GET_BACKEND_INFO: &str = "get_backend_info";

// 尺寸由代码决定（D-44）：旧版本存下的 320×240 不能覆盖 320×360，位置照常记忆。
const WINDOW_STATE_FLAGS: StateFlags = StateFlags::POSITION
    .union(StateFlags::MAXIMIZED)
    .union(StateFlags::VISIBLE)
    .union(StateFlags::DECORATIONS)
    .union(StateFlags::FULLSCREEN);

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

// 必须 async：同步命令在 Windows 里创建 WebView 窗口会死锁（见 window_opening_commands_are_async）。
#[tauri::command]
async fn open_chat_window(app: AppHandle, history: Option<bool>) -> Result<(), String> {
    window::open_chat_window(&app, history.unwrap_or(false)).map_err(|error| error.to_string())
}

#[tauri::command]
async fn open_status_window(app: AppHandle) -> Result<(), String> {
    window::open_status_window(&app).map_err(|error| error.to_string())
}

#[tauri::command]
async fn open_settings_window(app: AppHandle) -> Result<(), String> {
    window::open_settings_window(&app).map_err(|error| error.to_string())
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(
            tauri_plugin_window_state::Builder::default()
                .with_state_flags(WINDOW_STATE_FLAGS)
                .build(),
        )
        .manage(BackendSlot::default())
        .manage(perception::PerceptionRuntime::default())
        .on_menu_event(menu::handle_menu_event)
        .invoke_handler(tauri::generate_handler![
            get_backend_info,
            open_chat_window,
            open_status_window,
            open_settings_window,
            perception::start_perception,
            perception::update_perception_config,
            perception::stop_perception,
            menu::show_context_menu,
            menu::exit_app
        ])
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
        env: backend_env(),
    };
    backend::launch(&spec, backend::STARTUP_TIMEOUT)
}

/// 开发构建（`npm run dev`）由受信任的桌面壳注入开发标记；正式包不设该变量。
/// 状态窗口的完整正文展示边界因此不依赖前端自身判断。
fn backend_env() -> Vec<(String, String)> {
    if cfg!(debug_assertions) {
        vec![("PET_DEV_MODE".to_string(), "1".to_string())]
    } else {
        Vec::new()
    }
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
    use tauri_plugin_window_state::StateFlags;

    use crate::WINDOW_STATE_FLAGS;

    const CAPABILITY: &str = include_str!("../capabilities/default.json");
    const CHAT_CAPABILITY: &str = include_str!("../capabilities/chat.json");
    const STATUS_CAPABILITY: &str = include_str!("../capabilities/status.json");
    const SETTINGS_CAPABILITY: &str = include_str!("../capabilities/settings.json");
    const CONFIG: &str = include_str!("../tauri.conf.json");

    const CHAT_ALLOWED_PERMISSIONS: &[&str] = &[
        "core:default",
        "allow-get-backend-info",
        "allow-open-settings-window",
    ];
    const STATUS_ALLOWED_PERMISSIONS: &[&str] = &["core:default", "allow-get-backend-info"];
    const SETTINGS_ALLOWED_PERMISSIONS: &[&str] = &["core:default", "allow-get-backend-info"];

    const ALLOWED_PERMISSIONS: &[&str] = &[
        "core:default",
        "core:window:allow-start-dragging",
        "allow-get-backend-info",
        "allow-open-chat-window",
        "allow-open-status-window",
        "allow-show-context-menu",
        "allow-exit-app",
        "allow-start-perception",
        "allow-update-perception-config",
        "allow-stop-perception",
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

    fn assert_window_permissions(capability_json: &str, label: &str, allowed: &[&str]) {
        let capability: Value = serde_json::from_str(capability_json).expect("capability json");
        let windows: Vec<&str> = capability["windows"]
            .as_array()
            .expect("windows")
            .iter()
            .map(|value| value.as_str().expect("window label"))
            .collect();
        assert_eq!(windows, vec![label]);

        let permissions: Vec<&str> = capability["permissions"]
            .as_array()
            .expect("permissions")
            .iter()
            .map(|value| value.as_str().expect("permission"))
            .collect();
        for permission in &permissions {
            assert!(
                allowed.contains(permission),
                "{label} 窗口不应拥有权限：{permission}"
            );
        }
    }

    #[test]
    fn chat_window_only_gets_its_allowed_commands() {
        assert_window_permissions(CHAT_CAPABILITY, "chat", CHAT_ALLOWED_PERMISSIONS);
    }

    #[test]
    fn status_window_only_gets_backend_info() {
        assert_window_permissions(STATUS_CAPABILITY, "status", STATUS_ALLOWED_PERMISSIONS);
    }

    #[test]
    fn settings_window_only_gets_backend_info() {
        assert_window_permissions(SETTINGS_CAPABILITY, "settings", SETTINGS_ALLOWED_PERMISSIONS);
    }

    #[test]
    fn windows_are_created_by_the_shell_after_the_backend_is_ready() {
        let config: Value = serde_json::from_str(CONFIG).expect("tauri config json");
        let windows = config["app"]["windows"].as_array().expect("windows");
        assert!(windows.is_empty(), "窗口须在后端就绪后由代码创建");
    }

    /// Windows 上在同步命令里创建 WebView 窗口会死锁（wry issue #583）：
    /// 主线程同步等待窗口创建，而窗口创建又要回到主线程，IPC 频道被永久阻塞。
    /// 因此所有创建窗口的命令都必须是 `async fn`（跑在异步运行时，不占用主线程）。
    #[test]
    fn window_opening_commands_are_async() {
        fn assert_async_command<F, Fut>(_command: F)
        where
            F: Fn(tauri::AppHandle) -> Fut,
            Fut: std::future::Future,
        {
        }

        assert_async_command(|app| crate::open_chat_window(app, None));
        assert_async_command(|app| crate::open_chat_window(app, Some(true)));
        assert_async_command(crate::open_status_window);
        assert_async_command(crate::open_settings_window);
    }

    /// 开发构建由桌面壳向后端注入 `PET_DEV_MODE`，正式构建不注入任何展示开关。
    #[test]
    fn dev_build_marks_the_backend_for_development_mode() {
        #[cfg(debug_assertions)]
        assert_eq!(crate::backend_env(), vec![("PET_DEV_MODE".to_string(), "1".to_string())]);
        #[cfg(not(debug_assertions))]
        assert!(crate::backend_env().is_empty());
    }

    #[test]
    fn pet_window_size_comes_from_code_not_saved_state() {
        assert_eq!(crate::window::PET_WINDOW_SIZE, (320.0, 360.0));
        assert!(
            !WINDOW_STATE_FLAGS.contains(StateFlags::SIZE),
            "尺寸不应由保存的窗口状态恢复"
        );
        assert!(WINDOW_STATE_FLAGS.contains(StateFlags::POSITION));
    }

    #[test]
    fn content_security_policy_is_declared() {
        let config: Value = serde_json::from_str(CONFIG).expect("tauri config json");
        let csp = config["app"]["security"]["csp"].as_str().expect("csp");
        assert!(csp.contains("default-src 'self'"));
        assert!(!csp.contains("unsafe-eval"));
    }

    /// Tauri 的事件名只允许字母数字与 `-` `/` `:` `_`，含点会被前端 `listen` 直接拒绝；
    /// 前端 src/service/tauri-events.ts 里的常量必须与这里逐字一致。
    #[test]
    fn event_names_follow_the_tauri_charset() {
        for name in [
            crate::menu::MENU_EVENT,
            crate::perception::SAMPLE_EVENT,
            crate::perception::CANDIDATE_EVENT,
        ] {
            assert!(
                !name.is_empty()
                    && name
                        .chars()
                        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '/' | ':' | '_')),
                "事件名不符合 Tauri 允许的字符集：{name}"
            );
        }
        assert_eq!(crate::menu::MENU_EVENT, "menu-action");
        assert_eq!(crate::perception::SAMPLE_EVENT, "perception-sample");
        assert_eq!(crate::perception::CANDIDATE_EVENT, "perception-candidate");
    }
}
