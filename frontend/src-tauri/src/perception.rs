use std::collections::HashSet;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, State};

// Tauri 只接受字母数字与 `-` `/` `:` `_` 的事件名，带点会被 `listen` 直接拒绝，
// 因此自定义事件一律用短横线分段；前端 perception.service.ts 的同名常量必须一致。
pub const SAMPLE_EVENT: &str = "perception-sample";
pub const CANDIDATE_EVENT: &str = "perception-candidate";
pub const FAST_INTERVAL: Duration = Duration::from_secs(5);
pub const PROCESS_INTERVAL: Duration = Duration::from_secs(30);
const POLL_INTERVAL: Duration = Duration::from_millis(200);

const SHELL_PROCESSES: &[&str] = &[
    "explorer",
    "applicationframehost",
    "textinputhost",
    "shellexperiencehost",
    "searchhost",
    "startmenuexperiencehost",
    "widgets",
    "lockapp",
    "logonui",
    "taskmgr",
];

const SELF_PROCESSES: &[&str] = &[
    "pet-shell",
    "pet",
    "chat",
    "settings",
    "backend",
    "python",
    "pythonw",
];

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct SamplePayload {
    kind: &'static str,
    value: serde_json::Value,
}

#[derive(Clone, Serialize)]
struct CandidatePayload {
    exe: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PerceptionConfig {
    pub foreground_enabled: bool,
    pub process_whitelist: Vec<String>,
    pub process_muted: Vec<String>,
}

impl PerceptionConfig {
    fn whitelist(&self) -> HashSet<String> {
        normalized_names(&self.process_whitelist)
    }

    fn muted(&self) -> HashSet<String> {
        normalized_names(&self.process_muted)
    }
}

fn normalized_names(names: &[String]) -> HashSet<String> {
    names
        .iter()
        .filter_map(|name| exe_stem(name))
        .collect::<HashSet<_>>()
}

fn exe_stem(raw: &str) -> Option<String> {
    let name = raw.trim().to_ascii_lowercase();
    let stem = name.strip_suffix(".exe").unwrap_or(&name);
    (!stem.is_empty()).then(|| stem.to_string())
}

fn ignored_process(exe: &str) -> bool {
    SHELL_PROCESSES.contains(&exe) || SELF_PROCESSES.contains(&exe)
}

struct Running {
    stop: Arc<AtomicBool>,
    config: Arc<Mutex<PerceptionConfig>>,
    handle: JoinHandle<()>,
}

#[derive(Default)]
pub struct PerceptionRuntime {
    inner: Mutex<Option<Running>>,
}

impl PerceptionRuntime {
    fn start(&self, app: AppHandle, config: PerceptionConfig) -> Result<(), String> {
        let mut slot = self
            .inner
            .lock()
            .map_err(|_| "感知状态不可用".to_string())?;
        if let Some(running) = slot.as_ref() {
            *running
                .config
                .lock()
                .map_err(|_| "感知配置不可用".to_string())? = config;
            return Ok(());
        }
        let stop = Arc::new(AtomicBool::new(false));
        let shared = Arc::new(Mutex::new(config));
        let handle = spawn_collector(app, Arc::clone(&shared), Arc::clone(&stop));
        *slot = Some(Running {
            stop,
            config: shared,
            handle,
        });
        Ok(())
    }

    fn update(&self, config: PerceptionConfig) -> Result<(), String> {
        let slot = self
            .inner
            .lock()
            .map_err(|_| "感知状态不可用".to_string())?;
        let Some(running) = slot.as_ref() else {
            return Ok(());
        };
        *running
            .config
            .lock()
            .map_err(|_| "感知配置不可用".to_string())? = config;
        Ok(())
    }

