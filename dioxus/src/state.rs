use dioxus::prelude::*;
use serde_json::json;

use crate::types::*;

/// 顶部状态提示
#[derive(Debug, Clone, PartialEq)]
pub enum StatusKind {
    None,
    #[allow(dead_code)] // 保留：后续桌面端原生日志注入等场景会使用
    Info(String),
    Ok(String),
    Err(String),
}

/// 全局应用状态（全部为 Copy 的 Signal，通过 Context 共享）
#[derive(Clone, Copy, PartialEq)]
pub struct AppState {
    pub meta: Signal<Option<Meta>>,
    pub plugin: Signal<String>,
    pub schema: Signal<String>,
    pub source: Signal<String>,
    pub output_dir: Signal<String>,
    pub style_zh: Signal<String>,
    pub style_en: Signal<String>,
    pub formats: Signal<Vec<String>>,
    pub langs: Signal<Vec<String>>,
    pub naming: Signal<String>,
    pub blink: Signal<bool>,
    pub typography: Signal<TypographyConfig>,
    pub watching: Signal<bool>,
    pub syncing: Signal<bool>,
    pub output_files: Signal<Vec<OutputFile>>,
    pub logs: Signal<Vec<LogLine>>,
    pub last_log_id: Signal<i64>,
    pub status: Signal<StatusKind>,
    pub loaded: Signal<bool>,
    pub busy: Signal<bool>,
}

impl AppState {
    pub fn new() -> Self {
        Self {
            meta: use_signal(|| None),
            plugin: use_signal(String::new),
            schema: use_signal(String::new),
            source: use_signal(String::new),
            output_dir: use_signal(String::new),
            style_zh: use_signal(String::new),
            style_en: use_signal(String::new),
            formats: use_signal(Vec::new),
            langs: use_signal(Vec::new),
            naming: use_signal(|| "ts".to_string()),
            blink: use_signal(|| true),
            typography: use_signal(TypographyConfig::default),
            watching: use_signal(|| false),
            syncing: use_signal(|| false),
            output_files: use_signal(Vec::new),
            logs: use_signal(Vec::new),
            last_log_id: use_signal(|| 0),
            status: use_signal(|| StatusKind::None),
            loaded: use_signal(|| false),
            busy: use_signal(|| false),
        }
    }

    /// 首次加载：用后端 state 填充全部表单
    pub fn fill_from_payload(mut self, p: &StatePayload) {
        *self.source.write() = p.source.clone();
        *self.output_dir.write() = p.output_dir.clone();
        *self.plugin.write() = p.plugin.clone();
        *self.schema.write() = p.schema.clone();
        *self.style_zh.write() = p.style_zh.clone();
        *self.style_en.write() = p.style_en.clone();
        // 后端 formats/langs 是分离的（纯格式 × 纯语言），UI 内部用组合 key
        // （如 "html_zh"）表示一个勾选框，这里组合回去供 checkbox 回显。
        let mut combos: Vec<String> = Vec::new();
        for f in &p.formats {
            for l in &p.langs {
                combos.push(format!("{}_{}", f, l));
            }
        }
        *self.formats.write() = combos;
        *self.langs.write() = p.langs.clone();
        *self.naming.write() = p.naming.clone();
        *self.blink.write() = p.blink;
        *self.typography.write() = p.typography;
        *self.watching.write() = p.watching;
        *self.syncing.write() = p.syncing;
        *self.output_files.write() = p.output_files.clone();
    }

    /// 轮询刷新：只更新运行态与文件列表，避免打断用户输入
    pub fn apply_runtime(mut self, p: &StatePayload) {
        *self.watching.write() = p.watching;
        *self.syncing.write() = p.syncing;
        *self.output_files.write() = p.output_files.clone();
    }

    /// 收集当前 UI 值 → POST /api/config payload
    /// UI 内部 formats 存组合 key（"html_zh"），后端要求 formats/langs 分离
    /// （与 index.html 的 collect() 语义一致），这里拆开后发送。
    pub fn config_payload(self) -> serde_json::Value {
        let mut formats: Vec<String> = Vec::new();
        let mut langs: Vec<String> = Vec::new();
        for key in self.formats.read().iter() {
            if let Some((f, l)) = key.split_once('_') {
                if !formats.iter().any(|x| x == f) {
                    formats.push(f.to_string());
                }
                if !langs.iter().any(|x| x == l) {
                    langs.push(l.to_string());
                }
            }
        }
        json!({
            "source": self.source.read().as_str(),
            "output_dir": self.output_dir.read().as_str(),
            "plugin": self.plugin.read().as_str(),
            "schema": self.schema.read().as_str(),
            "style_zh": self.style_zh.read().as_str(),
            "style_en": self.style_en.read().as_str(),
            "formats": formats,
            "langs": langs,
            "naming": self.naming.read().as_str(),
            "blink": *self.blink.read(),
            "typography": *self.typography.read(),
        })
    }
}

pub fn toggle_in_vec(mut sig: Signal<Vec<String>>, item: &str) {
    let mut v = sig.read().clone();
    if v.iter().any(|x| x == item) {
        v.retain(|x| x != item);
    } else {
        v.push(item.to_string());
    }
    *sig.write() = v;
}

/// 异步保存配置，出错时写入顶部状态提示
pub async fn save_and_apply(mut state: AppState, extra: serde_json::Value) {
    let mut payload = state.config_payload();
    if let serde_json::Value::Object(map) = &mut payload {
        if let Some(obj) = extra.as_object() {
            for (k, v) in obj {
                map.insert(k.clone(), v.clone());
            }
        }
    }
    match crate::api::save_config(&payload).await {
        Ok(p) => state.apply_runtime(&p),
        Err(e) => *state.status.write() = StatusKind::Err(e),
    }
}
