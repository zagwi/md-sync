# md-sync

你用**中文或英文**写一份 Markdown 源文件，md-sync 会**自动把它翻译成另一种语言**，并同时生成
多种格式（HTML / PDF / Markdown 等等）的输出；源文件一改动，所有产物自动重新生成。适合文档工作者的效率提升神器。

- 一份源 → 多份产物：`html/zh`、`html/en`、`pdf/zh`、`pdf/en`、`md/zh`、`md/en`、 …
- 源文件改了 → 自动重新同步（带防抖 / debounce）
- 翻译走 **缓存优先**：已有译文直接复用，缺失才回退到 AI
- 自带 Web 风格UI 与原生桌面 GUI

---

## 为什么需要 md-sync？

把"重复、繁琐、易错的体力活"交给 md-sync，你专注内容本身。AI 工具确实能翻译文档、也能转换格式——但那解决的是"一次性"需求。
真正繁琐的是**频繁修改源文件**的场景：你每改一个词，就要手动走完
**修改 → 翻译 → 转换格式** 这一整条链路，而且往往要同时维护多个语言、多种格式的产物。
人工反复操作，不仅费时，更容易漏翻、漏转、版本对不齐。

**md-sync 就是为这个场景而生：你只管改源文件，其余交给工具。**

- **所改即所得**：基于文件监听，源文件一保存，所有语言 / 格式的产物在秒级内自动重新生成。
- **一次配置，长期受益**：翻译缓存 + 多输出配置只需设定一次，后续每次修改零额外操作。
- **不易出错**：译文与格式由工具统一处理，避免人工复制粘贴导致的漏翻、漏转、错版。



---

## 使用流程

md-sync 只负责「把稿子变成发布物」，**写稿仍用你最顺手的 Markdown 编辑器**：

