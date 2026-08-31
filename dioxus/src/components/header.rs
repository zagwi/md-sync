use dioxus::prelude::*;

use crate::api;
use crate::state::{AppState, StatusKind};

#[component]
pub fn Header() -> Element {
    let state = use_context::<AppState>();
    let watching = state.watching.read().clone();
    let syncing = state.syncing.read().clone();
    let source = state.source.read().clone();

    rsx! {
        header { class: "sticky top-0 z-40 w-full border-b border-border/60 bg-background/80 backdrop-blur",
            div { class: "mx-auto w-full max-w-[1280px] px-6 h-16 flex items-center justify-between",
                div { class: "flex items-center gap-3",
                    div { class: "flex size-9 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-sm",
                        svg { class: "size-5", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                            path { stroke_linecap: "round", stroke_linejoin: "round",
                                d: "M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" }
                            }
                        }
                    div { class: "flex flex-col leading-tight",
                        span { class: "font-semibold text-[15px]", "md-sync" }
                        span { class: "text-[11px] text-muted-foreground", "Markdown 同步渲染器" }
                    }
                }
                div { class: "flex items-center gap-3",
                    if syncing {
                        span { class: "badge badge-info",
                            span { class: "spinner spinner-sm" }
                            "同步中…"
                        }
                    } else if watching {
                        span { class: "badge badge-success",
                            span { class: "pulse-dot" }
                            "监听中"
                        }
                    } else {
                        span { class: "badge",
                            span { class: "w-1.5 h-1.5 rounded-full bg-muted-foreground/50" }
                            "待机"
                        }
                    }
                    if !source.is_empty() {
                        span { class: "hidden md:flex max-w-[220px] truncate text-[11px] text-muted-foreground",
                            title: "{source}", "{source}" }
                    }
                    button {
                        class: "btn btn-ghost btn-icon",
                        title: "刷新状态",
                        onclick: move |_| {
                            let mut state = state;
                            dioxus::prelude::spawn(async move {
                                match api::fetch_state().await {
                                    Ok(p) => {
                                        state.apply_runtime(&p);
                                        *state.status.write() = StatusKind::Ok("状态已刷新".into());
                                    }
                                    Err(e) => *state.status.write() = StatusKind::Err(e),
                                }
                            });
                        },
                        svg { class: "size-4", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                            path { stroke_linecap: "round", stroke_linejoin: "round",
                                d: "M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" }
                            }
                    }
                }
            }
        }
    }
}