    fn stop(&self) -> Result<(), String> {
        let running = self
            .inner
            .lock()
            .map_err(|_| "感知状态不可用".to_string())?
            .take();
        if let Some(running) = running {
            running.stop.store(true, Ordering::SeqCst);
            let _ = running.handle.join();
        }
        Ok(())
    }
}

#[tauri::command]
pub fn start_perception(
    app: AppHandle,
    runtime: State<'_, PerceptionRuntime>,
    config: PerceptionConfig,
) -> Result<(), String> {
    runtime.start(app, config)
}

#[tauri::command]
pub fn update_perception_config(
    runtime: State<'_, PerceptionRuntime>,
    config: PerceptionConfig,
) -> Result<(), String> {
    runtime.update(config)
}

#[tauri::command]
pub fn stop_perception(runtime: State<'_, PerceptionRuntime>) -> Result<(), String> {
    runtime.stop()
}

fn spawn_collector(
    app: AppHandle,
    config: Arc<Mutex<PerceptionConfig>>,
    stop: Arc<AtomicBool>,
) -> JoinHandle<()> {
    thread::spawn(move || {
        let mut asked: HashSet<String> = HashSet::new();
        let mut last_fast: Option<Instant> = None;
        let mut last_process: Option<Instant> = None;
        let mut fullscreen: Option<bool> = None;
        let mut locked: Option<bool> = None;
        let mut foreground: Option<Option<String>> = None;
        let mut processes: Option<Vec<String>> = None;

        while !stop.load(Ordering::SeqCst) {
            let now = Instant::now();
            let Ok(current) = config.lock().map(|guard| guard.clone()) else {
                return;
            };

            if last_fast.is_none_or(|last| now.duration_since(last) >= FAST_INTERVAL) {
                last_fast = Some(now);
                if let Some(idle) = idle::milliseconds() {
                    emit_sample(&app, "idle", serde_json::json!(idle));
                }
                sample_busy(&app, &mut fullscreen, &mut locked);
                if current.foreground_enabled {
                    let next = foreground_state(&app, &current, &mut asked);
                    if foreground.as_ref() != Some(&next) {
                        emit_sample(
                            &app,
                            "foreground",
                            match &next {
                                Some(exe) => serde_json::json!(exe),
                                None => serde_json::Value::Null,
                            },
                        );
                        foreground = Some(next);
                    }
                } else {
                    foreground = None;
                }
            }

            if current.foreground_enabled
                && last_process.is_none_or(|last| now.duration_since(last) >= PROCESS_INTERVAL)
            {
                last_process = Some(now);
                let running = running_whitelisted(&current.whitelist());
                if processes.as_ref() != Some(&running) {
                    emit_sample(&app, "processes", serde_json::json!(running));
                    processes = Some(running);
                }
            }

            thread::sleep(POLL_INTERVAL);
        }
    })
}

fn emit_sample(app: &AppHandle, kind: &'static str, value: serde_json::Value) {
    let _ = app.emit(SAMPLE_EVENT, SamplePayload { kind, value });
}

fn foreground_state(
    app: &AppHandle,
    config: &PerceptionConfig,
    asked: &mut HashSet<String>,
) -> Option<String> {
    let exe = foreground::process()?;
    if ignored_process(&exe) {
        return None;
    }
    let whitelist = config.whitelist();
    if whitelist.contains(&exe) {
        return Some(exe);
    }
    if config.muted().contains(&exe) {
        return None;
    }
    if asked.insert(exe.clone()) {
        let _ = app.emit(CANDIDATE_EVENT, CandidatePayload { exe });
    }
    None
}

fn sample_busy(app: &AppHandle, fullscreen: &mut Option<bool>, locked: &mut Option<bool>) {
    let next_fullscreen = foreground::is_fullscreen();
    if *fullscreen != Some(next_fullscreen) {
        *fullscreen = Some(next_fullscreen);
        emit_sample(app, "fullscreen", serde_json::json!(next_fullscreen));
    }
    let next_locked = desktop::is_locked();
    if *locked != Some(next_locked) {
        *locked = Some(next_locked);
        emit_sample(app, "locked", serde_json::json!(next_locked));
    }
}

fn running_whitelisted(whitelist: &HashSet<String>) -> Vec<String> {
    let mut running: Vec<String> = snapshot::process_names()
        .into_iter()
        .filter(|name| whitelist.contains(name))
        .collect();
    running.sort();
    running.dedup();
    running
}

#[cfg(windows)]
mod win {
    use std::ffi::c_void;
    use std::mem::size_of;

