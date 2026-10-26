use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use rand::RngCore;
use rand::rngs::OsRng;
use serde::Serialize;

pub const PORT_PREFIX: &str = "PET_PORT=";
pub const SHUTDOWN_COMMAND: &[u8] = b"shutdown\n";
pub const STARTUP_TIMEOUT: Duration = Duration::from_secs(15);
pub const STOP_TIMEOUT: Duration = Duration::from_secs(8);
const STDERR_TAIL_LINES: usize = 20;
const POLL_INTERVAL: Duration = Duration::from_millis(50);

#[derive(Clone, Serialize)]
pub struct BackendInfo {
    pub port: u16,
    pub token: String,
}

pub struct LaunchSpec {
    pub command: PathBuf,
    pub args: Vec<String>,
    pub cwd: PathBuf,
    pub env: Vec<(String, String)>,
}

pub struct Launched {
    pub info: BackendInfo,
    child: Child,
    stdin: ChildStdin,
}

pub struct ExitInfo {
    pub code: Option<i32>,
}

pub fn create_token() -> String {
    let mut bytes = [0u8; 32];
    OsRng.fill_bytes(&mut bytes);
    URL_SAFE_NO_PAD.encode(bytes)
}

pub fn parse_port_line(line: &str) -> Option<u16> {
    let value: u16 = line.strip_prefix(PORT_PREFIX)?.trim().parse().ok()?;
    (value > 0).then_some(value)
}

pub fn launch(spec: &LaunchSpec, timeout: Duration) -> Result<Launched, String> {
    let token = create_token();
    let mut command = Command::new(&spec.command);
    command
        .args(&spec.args)
        .current_dir(&spec.cwd)
        .envs(spec.env.iter().map(|(key, value)| (key, value)))
        .env("PET_PORT", "0")
        .env("PET_TOKEN", &token)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    let mut child = command
        .spawn()
        .map_err(|error| format!("无法启动后端：{error}"))?;
    let (Some(stdin), Some(stdout), Some(stderr)) =
        (child.stdin.take(), child.stdout.take(), child.stderr.take())
    else {
        let _ = child.kill();
        return Err("无法获取后端的标准输入输出".to_string());
    };

    let (port_tx, port_rx) = mpsc::channel::<u16>();
    thread::spawn(move || {
        let mut announced = false;
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if !announced
                && let Some(port) = parse_port_line(&line)
            {
                announced = true;
                let _ = port_tx.send(port);
                continue;
            }
            eprintln!("[backend] {line}");
        }
    });

    let stderr_tail: Arc<Mutex<VecDeque<String>>> = Arc::new(Mutex::new(VecDeque::new()));
    let tail = Arc::clone(&stderr_tail);
    let stderr_thread = thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            eprintln!("[backend:err] {line}");
            if let Ok(mut lines) = tail.lock() {
                lines.push_back(line);
                if lines.len() > STDERR_TAIL_LINES {
                    lines.pop_front();
                }
            }
        }
    });

    match port_rx.recv_timeout(timeout) {
        Ok(port) => Ok(Launched {
            info: BackendInfo { port, token },
            child,
            stdin,
        }),
        Err(RecvTimeoutError::Timeout) => {
            let _ = child.kill();
            let _ = child.wait();
            Err(format!("后端启动超时（{} 秒）", timeout.as_secs()))
        }
        Err(RecvTimeoutError::Disconnected) => {
            let status = child.wait().ok();
            let _ = stderr_thread.join();
            let tail = stderr_tail
                .lock()
                .map(|lines| lines.iter().cloned().collect::<Vec<_>>().join("\n"))
                .unwrap_or_default();
            Err(format!(
                "后端在启动前退出（{}）\n{tail}",
                describe_status(status)
            ))
        }
    }
}

fn describe_status(status: Option<ExitStatus>) -> String {
    match status.and_then(|status| status.code()) {
        Some(code) => format!("代码 {code}"),
        None => "未知退出状态".to_string(),
    }
}

struct Shared {
    child: Mutex<Child>,
    stdin: Mutex<Option<ChildStdin>>,
    stopping: AtomicBool,
}

pub struct Supervisor {
    shared: Arc<Shared>,
}

impl Supervisor {
    pub fn new(
        launched: Launched,
        on_unexpected_exit: impl FnOnce(ExitInfo) + Send + 'static,
    ) -> Self {
        let shared = Arc::new(Shared {
            child: Mutex::new(launched.child),
            stdin: Mutex::new(Some(launched.stdin)),
            stopping: AtomicBool::new(false),
        });
        let watcher = Arc::clone(&shared);
        thread::spawn(move || {
            loop {
                thread::sleep(POLL_INTERVAL);
                let status = match watcher.child.lock() {
                    Ok(mut child) => child.try_wait(),
                    Err(_) => return,
                };
                match status {
                    Ok(Some(status)) => {
                        if !watcher.stopping.load(Ordering::SeqCst) {
                            on_unexpected_exit(ExitInfo {
                                code: status.code(),
                            });
                        }
                        return;
                    }
                    Ok(None) => continue,
                    Err(_) => return,
                }
            }
        });
        Supervisor { shared }
    }

