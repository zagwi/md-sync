use serde::de::DeserializeOwned;

use crate::types::*;

// ── 传输层统一入口 ────────────────────────────────────────────────────────
// web（wasm32）通过浏览器 fetch 走 HTTP；
// desktop（非 wasm32）通过本机 Unix socket 与打包后端通信（不监听/访问任何网络端口）。

pub async fn call<T: DeserializeOwned>(method: &str, params: &serde_json::Value) -> Result<T, String> {
    #[cfg(target_arch = "wasm32")]
    {
        call_http(method, params).await
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        call_unix(method, params).await
    }
}

// ── web 分支：HTTP ────────────────────────────────────────────────────────

/// API 基地址：后端固定监听 127.0.0.1:8580（仅 web 版使用）。
#[cfg(target_arch = "wasm32")]
pub fn base_url() -> String {
    "http://127.0.0.1:8580".to_string()
}

#[cfg(target_arch = "wasm32")]
fn http_client() -> reqwest::Client {
    // wasm 走浏览器 fetch，ClientBuilder 不支持 timeout 配置
    reqwest::Client::new()
}

#[cfg(target_arch = "wasm32")]
async fn call_http<T: DeserializeOwned>(method: &str, params: &serde_json::Value) -> Result<T, String> {
    let url = format!("{}{}", base_url(), http_path(method, params));
    let client = http_client();
    let resp = if is_get(method) {
        client.get(&url).send().await
    } else {
        client.post(&url).json(params).send().await
    }
    .map_err(|e| format!("请求失败: {e}"))?;
    let status = resp.status();
    if !status.is_success() {
        return Err(format!("HTTP {}", status));
    }
    resp.json::<T>().await.map_err(|e| format!("解析失败: {e}"))
}

#[cfg(target_arch = "wasm32")]
fn is_get(method: &str) -> bool {
    matches!(method, "meta" | "styles" | "state" | "refresh" | "logs")
}

#[cfg(target_arch = "wasm32")]
fn http_path(method: &str, params: &serde_json::Value) -> String {
    let q = |k: &str| params.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string();
    match method {
        "meta" => "/api/meta".into(),
        "styles" => format!("/api/styles?schema={}", q("schema")),
        "state" => "/api/state".into(),
        "config" => "/api/config".into(),
        "sync" => "/api/sync".into(),
        "watch" => "/api/watch".into(),
        "clear" => "/api/clear".into(),
        "open-dir" => "/api/open-dir".into(),
        "normalize" => "/api/normalize".into(),
        "refresh" => "/api/refresh".into(),
        "logs" => format!(
            "/api/logs?after={}&json=1",
            params.get("after").and_then(|v| v.as_i64()).unwrap_or(0)
        ),
        _ => format!("/api/{method}"),
    }
}

// 仅 web 分支使用；desktop 改为直接设置本地路径，不再上传
#[cfg(target_arch = "wasm32")]
pub async fn upload_file(filename: &str, bytes: Vec<u8>) -> Result<StatePayload, String> {
    let enc = percent_encoding::utf8_percent_encode(
        filename,
        percent_encoding::NON_ALPHANUMERIC,
    )
    .to_string();
    let url = format!("{}/api/upload?filename={}", base_url(), enc);
    let resp = http_client()
        .post(&url)
        .body(bytes)
        .send()
        .await
        .map_err(|e| format!("上传失败: {e}"))?;
    let status = resp.status();
    if !status.is_success() {
        return Err(format!("HTTP {}", status));
    }
    resp.json::<StatePayload>().await.map_err(|e| format!("解析失败: {e}"))
}

/// 输出文件下载地址（web 版用 <a> 直接跳转）
#[cfg(target_arch = "wasm32")]
pub fn file_url(path: &str, download: bool) -> String {
    format!(
        "{}/api/file?download={}&path={}",
        base_url(),
        if download { 1 } else { 0 },
        percent_encoding::utf8_percent_encode(path, percent_encoding::NON_ALPHANUMERIC)
    )
}