    use windows_sys::Win32::Foundation::{CloseHandle, INVALID_HANDLE_VALUE, RECT};
    use windows_sys::Win32::Graphics::Dwm::{DWMWA_EXTENDED_FRAME_BOUNDS, DwmGetWindowAttribute};
    use windows_sys::Win32::Graphics::Gdi::{
        GetMonitorInfoW, MONITOR_DEFAULTTONEAREST, MONITORINFO, MonitorFromWindow,
    };
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, PROCESSENTRY32W, Process32FirstW, Process32NextW,
        TH32CS_SNAPPROCESS,
    };
    use windows_sys::Win32::System::StationsAndDesktops::{CloseDesktop, OpenInputDesktop};
    use windows_sys::Win32::System::SystemInformation::GetTickCount64;
    use windows_sys::Win32::System::Threading::{
        OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION, QueryFullProcessImageNameW,
    };
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{GetLastInputInfo, LASTINPUTINFO};
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GWL_STYLE, GetForegroundWindow, GetWindowLongW, GetWindowRect, GetWindowTextLengthW,
        GetWindowThreadProcessId, IsIconic, IsWindowVisible, IsZoomed, WS_CAPTION,
    };

    pub mod idle {
        use super::*;

        pub fn milliseconds() -> Option<u64> {
            let mut info = LASTINPUTINFO {
                cbSize: size_of::<LASTINPUTINFO>() as u32,
                dwTime: 0,
            };
            if unsafe { GetLastInputInfo(&mut info) } == 0 {
                return None;
            }
            let now = unsafe { GetTickCount64() } as u32;
            Some(now.wrapping_sub(info.dwTime) as u64)
        }
    }

    pub mod desktop {
        use super::*;

        const DESKTOP_READOBJECTS: u32 = 0x0001;

        pub fn is_locked() -> bool {
            let handle = unsafe { OpenInputDesktop(0, 0, DESKTOP_READOBJECTS) };
            if handle.is_null() {
                return true;
            }
            unsafe {
                CloseDesktop(handle);
            }
            false
        }
    }

    pub mod foreground {
        use super::*;

        pub fn process() -> Option<String> {
            let hwnd = unsafe { GetForegroundWindow() };
            if hwnd.is_null() {
                return None;
            }
            if unsafe { IsWindowVisible(hwnd) } == 0 || unsafe { IsIconic(hwnd) } != 0 {
                return None;
            }
            if unsafe { GetWindowTextLengthW(hwnd) } == 0 {
                return None;
            }
            let mut pid = 0u32;
            unsafe {
                GetWindowThreadProcessId(hwnd, &mut pid);
            }
            if pid == 0 {
                return None;
            }
            let path = process_name(pid)?;
            std::path::Path::new(&path)
                .file_stem()
                .map(|stem| stem.to_string_lossy().to_ascii_lowercase())
        }

        pub fn is_fullscreen() -> bool {
            let hwnd = unsafe { GetForegroundWindow() };
            if hwnd.is_null() || unsafe { IsZoomed(hwnd) } != 0 {
                return false;
            }
            let monitor = unsafe { MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST) };
            if monitor.is_null() {
                return false;
            }
            let mut info: MONITORINFO = unsafe { std::mem::zeroed() };
            info.cbSize = size_of::<MONITORINFO>() as u32;
            if unsafe { GetMonitorInfoW(monitor, &mut info) } == 0 {
                return false;
            }
            let monitor_rect = info.rcMonitor;

            let mut window_rect: RECT = unsafe { std::mem::zeroed() };
            if unsafe { GetWindowRect(hwnd, &mut window_rect) } == 0 {
                return false;
            }
            if rect_equals(&window_rect, &monitor_rect) {
                return true;
            }

            let style = unsafe { GetWindowLongW(hwnd, GWL_STYLE) } as u32;
            if style & WS_CAPTION != 0 {
                return false;
            }
            let mut frame: RECT = unsafe { std::mem::zeroed() };
            let result = unsafe {
                DwmGetWindowAttribute(
                    hwnd,
                    DWMWA_EXTENDED_FRAME_BOUNDS as u32,
                    &mut frame as *mut RECT as *mut c_void,
                    size_of::<RECT>() as u32,
                )
            };
            result == 0 && rect_covers(&frame, &monitor_rect)
        }

        fn process_name(pid: u32) -> Option<String> {
            let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
            if handle.is_null() {
                return None;
            }
            let mut buffer = [0u16; 1024];
            let mut length = buffer.len() as u32;
            let ok =
                unsafe { QueryFullProcessImageNameW(handle, 0, buffer.as_mut_ptr(), &mut length) };
            unsafe {
                CloseHandle(handle);
            }
            if ok == 0 || length == 0 {
                return None;
            }
            Some(String::from_utf16_lossy(&buffer[..length as usize]))
        }
    }

    pub mod snapshot {
        use super::*;

        pub fn process_names() -> Vec<String> {
            let handle = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) };
            if handle == INVALID_HANDLE_VALUE {
                return Vec::new();
            }
            let mut entry: PROCESSENTRY32W = unsafe { std::mem::zeroed() };
            entry.dwSize = size_of::<PROCESSENTRY32W>() as u32;
            let mut names = Vec::new();
            let mut current = unsafe { Process32FirstW(handle, &mut entry) } != 0;
            while current {
                if let Some(name) = wide_z(&entry.szExeFile) {
                    names.push(name);
                }
                current = unsafe { Process32NextW(handle, &mut entry) } != 0;
            }
            unsafe {
                CloseHandle(handle);
            }
            names
        }

        fn wide_z(buffer: &[u16]) -> Option<String> {
            let length = buffer.iter().position(|value| *value == 0)?;
            Some(String::from_utf16_lossy(&buffer[..length]))
        }
    }

    fn rect_equals(left: &RECT, right: &RECT) -> bool {
        (left.left - right.left).abs() <= 1
            && (left.top - right.top).abs() <= 1
            && (left.right - right.right).abs() <= 1
            && (left.bottom - right.bottom).abs() <= 1
    }

    fn rect_covers(outer: &RECT, inner: &RECT) -> bool {
        outer.left <= inner.left + 1
            && outer.top <= inner.top + 1
            && outer.right >= inner.right - 1
            && outer.bottom >= inner.bottom - 1
    }
}

