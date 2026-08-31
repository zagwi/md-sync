use dioxus::prelude::*;
use serde_json::json;

use crate::state::{save_and_apply, AppState};

#[component]
pub fn PluginCard() -> Element {
    let mut state = use_context::<AppState>();
    let meta = state.meta.read().clone();
    let Some(meta) = meta else { return rsx! {} };

    let plugin = state.plugin.read().clone();
    let current = meta.plugins.iter().find(|p| p.name == plugin).cloned();

    rsx! {
        section { class: "card",
            div { class: "card-header",
                div { class: "flex items-center gap-2",
                    svg { class: "size-4 text-muted-foreground", fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                        path { stroke_linecap: "round", stroke_linejoin: "round",
                            d: "M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" } }
                    h2 { class: "text-base font-semibold", "插件" }
                    span { class: "badge badge-secondary", "核心" }
                }
                p { class: "text-xs text-muted-foreground",
                    "选择用于解析 Markdown 源文档的渲染插件，决定输出产物的能力与排版风格。" }
            }
            div { class: "card-body",
                div { class: "flex flex-col sm:flex-row items-end gap-3",
                    div { class: "w-full sm:max-w-sm",
                        label { class: "field-label", "渲染插件" }
                        select {
                            class: "shadcn-select",
                            value: "{plugin}",
                            onchange: move |evt| {
                                let name = evt.value();
                                if name == plugin { return; }
                                let Some(m) = state.meta.read().clone() else { return };
                                let Some(p) = m.plugins.iter().find(|p| p.name == name).cloned() else { return };
                                *state.plugin.write() = p.name.clone();
                                *state.schema.write() = p.parser_schema.clone();
                                let schema = p.parser_schema.clone();
                                dioxus::prelude::spawn(async move {
                                    save_and_apply(state, json!({ "plugin": name, "schema": schema })).await;
                                });
                            },
                            for p in meta.plugins.iter() {
                                option {
                                    value: "{p.name}",
                                    selected: p.name == plugin,
                                    "{p.label}"
                                }
                            }
                        }
                    }
                    if let Some(cur) = &current {
                        div { class: "plugin-meta",
                            div { class: "flex flex-wrap items-center gap-2",
                                span { class: "badge badge-outline", "{cur.plugin_type}" }
                                span { class: "text-xs text-muted-foreground", "v{cur.version}" }
                            }
                            p { class: "text-xs text-muted-foreground mt-1", "{cur.label} · schema: {cur.parser_schema}" }
                        }
                    }
                }
            }
        }
    }
}
