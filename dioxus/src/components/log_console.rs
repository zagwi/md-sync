use dioxus::prelude::*;

use crate::state::AppState;

#[component]
pub fn LogConsole() -> Element {
    let state = use_context::<AppState>();
    let mut expanded = use_signal(|| true);
    let logs = state.logs.read().clone();

    rsx! {
        section { class: "card",
            button {
                class: "card-header w-full text-left cursor-pointer",
                onclick: move |_| {
                    let cur = *expanded.read();
                    *expanded.write() = !cur;
                },
                div { class: "flex items-center gap-2",
                    svg {
                        class: if *expanded.read() { "size-4 text-muted-foreground transition-transform rotate-90" } else { "size-4 text-muted-foreground transition-transform" },
                        fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                        path { stroke_linecap: "round", stroke_linejoin: "round", d: "M9 5l7 7-7 7" }
                    }
                    svg { class: "size-4 text-muted-foreground", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                        path { stroke_linecap: "round", stroke_linejoin: "round",
                            d: "M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" } }
                    h2 { class: "text-base font-semibold", "同步日志" }
                    if !logs.is_empty() {
                        span { class: "badge badge-secondary", "{logs.len()}" }
                    }
                }
                p { class: "text-xs text-muted-foreground", "实时输出同步任务进度与错误信息。" }
            }
            if *expanded.read() {
                div { id: "log-scroll", class: "log-console",
                    if logs.is_empty() {
                        div { class: "log-line log-line-empty",
                            span { "等待同步任务…" }
                        }
                    } else {
                        for line in logs.iter() {
                            div {
                                class: match log_kind(&line.text) {
                                    "error" => "log-line log-line-error",
                                    "success" => "log-line log-line-success",
                                    "info" => "log-line log-line-info",
                                    _ => "log-line",
                                },
                                span { "{line.text}" }
                            }
                        }
                    }
                }
            }
        }
    }
}

fn log_kind(text: &str) -> &'static str {
    if text.contains('✗') || text.contains("失败") || text.contains("Error") || text.contains("错误") {
        "error"
    } else if text.contains('✓') || text.contains("完成") || text.contains("成功") {
        "success"
    } else if text.contains('▶') || text.contains("开始") {
        "info"
    } else {
        "plain"
    }
}