#[cfg(windows)]
use win::{desktop, foreground, idle, snapshot};

#[cfg(not(windows))]
mod desktop {
    pub fn is_locked() -> bool {
        false
    }
}

#[cfg(not(windows))]
mod foreground {
    pub fn process() -> Option<String> {
        None
    }

    pub fn is_fullscreen() -> bool {
        false
    }
}

#[cfg(not(windows))]
mod idle {
    pub fn milliseconds() -> Option<u64> {
        None
    }
}

#[cfg(not(windows))]
mod snapshot {
    pub fn process_names() -> Vec<String> {
        Vec::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shell_and_self_processes_are_ignored() {
        assert!(ignored_process("explorer"));
        assert!(ignored_process("lockapp"));
        assert!(ignored_process("pet-shell"));
        assert!(ignored_process("python"));
        assert!(!ignored_process("chrome"));
    }

    #[test]
    fn exe_stem_strips_suffix_and_path_is_not_kept() {
        assert_eq!(exe_stem("Code.EXE").as_deref(), Some("code"));
        assert_eq!(exe_stem("  WeChat  ").as_deref(), Some("wechat"));
        assert_eq!(exe_stem(""), None);
    }

    #[test]
    fn normalized_names_lowercases_and_dedupes() {
        let names = normalized_names(&[
            "Code.EXE".to_string(),
            "code".to_string(),
            " WeChat ".to_string(),
        ]);
        assert_eq!(names.len(), 2);
        assert!(names.contains("code"));
        assert!(names.contains("wechat"));
    }
}
