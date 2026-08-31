use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Deserialize)]
pub struct PluginInfo {
    pub name: String,
    pub label: String,
    #[serde(rename = "parser_schema")]
    pub parser_schema: String,
    #[serde(default)]
    pub version: String,
    #[serde(rename = "type", default)]
    pub plugin_type: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct StyleInfo {
    pub name: String,
    pub label: String,
    pub schema: String,
    #[serde(default)]
    pub group: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Meta {
    pub plugins: Vec<PluginInfo>,
    pub styles: Vec<StyleInfo>,
    #[allow(dead_code)] // API 契约字段：后端下发的可用格式列表，前端使用内置 FORMATS 常量
    pub formats: Vec<String>,
    pub langs: Vec<String>,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq)]
#[serde(default)]
pub struct TypographyConfig {
    pub enabled: bool,
    pub cjk_latin_space: bool,
    pub cjk_digit_space: bool,
    pub number_unit_space: bool,
    pub fullwidth_punct_no_space: bool,
    pub en_no_space_before_punct: bool,
    pub en_space_after_punct: bool,
    pub en_collapse_spaces: bool,
}

impl Default for TypographyConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            cjk_latin_space: true,
            cjk_digit_space: true,
            number_unit_space: true,
            fullwidth_punct_no_space: true,
            en_no_space_before_punct: true,
            en_space_after_punct: true,
            en_collapse_spaces: true,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct OutputFile {
    pub path: String,
    pub filename: String,
    pub lang: String,
    pub format: String,
    #[serde(default)]
    pub pdf: bool,
    #[serde(default)]
    pub is_source: bool,
    #[serde(default)]
    pub exists: bool,
    #[serde(default)]
    pub size: u64,
    #[serde(default)]
    pub mtime: f64,
    #[serde(default)]
    pub mtime_fmt: String,
    #[serde(default)]
    pub status: String, // synced | stale | missing
}

#[derive(Debug, Clone, Deserialize)]
pub struct StatePayload {
    pub source: String,
    pub output_dir: String,
    pub plugin: String,
    pub schema: String,
    pub style_zh: String,
    pub style_en: String,
    #[serde(default)]
    pub formats: Vec<String>,
    #[serde(default)]
    pub langs: Vec<String>,
    #[serde(default)]
    pub naming: String,
    #[serde(default)]
    pub blink: bool,
    #[serde(default)]
    pub typography: TypographyConfig,
    #[serde(default)]
    pub watching: bool,
    #[serde(default)]
    pub syncing: bool,
    #[serde(default)]
    pub output_files: Vec<OutputFile>,
    #[serde(default)]
    #[allow(dead_code)] // API 契约字段：后端下发的统计信息，前端暂未展示
    pub last_stats: serde_json::Value,
}

#[derive(Debug, Clone, Deserialize)]
pub struct LogLine {
    #[allow(dead_code)] // API 契约字段：日志行 id，轮询游标使用页级 max_id
    pub id: i64,
    pub text: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct LogPage {
    pub lines: Vec<LogLine>,
    pub max_id: i64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SimpleResp {
    #[serde(default)]
    pub ok: bool,
    #[serde(default)]
    #[allow(dead_code)] // API 契约字段：后端回执中的启动时间标记
    pub started: bool,
    #[serde(default)]
    pub watching: bool,
    #[serde(default)]
    pub removed: i64,
    #[serde(default)]
    pub errors: Vec<String>,
    #[serde(default)]
    pub path: String,
}

/// 输出格式展示元信息（图标/配色与 Web 前端一致）
pub const FORMATS: [FormatMeta; 5] = [
    FormatMeta {
        key: "html",
        label: "HTML",
        color: "html",
        icon: "M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4",
    },
    FormatMeta {
        key: "md",
        label: "Markdown",
        color: "markdown",
        icon: "M7 8h10M7 12h4m1 8l-4-4H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-3l-4 4z",
    },
    FormatMeta {
        key: "pdf",
        label: "PDF",
        color: "destructive",
        icon: "M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z",
    },
    FormatMeta {
        key: "docx",
        label: "DOCX",
        color: "docx",
        icon: "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z",
    },
    FormatMeta {
        key: "epub",
        label: "EPUB",
        color: "violet",
        icon: "M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253",
    },
];

#[derive(Debug, Clone, Copy)]
pub struct FormatMeta {
    pub key: &'static str,
    pub label: &'static str,
    pub color: &'static str,
    pub icon: &'static str,
}

/// 中文排版规则（4 条）
pub const TYPO_ZH_RULES: [(&str, &str); 4] = [
    ("cjk_latin_space", "中英文之间加空格（支持ChatGPT → 支持 ChatGPT）"),
    ("cjk_digit_space", "中文与数字之间加空格（花100元 → 花 100 元）"),
    ("number_unit_space", "数字与单位之间加空格（20Gbps → 20 Gbps；90°、15% 除外）"),
    ("fullwidth_punct_no_space", "全角标点前后不留空格"),
];

/// 英文排版规则（3 条）
pub const TYPO_EN_RULES: [(&str, &str); 3] = [
    ("en_no_space_before_punct", "英文标点前不留空格（Hello ,world → Hello,world）"),
    ("en_space_after_punct", "英文标点后加空格（Hello,world → Hello, world）"),
    ("en_collapse_spaces", "折叠连续空格（多空格压缩为单空格）"),
];
