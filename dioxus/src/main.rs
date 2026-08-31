#![cfg_attr(not(target_arch = "wasm32"), allow(unused_imports))]

mod api;
mod app;
mod components;
mod state;
mod types;

use dioxus::prelude::*;

use app::App;

/// CSS 直接内嵌进二进制，不走 dx 的 asset 运行时加载：
/// 纯 cargo build 的 desktop 版无法通过 dioxus:// 协议取到
/// asset! 打包的 CSS，会导致样式丢失 / asset 加载错误。
pub const APP_CSS: &str = include_str!("../assets/app.css");

/// 跨平台 sleep（web 用 gloo-timers，desktop 用 tokio）
pub async fn sleep(ms: u64) {
    #[cfg(target_arch = "wasm32")]
    {
        gloo_timers::future::TimeoutFuture::new(ms as u32).await;
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        tokio::time::sleep(std::time::Duration::from_millis(ms)).await;
    }
}

#[cfg(feature = "web")]
fn main() {
    dioxus_logger::initialize_default();
    dioxus::launch(App);
}

#[cfg(all(feature = "desktop", not(feature = "web")))]
fn main() {
    dioxus_logger::initialize_default();
    ensure_backend();
    dioxus::LaunchBuilder::new()
        .with_cfg(
            dioxus::desktop::Config::new().with_window(
                dioxus::desktop::WindowBuilder::new()
                    .with_title("md-sync")
                    .with_inner_size(dioxus::desktop::LogicalSize::new(1280.0, 860.0)),
            ),
        )
        .launch(App);
}

/// 内嵌的打包后端（PyInstaller onefile），由 build.rs 生成（desktop 专属）。
#[cfg(feature = "desktop")]
mod backend_embed {
    include!(concat!(env!("OUT_DIR"), "/backend_embed.rs"));
}

/// 桌面版：若 IPC socket 尚无后端，则自动拉起。
/// 查找链：旁边可执行文件 → 仓库 dist/ → 内嵌资源解压 → 回退 python。
/// 拉起后等待 socket 就绪，避免前端首请求撞上「连接被拒绝」的启动竞态。
#[cfg(feature = "desktop")]
fn ensure_backend() {
    use std::time::{Duration, Instant};

    let sock = api::ipc_socket_path();
    if socket_open(&sock) {
        return; // 后端已在运行
    }

    // 1) 优先拉起自包含后端（免 Python 环境）
    let mut started = false;
    for candidate in bundled_backend_candidates() {
        if spawn_backend(&candidate) {
            started = true;
            break;
        }
    }

    // 2) 开发回退：python -m md_sync.web.ipc（需本地已装 md_sync）
    if !started {
        let project_root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap_or_else(|| std::path::Path::new("."));
        if std::process::Command::new("python")
            .args(["-m", "md_sync.web.ipc"])
            .current_dir(project_root)
            .spawn()
            .is_ok()
        {
            started = true;
        }
    }

    // 3) 等待后端就绪（最多 20 秒，每 200ms 探测一次）
    if started {
        let deadline = Instant::now() + Duration::from_secs(20);
        while Instant::now() < deadline {
            if socket_open(&sock) {
                break;
            }
            std::thread::sleep(Duration::from_millis(200));
        }
    }
}

/// 探测 IPC socket 是否可连接（unix 平台；Windows 暂不支持桌面版）。
#[cfg(feature = "desktop")]
fn socket_open(path: &std::path::Path) -> bool {
    #[cfg(unix)]
    {
        std::os::unix::net::UnixStream::connect(path).is_ok()
    }
    #[cfg(not(unix))]
    {
        let _ = path;
        false
    }
}

/// 自包含后端的候选路径，按优先级排列。
#[cfg(feature = "desktop")]
fn bundled_backend_candidates() -> Vec<std::path::PathBuf> {
    use std::path::PathBuf;
    let mut v = Vec::new();

    // a) 当前可执行文件同目录（发布形态：app 与后端放一起）
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            v.push(dir.join(backend_bin_name()));
        }
    }

    // b) 仓库 dist/（开发形态）
    let project_root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap_or_else(|| std::path::Path::new("."));
    v.push(project_root.join("dist").join(backend_bin_name()));

    // c) 内嵌资源 → 解压到缓存目录（真·单文件分发）
    if let Some((bytes, fname)) = backend_embed::BACKEND_EMBED {
        if let Some(dir) = backend_cache_dir() {
            let p = dir.join(fname);
            let need_write = !p.is_file()
                || std::fs::metadata(&p)
                    .map(|m| m.len() as usize != bytes.len())
                    .unwrap_or(true);
            if need_write {
                if std::fs::create_dir_all(&dir).is_ok() && std::fs::write(&p, bytes).is_ok() {
                    #[cfg(unix)]
                    {
                        use std::os::unix::fs::PermissionsExt;
                        let _ =
                            std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755));
                    }
                }
            }
            if p.is_file() {
                v.push(p);
            }
        }
    }

    v
}

#[cfg(feature = "desktop")]
fn backend_bin_name() -> &'static str {
    if cfg!(windows) { "md-sync.exe" } else { "md-sync" }
}

#[cfg(feature = "desktop")]
fn backend_cache_dir() -> Option<std::path::PathBuf> {
    use std::path::PathBuf;
    #[cfg(windows)]
    {
        std::env::var_os("LOCALAPPDATA").map(|p| PathBuf::from(p).join("md-sync"))
    }
    #[cfg(target_os = "macos")]
    {
        std::env::var_os("HOME").map(|p| PathBuf::from(p).join("Library/Caches/md-sync"))
    }
    #[cfg(not(any(windows, target_os = "macos")))]
    {
        let base = std::env::var_os("XDG_CACHE_HOME")
            .map(PathBuf::from)
            .or_else(|| std::env::var_os("HOME").map(|p| PathBuf::from(p).join(".cache")));
        base.map(|p| p.join("md-sync"))
    }
}

#[cfg(feature = "desktop")]
fn spawn_backend(path: &std::path::Path) -> bool {
    // 打包后端的 CLI 入口：md-sync ipc —— 起 Unix socket server（无网络端口）
    std::process::Command::new(path).arg("ipc").spawn().is_ok()
}
