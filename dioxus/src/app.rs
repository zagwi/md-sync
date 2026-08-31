use dioxus::prelude::*;

use crate::api;
use crate::state::{AppState, StatusKind};
use crate::APP_CSS;

use crate::components::header::Header;
use crate::components::file_table::FileTable;
use crate::components::log_console::LogConsole;
use crate::components::output_settings::OutputSettings;
use crate::components::plugin_card::PluginCard;
use crate::components::typography_card::TypographyCard;

#[allow(non_snake_case)]
pub fn App() -> Element {
    // AppState::new() 内部会调用 20 个 use_signal，必须在 hook 之外先执行：
    // use_context_provider 的初始化闭包运行在 use_hook 内部，在其中再调用 hook
    // 会触发 "hook list is already borrowed" 的 BorrowMutError 导致白屏。
    let state = AppState::new();
    let state = use_context_provider(|| state);

    // 首次加载 meta + state
    let mut booted = use_signal(|| false);
    use_effect(move || {
        if *booted.read() {
            return;
        }
        *booted.write() = true;
        let mut state = state;
        dioxus::prelude::spawn(async move {
            match api::fetch_meta().await {
                Ok(m) => {
                    // 默认插件：第一个 parser 类型插件；默认 schema 取当前插件 schema
                    let default_plugin = m
                        .plugins
                        .iter()
                        .find(|p| p.plugin_type == "parser")
                        .or_else(|| m.plugins.first())
                        .cloned();
                    if let Some(p) = default_plugin {
                        if state.plugin.read().is_empty() {
                            *state.plugin.write() = p.name.clone();
                        }
                        if state.schema.read().is_empty() {
                            *state.schema.write() = p.parser_schema.clone();
                        }
                    }
                    *state.meta.write() = Some(m);
                }
                Err(e) => {
                    *state.status.write() = StatusKind::Err(format!("无法连接后端服务: {e}"));
                    *state.loaded.write() = true;
                    return;
                }
            }
            match api::fetch_state().await {
                Ok(p) => state.fill_from_payload(&p),
                Err(e) => *state.status.write() = StatusKind::Err(e),
            }
            *state.loaded.write() = true;
        });
    });

    // 日志 + 状态轮询
    use_effect(move || {
        let mut state = state;
        dioxus::prelude::spawn(async move {
            let mut after: i64 = 0;
            let mut first = true;
            loop {
                match api::fetch_logs(after).await {
                    Ok(page) => {
                        if !page.lines.is_empty() {
                            state.logs.write().extend(page.lines.iter().cloned());
                            let total = state.logs.read().len();
                            if total > 500 {
                                state.logs.write().drain(0..total - 500);
                            }
                            scroll_log_bottom();
                        }
                        after = page.max_id;
                    }
                    Err(_) => {
                        if first {
                            // 后端可能尚未就绪（desktop 拉起进程场景），静默重试
                        }
                    }
                }
                if *state.loaded.read() {
                    match api::fetch_state().await {
                        Ok(p) => state.apply_runtime(&p),
                        Err(e) => {
                            if first {
                                *state.status.write() = StatusKind::Err(e);
                            }
                        }
                    }
                }
                first = false;
                crate::sleep(1200).await;
            }
        });
    });

    rsx! {
        style { "{APP_CSS}" }
        div { class: "min-h-screen bg-background text-foreground",
            Header {}
            main { class: "mx-auto w-full max-w-[1280px] px-6 pb-24",
                if !*state.loaded.read() {
                    div { class: "py-24 flex flex-col items-center gap-4",
                        div { class: "spinner" }
                        p { class: "text-sm text-muted-foreground", "正在加载 md-sync…" }
                    }
                } else {
                    div { class: "flex flex-col gap-6 pt-6",
                        StatusBanner {}
                        PluginCard {}
                        TypographyCard {}
                        OutputSettings {}
                        FileTable {}
                        LogConsole {}
                    }
                }
            }
            footer { class: "border-t border-border py-4",
                div { class: "mx-auto w-full max-w-[1280px] px-6 flex items-center justify-between text-xs text-muted-foreground",
                    span { "md-sync · Markdown 同步渲染器" }
                    span { "Dioxus 重构版 (web + desktop)" }
                }
            }
        }
    }
}

#[component]
fn StatusBanner() -> Element {
    let state = use_context::<AppState>();
    let status = state.status.read().clone();
    match status {
        StatusKind::None => rsx! {},
        StatusKind::Info(msg) => rsx! {
            div { class: "alert alert-info",
                svg { class: "w-4 h-4 shrink-0", width: "16", height: "16", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                    path { stroke_linecap: "round", stroke_linejoin: "round", d: "M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" }
                }
                span { "{msg}" }
            }
        },
        StatusKind::Ok(msg) => rsx! {
            div { class: "alert alert-success",
                svg { class: "w-4 h-4 shrink-0", width: "16", height: "16", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                    path { stroke_linecap: "round", stroke_linejoin: "round", d: "M5 13l4 4L19 7" }
                }
                span { "{msg}" }
            }
        },
        StatusKind::Err(msg) => rsx! {
            div { class: "alert alert-destructive",
                svg { class: "w-4 h-4 shrink-0", width: "16", height: "16", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                    path { stroke_linecap: "round", stroke_linejoin: "round", d: "M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" }
                }
                span { "{msg}" }
            }
        },
    }
}

fn scroll_log_bottom() {
    dioxus::prelude::spawn(async move {
        let _ = dioxus::document::eval(
            r#"var el = document.getElementById('log-scroll'); if (el) el.scrollTop = el.scrollHeight;"#,
        )
        .await;
    });
}