    pub fn stop(&self, timeout: Duration) -> Option<ExitInfo> {
        self.shared.stopping.store(true, Ordering::SeqCst);
        if let Ok(mut slot) = self.shared.stdin.lock()
            && let Some(mut pipe) = slot.take()
        {
            let _ = pipe.write_all(SHUTDOWN_COMMAND);
            let _ = pipe.flush();
        }
        let deadline = Instant::now() + timeout;
        loop {
            if let Some(status) = self.try_exit() {
                return Some(ExitInfo {
                    code: status.code(),
                });
            }
            if Instant::now() >= deadline {
                let mut child = self.shared.child.lock().ok()?;
                let _ = child.kill();
                return child.wait().ok().map(|status| ExitInfo {
                    code: status.code(),
                });
            }
            thread::sleep(POLL_INTERVAL);
        }
    }

    fn try_exit(&self) -> Option<ExitStatus> {
        self.shared.child.lock().ok()?.try_wait().ok().flatten()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::net::TcpStream;
    use std::path::Path;

    fn real_python(backend_dir: &Path) -> Option<PathBuf> {
        let venv = backend_dir.join(".venv");
        let python = if cfg!(windows) {
            venv.join("Scripts").join("python.exe")
        } else {
            venv.join("bin").join("python")
        };
        python.exists().then_some(python)
    }

    fn backend_dir() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..")
            .join("backend")
    }

    fn http_status(port: u16, token: Option<&str>) -> u16 {
        let mut stream = TcpStream::connect(("127.0.0.1", port)).expect("connect backend");
        let header = token
            .map(|value| format!("X-Pet-Token: {value}\r\n"))
            .unwrap_or_default();
        stream
            .write_all(
                format!("GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\n{header}Connection: close\r\n\r\n")
                    .as_bytes(),
            )
            .expect("write request");
        let mut response = String::new();
        std::io::Read::read_to_string(&mut stream, &mut response).expect("read response");
        response
            .split_whitespace()
            .nth(1)
            .and_then(|code| code.parse().ok())
            .expect("status code")
    }

    #[test]
    fn token_is_url_safe_and_unique() {
        let first = create_token();
        let second = create_token();
        assert_eq!(first.len(), 43);
        assert!(first.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_'));
        assert_ne!(first, second);
    }

    #[test]
    fn port_line_must_be_a_positive_port() {
        assert_eq!(parse_port_line("PET_PORT=4321"), Some(4321));
        assert_eq!(parse_port_line("PET_PORT= 8080 "), Some(8080));
        assert_eq!(parse_port_line("PET_PORT=0"), None);
        assert_eq!(parse_port_line("PET_PORT=70000"), None);
        assert_eq!(parse_port_line("hello PET_PORT=1"), None);
        assert_eq!(parse_port_line("PET_PORT=abc"), None);
    }

    #[test]
    fn real_backend_starts_serves_with_token_and_shuts_down_gracefully() {
        let dir = backend_dir();
        let Some(python) = real_python(&dir) else {
            eprintln!("跳过：未找到 backend/.venv");
            return;
        };
        let data_dir = std::env::temp_dir().join(format!("pet-shell-test-{}", create_token()));
        fs::create_dir_all(&data_dir).expect("create data dir");
        let spec = LaunchSpec {
            command: python,
            args: vec!["main.py".to_string()],
            cwd: dir,
            env: vec![
                (
                    "PET_DATA_DIR".to_string(),
                    data_dir.to_string_lossy().into_owned(),
                ),
                ("PET_LOG_MAX_WAIT_SECONDS".to_string(), "60".to_string()),
            ],
        };
        let launched = launch(&spec, Duration::from_secs(30)).expect("launch backend");
        let token = launched.info.token.clone();
        let port = launched.info.port;

        assert_eq!(http_status(port, None), 401);
        assert_eq!(http_status(port, Some(&token)), 200);

        let supervisor = Supervisor::new(launched, |_| {});
        let exit = supervisor.stop(Duration::from_secs(15)).expect("exit status");
        assert_eq!(exit.code, Some(0));

        let log = fs::read_to_string(data_dir.join("logs").join("backend.log"))
            .expect("backend log written");
        assert!(log.contains("后端已退出"));
        assert!(!log.contains(&token), "令牌不得出现在日志中");
        let _ = fs::remove_dir_all(&data_dir);
    }
}
