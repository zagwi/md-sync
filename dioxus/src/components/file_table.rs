use dioxus::prelude::*;

// wasm 分支用 <a> 下载需要 api::file_url；desktop 分支直接本地打开，不引用 api
#[cfg(target_arch = "wasm32")]
use crate::api;
use crate::state::AppState;
// wasm 分支走 <a> 直接下载，无失败回调，不需要 StatusKind
#[cfg(not(target_arch = "wasm32"))]
use crate::state::StatusKind;
use crate::types::OutputFile;

#[component]
pub fn FileTable() -> Element {
    let state = use_context::<AppState>();
    let files = state.output_files.read().clone();
    let blink = state.blink.read().clone();

    rsx! {
        section { class: "card",
            div { class: "card-header",
                div { class: "flex items-center gap-2 flex-wrap",
                    svg { class: "size-4 text-muted-foreground", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                        path { stroke_linecap: "round", stroke_linejoin: "round",
                            d: "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" } }
                    h2 { class: "text-base font-semibold", "输出文件" }
                    span { class: "badge badge-secondary", "{files.len()}" }
                    p { class: "text-xs text-muted-foreground ml-auto text-right",
                        "已生成的同步输出，点击直接打开查看。" }
                }
            }
            div { class: "card-body",
                if files.is_empty() {
                    div { class: "empty-state",
                        svg { class: "size-10 text-muted-foreground/40", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "1.5",
                            path { stroke_linecap: "round", stroke_linejoin: "round",
                                d: "M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" } }
                        p { class: "text-sm text-muted-foreground mt-3", "暂无输出文件，先同步一次看看" }
                    }
                } else {
                    div { class: "table-wrap",
                        table { class: "shadcn-table",
                            thead {
                                tr {
                                    th { "状态" }
                                    th { "格式" }
                                    th { "语言" }
                                    th { "文件" }
                                    th { "最后更新" }
                                    th { class: "text-right", "操作" }
                                }
                            }
                            tbody {
                                for f in files.iter() {
                                    FileRow { file: f.clone(), blink: blink }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

#[component]
fn FileRow(file: OutputFile, blink: bool) -> Element {
    let (badge_class, badge_text, pulse) = match file.status.as_str() {
        "synced" => (
            "badge badge-success",
            "已同步",
            blink,
        ),
        "stale" => ("badge badge-warning", "已过期", false),
        _ => ("badge badge-destructive", "缺失", false),
    };

    let lang_label = match file.lang.as_str() {
        "zh" => "中文",
        "en" => "英文",
        other => other,
    };

    rsx! {
        tr {
            td {
                span { class: "{badge_class}",
                    if pulse { span { class: "pulse-dot" } }
                    "{badge_text}"
                }
            }
            td {
                span { class: "badge badge-outline uppercase", "{file.format}" }
            }
            td { span { class: "badge badge-outline", "{lang_label}" } }
            td {
                div { class: "min-w-0",
                    p { class: "text-[13px] font-medium truncate max-w-[280px]", "{file.filename}" }
                    if file.is_source {
                        span { class: "text-[10px] text-muted-foreground", "源文档" }
                    }
                }
            }
            td { span { class: "text-xs text-muted-foreground whitespace-nowrap",
                    if file.mtime_fmt.is_empty() { "—" } else { "{file.mtime_fmt}" } } }
            td {
                div { class: "flex items-center justify-end gap-1",
                    DownloadButton { file: file.clone() }
                }
            }
        }
    }
}

#[component]
fn DownloadButton(file: OutputFile) -> Element {
    // state / exists 仅在 desktop 分支（下载失败回调、禁用态）使用
    #[cfg(not(target_arch = "wasm32"))]
    let state = use_context::<AppState>();
    let path = file.path.clone();
    let filename = file.filename.clone();
    #[cfg(not(target_arch = "wasm32"))]
    let exists = file.exists;

    #[cfg(target_arch = "wasm32")]
    {
        rsx! {
            a {
                class: "btn btn-ghost btn-icon",
                href: api::file_url(&path, true),
                download: "true",
                title: "下载 {filename}",
                aria_label: "下载 {filename}",
                svg { class: "size-4", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                    path { stroke_linecap: "round", stroke_linejoin: "round",
                        d: "M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" } }
            }
        }
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        rsx! {
            button {
                class: "btn btn-ghost btn-icon",
                disabled: !exists,
                title: "打开 {filename}",
                onclick: move |_| {
                    let mut state = state;
                    let path = path.clone();
                    dioxus::prelude::spawn(async move {
                        // desktop 下输出就在本机磁盘，直接用系统默认程序打开
                        match tokio::task::spawn_blocking(move || open_with_default_app(&path)).await {
                            Ok(Ok(())) => {}
                            Ok(Err(e)) => *state.status.write() = StatusKind::Err(format!("打开失败: {e}")),
                            Err(_) => *state.status.write() = StatusKind::Err("打开失败: 后台任务异常".to_string()),
                        }
                    });
                },
                svg { class: "size-4", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                    path { stroke_linecap: "round", stroke_linejoin: "round",
                        d: "M7 17L17 7m0 0H8m9 0v9" } }
            }
        }
    }
}

/// 用系统默认程序打开本地文件（desktop 专用）。
/// Linux → xdg-open；macOS → open；Windows → start。
#[cfg(not(target_arch = "wasm32"))]
fn open_with_default_app(path: &str) -> Result<(), String> {
    use std::process::Command;
    let mut cmd = Command::new(match std::env::consts::OS {
        "macos" => "open",
        "windows" => "cmd",
        _ => "xdg-open",
    });
    #[cfg(target_os = "windows")]
    cmd.args(["/c", "start", "", path]);
    #[cfg(not(target_os = "windows"))]
    cmd.arg(path);
    cmd.spawn().map(|_| ()).map_err(|e| e.to_string())
}