1. **用你喜欢的 MD 编辑器写稿**：例如 [Typora](https://typora.io/)、Obsidian、VS Code 等，按习惯写好 `.md` 源文件即可。
2. **交给 md-sync 自动生成发布文档**：把源文件交给 md-sync，它会按配置自动同步出多种格式（HTML / PDF / Markdown / DOCX / EPUB）与多种语言（中文 / 英文）的产物，并在源文件改动时自动重新生成。
3. **套用 Typora 主题产出美观文档**：md-sync 可直接选用你电脑上 Typora 主题目录下的丰富主题资源（如 bloom-mist、night、claude-like 等），用它渲染出风格统一、美观的 HTML / PDF 文档，无需自己从头调样式。**前提是你本机已安装 [Typora](https://typora.io/)** —— 主题 CSS 存放在 Typora 的配置目录里，未安装则不会出现在「渲染风格」下拉框中。

> 一句话：**你只管在编辑器里写，md-sync 负责把它变成好看的发布稿。**

---

## 为什么用 Markdown 写稿（而不是 docx）

文档工作流的核心价值在于「可比较、可追溯、可协作」，而文件格式直接决定了你能否用成熟的工具链做到这一点：

- **Markdown 是纯文本**：基本不带二进制格式控制，本质就是 `.txt` 的增强版。因此它天然能接入程序员早已验证过的文本工作流：
  - **差异比较**：用 `diff` 或任意代码对比工具直接看增删改，逐行比对一目了然；
  - **版本管理**：用 `git` 等版本控制系统管理文档——提交历史、分支、回滚、多人协作 merge 全部可用；
  - **自动化**：文本可被脚本解析、批量处理，也能接入 CI 做自动检查与发布。
- **docx / pdf / epub 等是二进制格式**：内容被打包进专有容器，普通文本工具读不出可读的差异，无法做 `diff`，也很难在 git 中做有意义的版本对比（每次保存整文件变化，历史膨胀且不可读）。

> 结论：**Markdown 只作为编辑与协作的「源格式」，docx / pdf / epub 等二进制格式只应作为「输出格式」**——由 md-sync 从同一份 Markdown 源自动生成，而不应该反过来拿它们当编辑格式去反复手改。这样你既享受二进制格式的发布兼容性，又保留纯文本带来的差异比较与版本管理能力。

---

## 功能概览

| 能力 | 说明 |
|------|------|
| **多格式输出** | `html` `md` `pdf`，并可经插件扩展 `docx` / `epub`（PDF 由 Chromium 生成） |
| **多语言输出** | `zh` / `en`，翻译基于 `.translations.json` 缓存，缺失回退 AI（provider `auto`） |
| **文件监听** | 基于 `watchdog` 监听源文件，`debounce` 默认 1.5s，改动即同步 |
| **模板 / 主题** | `bwx`、`modern` 等内置样式；并可直接选用 Typora 主题目录（`~/.config/Typora/themes/`）下的主题（`typora-bloom-mist`、`typora-night`、`typora-claude-like` 等），自动兼容背景、dark/light 与代码块（`md-sync template` / `md-sync plugin`） |
| **翻译缓存** | `strategy: mapping` + `mapping_file`，译文只更新缓存字典、不直接出文件，渲染时再取用 |
| **Web 仪表盘** | 浏览器里配置源文件、查看解析信息、启动/停止监听、手动同步、查看同步事件与历史项目 |
| **桌面 GUI（Qt 原生）** | PySide6 原生界面，直接调用核心 pipeline，持续监听同步，零 HTTP 服务器 |

### Typora 主题兼容与 PDF 导出

md-sync 会自动发现本机 Typora 主题目录下的主题，并以
`typora-<主题名>` 形式选用（如 `typora-bloom-mist` / `typora-bloom-mist-dark` /
`typora-night` / `typora-claude-like` 等）。**需本机已安装 Typora**；不同系统的主题目录为：

- **Windows**：`%APPDATA%\Typora\themes`
- **macOS**：`~/Library/Application Support/abnerworks.Typora/themes`
- **Linux**：`~/.config/Typora/themes`

未检测到上述目录时，Typora 主题不会出现在「渲染风格」下拉框，GUI 会提示「未检测到本机已安装 Typora」。

渲染层针对「Typora 编辑器主题」与
「单文件 HTML / PDF」的差异做了兼容，保证这些主题在 md-sync 下也能正确呈现：

- **背景与卡片观感**：Typora 主题通常依赖「深色外壳 + 白色卡片 (`#write`)」双层结构；
  而 md-sync 仅生成 `<body><div id="write">` 单层，故依据主题的 `--bg` 变量派生底色与
  卡片实色底，light / dark 通用。
- **标题 / 正文颜色**：使用变量回退链（`--text` / `--text-color`），传统主题
  （如 night）不会被误洗成浅色、也不会把 dark 主题输出成 light。
- **代码块**：Typora 主题的 `pre` 规则多限定 `.md-fences` 类，而 md-sync 输出的是
  `<pre class="language-*">`，故补齐通用代码块样式，并兼容 bloom（`--code-bg`）、
  night（`--bg-color`）、claude（`--code-bg-color`）等多套变量命名。

PDF 导出（Chromium 引擎）已优化为「专业外观」：

- 强制 `print-color-adjust: exact`，整页保留主题背景色（不再整页发白）；
- 抑制页眉页脚（日期 / URL / 页码），同时兼容 `--no-pdf-header-footer` 与
  `--no-print-header-footer` 两种参数写法（不同 Chromium 版本/发行版各取其一）；
- `@page { margin: 0 }` 配合 `#write` 的 `padding`
  （`box-sizing: border-box` + `box-decoration-break: clone`），使每一页
  （含末页）的四周边距保持一致，且无刺眼白色边框。

## 目录结构

```
md-sync/
├── md_sync/                # 核心包
│   ├── cli.py              # 命令行入口（md-sync start / sync / gui / status …）
│   ├── config.py           # ProjectConfig 解析（md-sync.yaml）
│   ├── qt_app.py           # 原生 PySide6 桌面 GUI（python -m md_sync.qt_app / md-sync gui）
│   ├── watcher.py          # 文件监听（watchdog + debounce）
│   ├── core/pipeline.py    # 同步主流程编排
│   ├── renderers/          # md / html 渲染器
│   ├── translate/          # 翻译管理 + AI 回退
│   ├── exporters/          # PDF 导出（Chromium）/ pandoc 导出（docx/epub）
│   ├── template/           # 模板管理
│   ├── plugin/             # 插件引擎（接口 / 注册表 / 加载器 / 钩子），不含插件实例
│   └── web/app.py          # FastAPI 后端 + 仪表盘
├── plugins/                # 内置插件（typora / resume / generic-markdown），各自携带模板
├── docs/example-plugin/    # 插件开发示例（resume-pack：源模板 + 解析器 + 渲染风格）
├── projects/               # 示例 / 项目配置（md-sync.yaml）
├── scripts/                # 构建与启动脚本
│   ├── build_app.py        # PyInstaller 单文件打包
│   └── start_server.py     # Web 模式启动（加载 projects/resume 配置）
├── tests/                  # 测试脚本
└── pyproject.toml
```

---

## 快速开始（推荐：原生桌面 GUI）

最常用、最省心的方式就是打开原生桌面 GUI —— 不需要浏览器、不启动任何 HTTP 服务器：

```bash
pip install -e .
pip install PySide6
python -m md_sync.qt_app      # 或：md-sync gui
```

启动后：选择源 `.md` 文件 → 选择输出目录 → 勾选需要的格式 / 语言 → 点〔开始监听〕。
源文件一保存（防抖 1.5s）即自动同步，产物出现在「输出文件」列表，可双击直接打开。

> 也支持 Web 仪表盘与命令行，见下方「使用方式」。

---

## 安装

要求 **Python ≥ 3.10**，并装有 `pip`。在仓库根目录（含 `pyproject.toml`）执行：

```bash
pip install -e .
```

依赖：`pyyaml`、`jinja2`、`watchdog`、`fastapi`、`uvicorn[standard]`、`httpx`。

桌面 GUI（Qt）额外需要 PySide6：

```bash
pip install PySide6
```

### 系统级前置依赖：Chromium（仅 PDF 导出需要）

PDF 由本机 **Chromium / Chrome** 以 headless 方式打印生成，**它是系统级前置依赖，`pip install` 不会自动下载**。导出 PDF 前请确保本机已安装，且 `md-sync` 能找到其二进制：

- **Arch / Manjaro**：`sudo pacman -S chromium`
- **Debian / Ubuntu 等**：`sudo apt install chromium`（或安装 Google Chrome）
- **macOS**：`brew install --cask chromium`（或安装 Google Chrome）
- **Windows**：从 [chromium.org](https://www.chromium.org/) 或 Google Chrome 官网安装

安装后 `md-sync` 会自动探测 `/usr/bin/chromium`、`google-chrome*`、`/snap/bin/chromium` 等常见路径；若装在非常规位置，可在配置中通过 `chromium_path` 显式指定二进制路径。未安装或探测不到时会直接报错中止，不会静默降级。

---

## 使用方式

> 推荐从**桌面 GUI（方式 A）** 开始；Web 仪表盘与命令行作为补充。

### 方式 A：桌面 GUI（Qt 原生，推荐）

原生 PySide6 界面，**直接调用核心 pipeline，不启动任何 HTTP 服务器**。
选好源文件、输出目录并勾选需要的格式/语言后点「开始监听」（两者都填好按钮才可点），源文件一改动（防抖 1.5s）即自动重新生成，
并在「输出文件」列表（单表，含格式列与语言列 badge）里显示每个产物的状态（已同步 / 待同步 / 不存在），可双击直接打开。

```bash
python -m md_sync.qt_app      # 或：md-sync gui
```

GUI 功能对照 Web 仪表盘：

- **📄 源文件**：选择 `.md` 源文件，自动检测源语言、章节数与待译条数（同时作为「开始监听」的必填项）
- **🎯 输出设置**：每种格式一个组（HTML / Markdown / PDF / DOCX / EPUB，后两者需插件），组内勾选中文、英文；**默认均不勾选**，须至少为一种格式勾选一种语言才可开始；PDF 组下方附带「页边距」下拉（15 / 20 / 25mm）控制 PDF 留白
- **〔开始监听 / 停止监听〕**：选定源文件且填好输出目录后按钮才可点击；开启监听后源改动自动同步，首次启动立即同步一次
- **输出文件列表**：以单表呈现，每行含状态、格式、语言（带 badge）、文件、修改时间，双击〔打开文件〕或右键〔复制路径〕
- **〔打开输出目录〕**：一键打开生成文件所在目录
- **同步日志**：本次会话的同步日志（时间、生成文件、耗时、错误）

### 方式 B：Web 仪表盘（浏览器访问）

```bash
# 1) 直接以示例项目启动（加载 projects/resume/md-sync.yaml）
python scripts/start_server.py
# 浏览器打开 http://127.0.0.1:8580

# 2) 或用 CLI（不读配置也能开，进浏览器再配置）
md-sync start
```

仪表盘功能：

- **📄 源文件**：点「打开」选择 `.md` 源文件，自动解析并展示语言、章节与条目数
- **🎯 同步后生成**：列出所有输出目标（格式 / 语言 / 模板 / PDF / 路径），可分别开关
- **⚙️ 转换模板** / **🌐 翻译方式**：选择与配置模板、翻译策略与 AI provider
- **〔同步一次〕**：手动触发一次同步
- **〔开始监听 / 停止监听〕**：开启/关闭文件监听（源改动自动同步）
- **🔔 同步事件**：本次会话的同步日志（时间、生成文件、耗时、错误）
- **⏱ 同步历史**：历史项目列表，点「打开」可切换已配置过的项目
- **📤 输出文件**：表格列出每个产物（格式 / 语言 / 文件名 / 大小 / 状态 / **最新时间** / 是否源文件 / 操作），可双击〔打开文件〕或右键〔复制路径〕

### 方式 C：命令行（无界面 / CI / 脚本）

```bash
md-sync init              # 在当前目录生成默认 md-sync.yaml
md-sync sync -c md-sync.yaml        # 一次性同步
md-sync status -c md-sync.yaml      # 查看解析信息与输出状态
md-sync dry-run -c md-sync.yaml     # 预览将发生的变化（不写文件）
md-sync template list               # 列出可用模板
md-sync plugin list                 # 列出已安装插件
```

---

## 制作插件

md-sync 的插件分两类目录：**`md_sync/plugin/`** 是插件**引擎代码**（接口 / 注册表 / 加载器 / 钩子），**`plugins/`** 与 `~/.md-sync/plugins/`** 才是插件**实例**（具体模板、解析器）。你只需按约定放一个目录，无需改引擎代码即可扩展渲染风格、解析器或钩子。

完整可运行示例见 **`docs/example-plugin/`**（`resume-pack`：源模板 + 解析器 + 渲染风格 + 过滤器四件套）。

### 插件目录结构

```text
my-plugin/
├── plugin.yaml          # 插件清单（必填）
├── template.md          # 源模板（仅 pack 类型需要；用户按此格式写稿）
├── parser.py            # 解析器（仅 pack/parser 类型需要）
├── templates/           # 渲染风格（Jinja2 + CSS）
│   └── <style-name>/    #   一个子目录 = 一种风格，目录名即风格名
│       ├── document.html.j2
│       └── style.css
└── filters.py           # 可选：自定义 Jinja2 过滤器
```

### plugin.yaml 清单字段

```yaml
name: resume-pack            # 唯一标识，用于 md-sync plugin remove <name>
version: "1.0"
description: 一句话描述
author: Your Name
type: pack                  # render | parser | pack | translate | export | hook
# type=pack 时：解析器 + 源模板 + 渲染风格打包在一起

parser:                     # 仅 pack/parser 需要
  class: parser.MyResumeParser   # parser.py 里的类名
  schema: my-resume              # 配置里 schema: my-resume 引用此插件

template: template.md       # 源模板相对路径（pack 类型）

templates:                  # 本插件提供的渲染风格名（须与 templates/<name>/ 目录一致）
  - example-style

hooks:                      # 监听的流水线钩子
  - after_parse
  - after_render

dependencies: []
```

> ⚠️ **`templates:` 里写的风格名必须真实存在于 `templates/<name>/` 目录**，否则该风格不会出现在 `md-sync template list` 中（也不会报错，只是不生效）。

### 三种常见插件类型

| 类型 | `type` | 用途 | 关键文件 |
|------|--------|------|----------|
| 渲染风格 | `render` | 只提供新 HTML/CSS 样式 | `templates/<style>/` |
| 自定义解析器 | `parser` | 让 md-sync 读懂特殊格式源稿 | `parser.py` |
| 完整插件包 | `pack` | 源模板 + 解析器 + 风格一体 | 上述全部 |

### 写解析器（pack/parser 类型）

继承 `md_sync.plugin.interface.ParserPlugin`，实现 `_parse` 把源文本转成 `Document` 模型：

```python
from md_sync.plugin.interface import ParserPlugin, PluginManifest, PLUGIN_TYPE_PACK
from md_sync.core.document import Document, Item, Section

class MyResumeParser(ParserPlugin):
    def __init__(self):
        self._manifest = PluginManifest(
            name="resume-pack",
            plugin_type=PLUGIN_TYPE_PACK,
            parser_schema="my-resume",   # 配置 schema 引用
        )

    @property
    def manifest(self) -> PluginManifest:
        return self._manifest

    def detect(self, text: str) -> bool:
        """自动识别该源是否属于本插件格式（返回 True 时被自动选用）。"""
        return "## 工作经历" in text and "---" in text

    def parse(self, text: str) -> Document:
        doc = Document()
        # ... 按自己的语法把 text 解析成 doc.sections / doc.items ...
        return doc
```

解析出的 `Document` 会交给模板渲染；`detect()` 命中后，配置里写 `schema: my-resume` 即可指定使用它。

### 写渲染风格（render/pack 类型）

在 `templates/<style-name>/` 下放 Jinja2 模板与 CSS。`Document` 模型的字段（`doc.name`、`doc.sections`、`doc.items` 等）即为模板可用变量。需要自定义过滤器时，在 `filters.py` 暴露一个 `filters` 字典：

```python
def highlight_metric(text: str) -> str:
    return text  # 实际实现略

filters = {            # DirectoryPlugin 自动发现并注册
    "highlight_metric": highlight_metric,
}
```

模板里即可使用 `{{ value | highlight_metric }}`。

### 安装与调试

```bash
# 从本地目录安装（复制到 ~/.md-sync/plugins/<name>/）
md-sync plugin install ./docs/example-plugin/resume-pack/

# 查看已安装插件与声明的风格
md-sync plugin list

# 查看某个插件的清单详情
md-sync plugin show resume-pack

# 从插件生成源模板，照着写稿
md-sync plugin template resume-pack -o my-resume.md

# 卸载
md-sync plugin remove resume-pack
```

也支持直接从 git 仓库或 PyPI 安装：

```bash
md-sync plugin install https://github.com/user/md-sync-plugin-xxx
md-sync plugin install some-pypi-package
```

安装后，配置 `md-sync.yaml` 指定该插件的 schema 与风格即可使用：

```yaml
source: my-resume.md
schema: my-resume          # 对应 parser.schema
outputs:
  - format: html
    lang: zh
    style: example-style   # 对应 templates 里的风格名
```

---

## 配置文件（md-sync.yaml）

```yaml
project: my-project                 # 项目名（仅内部标识）
source: README.md                   # 源 Markdown 路径
schema: resume                      # 解析模式（如 resume）

output_root: ./output               # 输出根目录（可省略，写在各 path 里也行）

outputs:
  - format: html                    # html / md
    lang: zh                        # zh / en
    pdf: true                       # 是否导出 PDF（需 Chromium）
    pdf_path: output/zh.pdf
    style: bwx                      # 模板/主题名
    page_size: A4                   # 页面尺寸：A4 / A3 / A5 / Letter / Legal（PDF、DOCX 通用）
    page_margin: ""                 # 留空 = 该尺寸的标准边距（A4→15mm、Letter→25.4mm…）；
                                    # 也可显式指定，如 "15mm" 或 "5mm 8mm"（上/下 左/右）
  - format: md
    lang: en
  - format: html
    lang: en
    pdf: true
    style: bwx

watch:
  enabled: true
  debounce: 1.5                     # 防抖秒数

translation:
  strategy: mapping                 # 缓存优先
  mapping_file: .translations.json  # 译文缓存文件
  ai:
    provider: auto                  # 缺失译文回退的 AI provider

web_ui:
  enabled: true
  host: 127.0.0.1
  port: 8580
```

要点：

- `source` 指向你的源 Markdown。
- `outputs` 每项是一个「格式 × 语言」组合；`html` 可指定 `style` 并可选 `pdf`。
- `translation.mapping_file` 是译文缓存：翻译只更新该 JSON，渲染时再取用，因此**翻译本身不直接生成输出文件**。

---

## 打包

### 1. Python 包（wheel）

```bash
pip install build
python -m build
# 产物在 dist/md_sync-*.whl，可 pip install 分发
```

CLI 入口：`md-sync`（见 `pyproject.toml` 的 `[project.scripts]`）。

### 2. 单文件可执行程序（PyInstaller，推荐）

仓库自带跨平台构建脚本 `scripts/build_app.py`，在当前系统上产出免 Python 环境的可执行文件：

```bash
pip install pyinstaller            # 或 pip install -e ".[build]"
python scripts/build_app.py                # 产出 dist/md-sync (Linux/macOS) 或 dist/md-sync.exe (Windows)
python scripts/build_app.py --clean        # 清缓存后重新构建
```

**产物位置（均在本项目 `dist/` 目录下）：**

| 平台 | 产物路径 | 说明 |
|------|----------|------|
| Linux   | `dist/md-sync`        | ELF 可执行文件，直接 `./dist/md-sync --help` 运行 |
| macOS   | `dist/md-sync`        | 同 Linux 命名，需在 macOS 上构建 |
| Windows | `dist/md-sync.exe`    | 需在 Windows 上构建 |

> 当前环境是 Linux，本地构建只会得到 `dist/md-sync`；Windows / macOS 二进制需在对应系统
> 或下面的 CI 上产出。`dist/`、`build/`、`*.spec` 都已写入 `.gitignore`，不会进版本库。

- 脚本自动把 `md_sync/templates`、`md_sync/themes`、`md_sync/web` 等资源打进 bundle，
  运行时通过 `md_sync.template.manager._find_install_dir()` 解析，**无需用户机器安装任何东西**。
- **构建 Python 版本建议用 3.12**（PyInstaller 官方稳定支持）。在 3.14 上需排除 `mypy`
  （脚本已默认 `--exclude-module mypy`，因其 mypyc 扩展与 3.14 的 CArchive 压缩不兼容会导致
  exe 启动即崩溃 `decompression resulted in return code -3`）。

### 3. 跨平台自动构建（GitHub Actions）

`.github/workflows/build.yml` 在 `ubuntu-latest` / `windows-latest` / `macos-latest`
三个 runner 上分别运行 `python scripts/build_app.py`，将三个平台的可执行文件作为 Release artifact 上传。

```bash
# 打 tag 触发自动构建并发布 Release
git tag v1.0.0 && git push origin v1.0.0
# 到 GitHub Releases 页面下载：md-sync-linux / md-sync-windows.exe / md-sync-macos
```

若只想临时取三平台二进制（不发 Release），在 Actions 对应 run 的 Artifacts 里下载即可，
artifact 命名为 `md-sync-ubuntu-latest` / `md-sync-windows-latest` / `md-sync-macos-latest`。

---

## 常见问题

- **「打开」选完文件后输入框清空**：这是预期行为——选文件即加载，无需手填路径。
- **翻译后为什么还有 HTML？** 翻译只换文字（语言），HTML 由渲染器（转换）生成（格式），两者是各自独立的产物，可分别勾选。
- **Qt GUI 的同步日志在哪？** 直接显示在 GUI 的「同步日志」面板中。
- **端口被占用？** Web 模式默认使用 8580；若该端口被占用，改 `web_ui.port` 即可。

---

## 许可证

MIT。

