use std::collections::{BTreeMap, HashSet};

use dioxus::prelude::*;
use serde_json::json;

use crate::api;
use crate::state::{AppState, StatusKind};
use crate::types::StyleInfo;

#[component]
pub fn ThemeDropdown(state: AppState) -> Element {
    let mut state = state;
    let mut open = use_signal(|| false);
    let mut collapsed = use_signal(HashSet::<usize>::new);

    let meta = state.meta.read().clone();
    let schema = state.schema.read().clone();
    let style_zh = state.style_zh.read().clone();

    // 按当前 schema 过滤样式；为空时退化为全部样式
    let styles: Vec<StyleInfo> = meta
        .as_ref()
        .map(|m| {
            let filtered: Vec<StyleInfo> = m
                .styles
                .iter()
                .filter(|s| s.schema == schema)
                .cloned()
                .collect();
            if filtered.is_empty() {
                m.styles.clone()
            } else {
                filtered
            }
        })
        .unwrap_or_default();

    // 分组为 owned 数据，rsx 中消耗迭代，闭包捕获 owned 值
    let mut groups: BTreeMap<String, Vec<StyleInfo>> = BTreeMap::new();
    for s in styles {
        let g = s.group.clone().unwrap_or_else(|| "标准模板".to_string());
        groups.entry(g).or_default().push(s);
    }
    let groups: Vec<(String, Vec<StyleInfo>)> = groups.into_iter().collect();

    let current_label = groups
        .iter()
        .flat_map(|(_, v)| v.iter())
        .find(|s| s.name == style_zh)
        .map(|s| s.label.clone())
        .unwrap_or_else(|| {
            if style_zh.is_empty() {
                "默认主题".to_string()
            } else {
                style_zh.clone()
            }
        });

    rsx! {
        div { class: "relative",
            label { class: "field-label", "渲染主题" }
            button {
                class: "shadcn-select w-full",
                onclick: move |_| {
                    let next = !*open.read();
                    *open.write() = next;
                },
                span { class: "truncate", "{current_label}" }
                svg {
                    class: if *open.read() { "size-4 shrink-0 text-muted-foreground rotate-180 transition-transform" } else { "size-4 shrink-0 text-muted-foreground transition-transform" },
                    fill: "none", view_box: "0 0 24 24", stroke: "currentColor", stroke_width: "2",
                    path { stroke_linecap: "round", stroke_linejoin: "round", d: "M19 9l-7 7-7-7" }
                }
            }
            if *open.read() {
                div { class: "theme-dropdown",
                    div { class: "theme-dropdown-scroll",
                        for (idx, (group_name, group_styles)) in groups.into_iter().enumerate() {
                            div { class: "mb-1",
                                if group_name != "标准模板" {
                                    button {
                                        class: "theme-group-head",
                                        onclick: {
                                            let idx = idx;
                                            move |_| {
                                                let mut set = collapsed.write();
                                                if set.contains(&idx) { set.remove(&idx); } else { set.insert(idx); }
                                            }
                                        },
                                        span { "{group_name}" }
                                        span { class: "text-muted-foreground", if collapsed.read().contains(&idx) { "›" } else { "⌄" } }
                                    }
                                } else {
                                    div { class: "theme-group-head cursor-default",
                                        span { "{group_name}" }
                                    }
                                }
                                if !collapsed.read().contains(&idx) {
                                    for s in group_styles {
                                        button {
                                            class: if s.name == style_zh { "theme-item theme-item-selected" } else { "theme-item" },
                                            onclick: {
                                                let name = s.name.clone();
                                                move |_| {
                                                    *open.write() = false;
                                                    *state.style_zh.write() = name.clone();
                                                    let value = name.clone();
                                                    dioxus::prelude::spawn(async move {
                                                        match api::save_config(&json!({ "style_zh": value })).await {
                                                            Ok(p) => state.apply_runtime(&p),
                                                            Err(e) => *state.status.write() = StatusKind::Err(e),
                                                        }
                                                    });
                                                }
                                            },
                                            if s.name == style_zh { span { class: "theme-check", "✓" } }
                                            span { class: "truncate", "{s.label}" }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                div { class: "fixed inset-0 z-40", onclick: move |_| *open.write() = false }
            }
        }
    }
}