// ── desktop 分支：Unix socket RPC ─────────────────────────────────────────

/// IPC socket 路径（与 md_sync/web/ipc.py 的 socket_path 保持同一算法）。
#[cfg(not(target_arch = "wasm32"))]
pub fn ipc_socket_path() -> std::path::PathBuf {
    let home = std::env::var_os("HOME")
        .map(std::path::PathBuf::from)
        .unwrap_or_default();
    #[cfg(target_os = "macos")]
    {
        home.join("Library/Caches/md-sync/md-sync.sock")
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        home.join(".cache/md-sync/md-sync.sock")
    }
    #[cfg(not(unix))]
    {
        std::path::PathBuf::new()
    }
}

#[cfg(all(not(target_arch = "wasm32"), unix))]
async fn call_unix<T: DeserializeOwned>(method: &str, params: &serde_json::Value) -> Result<T, String> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::UnixStream;

    let path = ipc_socket_path();
    let mut stream = UnixStream::connect(&path)
        .await
        .map_err(|e| format!("无法连接后端 ({}): {e}", path.display()))?;
    let req = serde_json::json!({ "method": method, "params": params });
    let mut buf = serde_json::to_string(&req).map_err(|e| e.to_string())?;
    buf.push('\n');
    stream
        .write_all(buf.as_bytes())
        .await
        .map_err(|e| format!("请求失败: {e}"))?;
    let mut resp = Vec::new();
    stream
        .read_to_end(&mut resp)
        .await
        .map_err(|e| format!("读取失败: {e}"))?;
    let v: serde_json::Value = serde_json::from_slice(&resp).map_err(|e| format!("解析失败: {e}"))?;
    if v.get("ok").and_then(|b| b.as_bool()).unwrap_or(false) {
        serde_json::from_value(v.get("data").cloned().unwrap_or(serde_json::Value::Null))
            .map_err(|e| format!("解析失败: {e}"))
    } else {
        Err(v
            .get("error")
            .and_then(|s| s.as_str())
            .unwrap_or("后端错误")
            .to_string())
    }
}

#[cfg(all(not(target_arch = "wasm32"), not(unix)))]
async fn call_unix<T: DeserializeOwned>(
    _method: &str,
    _params: &serde_json::Value,
) -> Result<T, String> {
    Err("桌面版暂不支持 Windows（Unix socket 不可用）".to_string())
}

// ── 业务 API ─────────────────────────────────────────────────────────────

pub async fn fetch_meta() -> Result<Meta, String> {
    call::<Meta>("meta", &serde_json::json!({})).await
}

pub async fn fetch_state() -> Result<StatePayload, String> {
    call::<StatePayload>("state", &serde_json::json!({})).await
}

pub async fn save_config(payload: &serde_json::Value) -> Result<StatePayload, String> {
    call::<StatePayload>("config", payload).await
}

pub async fn sync_now() -> Result<SimpleResp, String> {
    call::<SimpleResp>("sync", &serde_json::json!({})).await
}

pub async fn set_watch(enabled: bool) -> Result<SimpleResp, String> {
    call::<SimpleResp>("watch", &serde_json::json!({ "enabled": enabled })).await
}

pub async fn clear_outputs() -> Result<SimpleResp, String> {
    call::<SimpleResp>("clear", &serde_json::json!({})).await
}

pub async fn open_dir() -> Result<SimpleResp, String> {
    call::<SimpleResp>("open-dir", &serde_json::json!({})).await
}

pub async fn normalize() -> Result<SimpleResp, String> {
    call::<SimpleResp>("normalize", &serde_json::json!({})).await
}

pub async fn refresh() -> Result<serde_json::Value, String> {
    call::<serde_json::Value>("refresh", &serde_json::json!({})).await
}

pub async fn fetch_logs(after: i64) -> Result<LogPage, String> {
    call::<LogPage>("logs", &serde_json::json!({ "after": after })).await
}
