use dioxus::prelude::*;
use serde_json::json;

use crate::api;
use crate::state::{save_and_apply, toggle_in_vec, AppState, StatusKind};
use crate::types::FORMATS;

#[component]
pub fn OutputSettings() -> Element {
    let mut state = use_context::<AppState>();
    let meta = state.meta.read().clone();
    let formats = state.formats.read().clone();
    let langs = meta
        .as_ref()
        .map(|m| m.langs.clone())
        .unwrap_or_else(|| vec!["zh".to_string(), "en".to_string()]);
    let naming = state.naming.read().clone();
    let blink = state.blink.read().clone();
    let syncing = state.syncing.read().clone();
    let watching = state.watching.read().clone();
    let busy = state.busy.read().clone();

    let lang_label = |l: &str| -> String {
        match l {
            "zh" => "中文".to_string(),
            "en" => "英文".to_string(),
            other => other.to_string(),
        }
    };

    // 预计算格式×语言矩阵，避免在 rsx 内声明 let
    let format_grid: Vec<(&crate::types::FormatMeta, Vec<(String, String)>)> = FORMATS
        .iter()
        .map(|f| {
            let lang_keys: Vec<(String, String)> = langs
                .iter()
                .map(|l| (l.clone(), format!("{}_{}", f.key, l)))
                .collect();
            (f, lang_keys)
        })
        .collect();

    rsx! {
        section { class: "card",
            div { class: "card-header",
                div { class: "flex items-center gap-2 flex-wrap",
                    svg { class: "size-4 text-muted-foreground", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                        path { stroke_linecap: "round", stroke_linejoin: "round",
                            d: "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" }
                        path { stroke_linecap: "round", stroke_linejoin: "round", d: "M15 12a3 3 0 11-6 0 3 3 0 016 0z" }
                    }
                    h2 { class: "text-base font-semibold", "输出设置" }
                    p { class: "text-xs text-muted-foreground ml-auto text-right",
                        "配置源文档、输出目录与输出格式。" }
                }
            }

            div { class: "card-body flex flex-col gap-4",
                // 源文件 / 输出目录：md 以上两列并排，窄屏堆叠
                div { class: "grid grid-cols-1 md:grid-cols-2 gap-4",
                    // 源文件
                    div { class: "flex flex-col sm:flex-row gap-2 items-end",
                        div { class: "flex-1",
                            label { class: "field-label", "源文件（Markdown）" }
                            input {
                                class: "shadcn-input",
                                placeholder: "选择或输入 .md 源文件路径",
                                value: "{state.source.read()}",
                                onchange: move |evt| {
                                    let v = evt.value();
                                    if v.is_empty() { return; }
                                    *state.source.write() = v.clone();
                                    dioxus::prelude::spawn(async move {
                                        save_and_apply(state, json!({ "source": v })).await;
                                    });
                                },
                            }
                        }
                        UploadButton { state: state }
                    }

                    // 输出目录
                    div { class: "flex flex-col sm:flex-row gap-2 items-end",
                        div { class: "flex-1",
                            label { class: "field-label", "输出目录" }
                            input {
                                class: "shadcn-input",
                                placeholder: "选择或输入输出目录路径",
                                value: "{state.output_dir.read()}",
                                onchange: move |evt| {
                                    let v = evt.value();
                                    if v.is_empty() { return; }
                                    *state.output_dir.write() = v.clone();
                                    dioxus::prelude::spawn(async move {
                                        save_and_apply(state, json!({ "output_dir": v })).await;
                                    });
                                },
                            }
                        }
                        BrowseDirButton { state: state }
                    }
                }

                // 输出格式矩阵
                div {
                    label { class: "field-label", "输出格式" }
                    div { class: "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3",
                        for (fmt, lang_keys) in format_grid {
                            div { class: "format-card",
                                div { class: "flex items-center gap-2 mb-2.5",
                                    span { class: "format-icon {fmt.color}",
                                        svg { class: "size-4", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                                            path { stroke_linecap: "round", stroke_linejoin: "round", d: "{fmt.icon}" }
                                        }
                                    }
                                    span { class: "text-sm font-medium", "{fmt.label}" }
                                }
                                div { class: "flex gap-4",
                                    for (lang, key) in lang_keys {
                                        label { class: "checkbox-row",
                                            input {
                                                r#type: "checkbox",
                                                checked: formats.contains(&key),
                                                onchange: {
                                                    let key = key.clone();
                                                    move |_| {
                                                        toggle_in_vec(state.formats, &key);
                                                        let payload = state.config_payload();
                                                        dioxus::prelude::spawn(async move {
                                                            match api::save_config(&payload).await {
                                                                Ok(p) => state.apply_runtime(&p),
                                                                Err(e) => *state.status.write() = StatusKind::Err(e),
                                                            }
                                                        });
                                                    }
                                                },
                                            }
                                            span { "{lang_label(lang.as_str())}" }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // 命名策略 + 状态闪烁
                div { class: "flex flex-col sm:flex-row sm:items-center gap-3 rounded-lg border border-border bg-muted/40 px-4 py-3",
                    div {
                        p { class: "text-sm font-medium", "输出重名处理" }
                        p { class: "text-xs text-muted-foreground", "多版本输出文件名冲突时的策略" }
                    }
                    div { class: "flex items-center gap-4 sm:ml-auto",
                        div { class: "segmented",
                            button {
                                class: if naming == "ts" { "segmented-item segmented-active" } else { "segmented-item" },
                                onclick: move |_| {
                                    *state.naming.write() = "ts".into();
                                    dioxus::prelude::spawn(async move {
                                        save_and_apply(state, json!({ "naming": "ts" })).await;
                                    });
                                },
                                "时间戳"
                            }
                            button {
                                class: if naming == "overwrite" { "segmented-item segmented-active" } else { "segmented-item" },
                                onclick: move |_| {
                                    *state.naming.write() = "overwrite".into();
                                    dioxus::prelude::spawn(async move {
                                        save_and_apply(state, json!({ "naming": "overwrite" })).await;
                                    });
                                },
                                "覆盖"
                            }
                        }
                        div { class: "flex items-center gap-2",
                            span { class: "text-xs text-muted-foreground whitespace-nowrap", "状态闪烁" }
                            button {
                                class: if blink { "switch switch-on" } else { "switch" },
                                role: "switch",
                                aria_checked: blink.to_string(),
                                onclick: move |_| {
                                    let next = !*state.blink.read();
                                    *state.blink.write() = next;
                                    dioxus::prelude::spawn(async move {
                                        save_and_apply(state, json!({ "blink": next })).await;
                                    });
                                },
                                span { class: "switch-thumb" }
                            }
                        }
                    }
                }

                // CTA：主操作在前，监听次之，导航/危险操作右侧分组
                div { class: "flex flex-wrap items-center gap-2.5 mt-5",
                    button {
                        class: "btn btn-primary",
                        disabled: syncing || busy,
                        onclick: move |_| {
                            let mut state = state;
                            *state.busy.write() = true;
                            dioxus::prelude::spawn(async move {
                                match api::sync_now().await {
                                    Ok(r) => {
                                        if r.ok {
                                            *state.syncing.write() = true;
                                            *state.status.write() = StatusKind::Ok("同步任务已启动".into());
                                        } else {
                                            let msg = r.errors.join("；");
                                            *state.status.write() = StatusKind::Err(if msg.is_empty() { "同步失败".into() } else { msg });
                                        }
                                    }
                                    Err(e) => *state.status.write() = StatusKind::Err(e),
                                }
                                *state.busy.write() = false;
                            });
                        },
                        svg { class: "size-4", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                            path { stroke_linecap: "round", stroke_linejoin: "round",
                                d: "M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" } }
                        if syncing { "同步中…" } else { "同步输出" }
                    }
                    button {
                        class: "btn btn-outline",
                        disabled: syncing,
                        onclick: move |_| {
                            let next = !*state.watching.read();
                            dioxus::prelude::spawn(async move {
                                match api::set_watch(next).await {
                                    Ok(r) => {
                                        *state.watching.write() = r.watching;
                                        *state.status.write() = StatusKind::Ok(
                                            if r.watching { "已开启持续监听".into() } else { "已停止监听".into() }
                                        );
                                    }
                                    Err(e) => *state.status.write() = StatusKind::Err(e),
                                }
                            });
                        },
                        svg { class: "size-4", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                            path { stroke_linecap: "round", stroke_linejoin: "round",
                                d: if watching { "M21 12a9 9 0 11-18 0 9 9 0 0118 0zM9 9.5v5a1 1 0 001.5.87l4.5-2.5a1 1 0 000-1.74L10.5 8.63A1 1 0 009 9.5z" } else { "M15 12a3 3 0 11-6 0 3 3 0 016 0zM2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" } }
                        }
                        if watching { "停止监听" } else { "持续监听" }
                    }
                    button {
                        class: "btn btn-outline ml-auto",
                        onclick: move |_| {
                            let mut state = state;
                            dioxus::prelude::spawn(async move {
                                match api::open_dir().await {
                                    Ok(r) => {
                                        if !r.path.is_empty() {
                                            *state.status.write() = StatusKind::Ok(format!("已打开目录: {}", r.path));
                                        }
                                    }
                                    Err(e) => *state.status.write() = StatusKind::Err(e),
                                }
                            });
                        },
                        svg { class: "size-4", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                            path { stroke_linecap: "round", stroke_linejoin: "round",
                                d: "M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" } }
                        "打开输出目录"
                    }
                    button {
                        class: "btn btn-outline-destructive",
                        onclick: move |_| {
                            let mut state = state;
                            dioxus::prelude::spawn(async move {
                                match api::clear_outputs().await {
                                    Ok(r) => {
                                        *state.status.write() = StatusKind::Ok(format!("已清除 {} 个输出", r.removed));
                                        if let Ok(p) = api::fetch_state().await { state.apply_runtime(&p); }
                                    }
                                    Err(e) => *state.status.write() = StatusKind::Err(e),
                                }
                            });
                        },
                        svg { class: "size-4", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                            path { stroke_linecap: "round", stroke_linejoin: "round",
                                d: "M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" } }
                        "清除输出"
                    }
                }
            }
        }
    }
}

/// 源文件上传按钮（web 用隐藏 file input + eval 触发；desktop 用 rfd 对话框）
#[component]
fn UploadButton(state: AppState) -> Element {
    #[cfg(target_arch = "wasm32")]
    {
        rsx! {
            input {
                id: "source-file-input",
                r#type: "file",
                hidden: true,
                accept: ".md,.markdown,.txt,.text,text/markdown,text/plain",
                onchange: move |evt| {
                    // wasm 下 files() 直接返回 Vec<FileData>（非 wasm 为 Option）
                    let files = evt.files();
                    if !files.is_empty() {
                        let mut state = state;
                        dioxus::prelude::spawn(async move {
                            for f in files.iter().take(1) {
                                let name = f.name();
                                match f.read_bytes().await {
                                    Ok(bytes) => {
                                        match api::upload_file(&name, bytes.to_vec()).await {
                                            Ok(p) => {
                                                state.fill_from_payload(&p);
                                                *state.status.write() = StatusKind::Ok(format!("已上传 {name}"));
                                            }
                                            Err(e) => *state.status.write() = StatusKind::Err(e),
                                        }
                                    }
                                    Err(e) => *state.status.write() = StatusKind::Err(format!("读取文件失败: {e}")),
                                }
                            }
                        });
                    }
                }
            }
            button {
                class: "btn btn-outline",
                onclick: move |_| {
                    let _ = dioxus::document::eval("document.getElementById('source-file-input').click()");
                },
                svg { class: "size-4", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                    path { stroke_linecap: "round", stroke_linejoin: "round",
                        d: "M12 16V4m0 0L8 8m4-4l4 4m-9 8H5a2 2 0 00-2 2v2a2 2 0 002 2h14a2 2 0 002-2v-2a2 2 0 00-2-2h-2" } }
                "上传"
            }
        }
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        rsx! {
            button {
                class: "btn btn-outline",
                onclick: move |_| {
                    let mut state = state;
                    dioxus::prelude::spawn(async move {
                        // desktop 与后端同机，选完文件直接设置本地路径即可，
                        // 无需像 web 那样把文件内容上传一份到后端。
                        let picked = tokio::task::spawn_blocking(|| {
                            rfd::FileDialog::new()
                                .add_filter("Markdown", &["md", "markdown", "txt", "text"])
                                .pick_file()
                        })
                        .await
                        .ok()
                        .flatten();
                        if let Some(path) = picked {
                            let path_str = path.to_string_lossy().to_string();
                            let name = path.file_name().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
                            // 先回显到前端（save_and_apply 内部只 apply_runtime，不会更新 source）
                            *state.source.write() = path_str.clone();
                            save_and_apply(state, json!({ "source": path_str })).await;
                            *state.status.write() = StatusKind::Ok(format!("已选择源文件 {name}"));
                        }
                    });
                },
                svg { class: "size-4", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                    path { stroke_linecap: "round", stroke_linejoin: "round",
                        d: "M12 16V4m0 0L8 8m4-4l4 4m-9 8H5a2 2 0 00-2 2v2a2 2 0 002 2h14a2 2 0 002-2v-2a2 2 0 00-2-2h-2" } }
                "选择文件"
            }
        }
    }
}

/// 输出目录选择按钮（web 用 prompt；desktop 用 rfd 目录对话框）
#[component]
fn BrowseDirButton(state: AppState) -> Element {
    #[cfg(target_arch = "wasm32")]
    {
        rsx! {
            button {
                class: "btn btn-outline",
                onclick: move |_| {
                    let mut state = state;
                    dioxus::prelude::spawn(async move {
                        if let Ok(v) = dioxus::document::eval("prompt('输入输出目录绝对路径：') || ''").await {
                            if let Some(dir) = v.as_str() {
                                let dir = dir.to_string();
                                if !dir.is_empty() {
                                    *state.output_dir.write() = dir.clone();
                                    let _ = crate::state::save_and_apply(state, json!({ "output_dir": dir })).await;
                                }
                            }
                        }
                    });
                },
                svg { class: "size-4", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                    path { stroke_linecap: "round", stroke_linejoin: "round",
                        d: "M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" } }
                "选择目录"
            }
        }
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        rsx! {
            button {
                class: "btn btn-outline",
                onclick: move |_| {
                    let mut state = state;
                    dioxus::prelude::spawn(async move {
                        let picked = tokio::task::spawn_blocking(|| rfd::FileDialog::new().pick_folder())
                            .await
                            .ok()
                            .flatten();
                        if let Some(dir) = picked {
                            let dir = dir.to_string_lossy().to_string();
                            *state.output_dir.write() = dir.clone();
                            let _ = crate::state::save_and_apply(state, json!({ "output_dir": dir })).await;
                        }
                    });
                },
                svg { class: "size-4", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                    path { stroke_linecap: "round", stroke_linejoin: "round",
                        d: "M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" } }
                "选择目录"
            }
        }
    }
}
