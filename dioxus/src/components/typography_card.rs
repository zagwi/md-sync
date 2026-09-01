use dioxus::prelude::*;
use serde_json::json;

use crate::api;
use crate::state::{AppState, StatusKind};
use crate::types::{TYPO_EN_RULES, TYPO_ZH_RULES, TypographyConfig};

fn set_typo_field(mut state: AppState, field: &'static str, value: bool) {
    let mut t = *state.typography.read();
    match field {
        "enabled" => t.enabled = value,
        "cjk_latin_space" => t.cjk_latin_space = value,
        "cjk_digit_space" => t.cjk_digit_space = value,
        "number_unit_space" => t.number_unit_space = value,
        "fullwidth_punct_no_space" => t.fullwidth_punct_no_space = value,
        "en_no_space_before_punct" => t.en_no_space_before_punct = value,
        "en_space_after_punct" => t.en_space_after_punct = value,
        "en_collapse_spaces" => t.en_collapse_spaces = value,
        _ => return,
    }
    *state.typography.write() = t;
    let payload = json!({ "typography": t });
    dioxus::prelude::spawn(async move {
        match api::save_config(&payload).await {
            Ok(p) => state.apply_runtime(&p),
            Err(e) => *state.status.write() = StatusKind::Err(e),
        }
    });
}

fn typo_value(t: &TypographyConfig, field: &str) -> bool {
    match field {
        "enabled" => t.enabled,
        "cjk_latin_space" => t.cjk_latin_space,
        "cjk_digit_space" => t.cjk_digit_space,
        "number_unit_space" => t.number_unit_space,
        "fullwidth_punct_no_space" => t.fullwidth_punct_no_space,
        "en_no_space_before_punct" => t.en_no_space_before_punct,
        "en_space_after_punct" => t.en_space_after_punct,
        "en_collapse_spaces" => t.en_collapse_spaces,
        _ => false,
    }
}

#[component]
pub fn TypographyCard() -> Element {
    let state = use_context::<AppState>();
    let typo = *state.typography.read();
    let enabled = typo.enabled;

    rsx! {
        section { class: "card",
            div { class: "card-header",
                div { class: "flex items-center gap-2 flex-wrap",
                    svg { class: "size-4 text-muted-foreground", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                        path { stroke_linecap: "round", stroke_linejoin: "round",
                            d: "M4 6h16M4 12h16M4 18h7" } }
                    h2 { class: "text-base font-semibold", "文档排版规范" }
                    p { class: "text-xs text-muted-foreground ml-auto text-right",
                        "自动规整中英文混排间距，输出更专业、更一致的排版。" }
                }
            }
            div { class: "card-body",
                div { class: "flex items-center justify-between rounded-lg border border-border bg-muted/40 px-4 py-3",
                    div {
                        p { class: "text-sm font-medium", "启用自动排版" }
                        p { class: "text-xs text-muted-foreground", "同步渲染时自动应用以下排版规则" }
                    }
                    button {
                        class: if enabled { "switch switch-on" } else { "switch" },
                        role: "switch",
                        aria_checked: enabled.to_string(),
                        onclick: move |_| set_typo_field(state, "enabled", !typo_value(&typo, "enabled")),
                        span { class: "switch-thumb" }
                    }
                }

                div { class: "grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4",
                    RuleGroup { title: "中文配置", subtitle: "中文语境下的自动排版规则", rules: TYPO_ZH_RULES.to_vec(), state: state }
                    RuleGroup { title: "英文配置", subtitle: "英文语境下的自动排版规则", rules: TYPO_EN_RULES.to_vec(), state: state }
                }

                div { class: "flex items-center justify-end gap-2 mt-5",
                    p { class: "text-xs text-muted-foreground mr-auto",
                        "规范化将直接修改源文档，建议先备份。" }
                    button {
                        class: "btn btn-outline-destructive",
                        onclick: move |_| {
                            let mut state = state;
                            *state.busy.write() = true;
                            dioxus::prelude::spawn(async move {
                                match api::normalize().await {
                                    Ok(_) => {
                                        *state.status.write() = StatusKind::Ok("源文档规范化完成".into());
                                        if let Ok(p) = api::fetch_state().await { state.apply_runtime(&p); }
                                    }
                                    Err(e) => *state.status.write() = StatusKind::Err(e),
                                }
                                *state.busy.write() = false;
                            });
                        },
                        svg { class: "size-4", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                            path { stroke_linecap: "round", stroke_linejoin: "round",
                                d: "M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" } }
                        "规范化源文档"
                    }
                }
            }
        }
    }
}

#[component]
fn RuleGroup(
    title: String,
    subtitle: String,
    rules: Vec<(&'static str, &'static str)>,
    state: AppState,
) -> Element {
    let state = state;
    rsx! {
        div { class: "rounded-lg border border-border p-4",
            p { class: "text-sm font-medium", "{title}" }
            p { class: "text-xs text-muted-foreground mb-3", "{subtitle}" }
            div { class: "flex flex-col gap-2.5",
                for (field, desc) in rules {
                    div { class: "flex items-start justify-between gap-3",
                        div { class: "min-w-0",
                            p { class: "text-[13px] text-foreground/90 break-words", "{desc}" }
                        }
                        button {
                            class: if typo_value(&state.typography.read(), field) { "switch switch-on" } else { "switch" },
                            onclick: move |_| {
                                let cur = typo_value(&state.typography.read(), field);
                                set_typo_field(state, field, !cur);
                            },
                            span { class: "switch-thumb" }
                        }
                    }
                }
            }
        }
    }
}
