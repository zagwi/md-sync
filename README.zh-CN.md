# md-sync

你用**中文或英文**写一份 Markdown 源文件，md-sync 会**自动把它翻译成另一种语言**，并同时生成
多种格式（HTML / PDF / Markdown / DOCX / EPUB）的输出；源文件一改动，所有输出自动重新生成。适合文档工作者的效率提升神器。

- 一份源 → 多份输出：`zh` / `en` × HTML / PDF / Markdown / DOCX / EPUB，一次写稿全部到位
- 源文件保存即自动重新生成，无需任何手动操作
- 翻译**缓存优先**：已有译文直接复用，缺失才回退 AI，译稿不被重复翻译
- 三条入口任选：**桌面 GUI、Web 看板、命令行**

> **英文原文（默认）：[English (default)](README.md)** · 本文为简体中文翻译版
>
> 本文分三部分：**[面向兴趣者](#面向兴趣者)、[面向用户](#面向用户)** 与 **[开发者指南](#开发者指南)**，按需跳转。

---

## 面向兴趣者

### 为什么需要 md-sync？

把「重复、繁琐、易错的体力活」交给 md-sync，你专注内容本身。AI 工具确实能翻译文档、也能转换格式——但那解决的是「一次性」需求。
真正繁琐的是**频繁修改源文件**的场景：你每改一个词，就要手动走完
**修改 → 翻译 → 转换格式** 这一整条链路，而且往往要同时维护多个语言、多种格式的输出。
人工反复操作，不仅费时，更容易漏翻译、漏转、版本对不齐。

**md-sync 就是为这个场景而生：你只管改源文件，其余交给工具。**

- **所改即所得**：源文件一保存，所有语言 / 格式的输出在秒级内自动重新生成。
- **一次配置，长期收益**：翻译缓存与多输出配置只需设定一次，后续每次修改零额外操作。
- **不易出错**：译文与格式由工具统一处理，避免人工复制粘贴导致的漏翻、漏转、错版。

### 功能概览

| 能力 | 说明 |
|------|------|
| **多格式输出** | HTML / PDF / Markdown / DOCX / EPUB，可任意组合 |
| **多语言输出** | 中文 / 英文自动互译，翻译缓存优先，缺失时回退 AI |
| **自动重新生成** | 源文件一保存，所有选中的格式 / 语言输出自动更新 |
| **渲染风格** | 内置多套风格，可直接选用本机 [Typora](https://typora.io/) 主题 |
| **桌面 GUI** | 原生窗口界面，选源文件、勾格式、即点即用 |
| **Web 看板** | 无需配置文件，浏览器里上传源文件、看日志、下载输出 |
| **标准公文** | 内置模板，一键导出符合 GB/T 9704-2012 的公文 `docx` / `pdf` |

### 为什么用 Markdown 写稿（而不是 docx）

文档工作流的核心价值体现在「可比较、可追溯、可协作」，而这份能力取决于**源格式**：

- **Markdown 是纯文本**：本质是 `.txt` 的增强版，因此可直接接入成熟的文本工作流——
  用 `diff` 逐行比对增删改、用 `git` 做版本管理（提交历史、分支、回滚、多人协作）、被脚本自动批处理与检查。
- **docx / pdf / epub 是二进制格式**：内容被打进专有容器，普通文本工具读出可读差异，无法做有意义的版本对比，历史会不断膨胀且不可读。

> 结论：**Markdown 只作为编辑与协作的「源格式」，docx / pdf / epub 等只作为「输出格式」**——由 md-sync 从同一份源直接自动生成。这样既享受二进制格式的发布兼容性，又保留纯文本带来的差异比较与版本管理能力。

### 它的工作原理

一条源文件会走如下流程：**解析 → 翻译（缓存优先，缺译回退 AI）→ 排版规范化 → 渲染 → 导出**。
对实现细节（数据管线、插件机制、打包）感兴趣，跳转**[开发者指南](#开发者指南)**。

---

## 面向用户

### 安装

要求 **Python ≥ 3.10**，并装有 `pip`。在仓库根目录（含 `pyproject.toml`）执行：

```bash
pip install -e .
```

（依赖会自动安装。）桌面 GUI 额外需要：

```bash
pip install -e ".[gui]"
```

#### PDF 导出：需本机安装 Chromium

PDF 由本机 **Chromium / Chrome** 生成，**`pip install` 不会自动下载浏览器**。导出 PDF 前请确认本机已安装：

- **Arch / Manjaro**：`sudo pacman -S chromium`
- **Debian / Ubuntu 等**：`sudo apt install chromium`（或安装 Google Chrome）
- **macOS**：`brew install --cask chromium`（或安装 Google Chrome）
- **Windows**：从 [chromium.org](https://www.chromium.org/) 或 Google Chrome 官网安装

安装后 md-sync 会自动探测常见路径（`/usr/bin/chromium` 等）；装在非常规位置时，可在配置里用 `chromium_path` 显式指定二进制路径。

### 使用方式

> 桌面 app、Web 看板与命令行三套入口共用同一套同步引擎，按场景任选。

#### 方式 A：桌面 app（Dioxus，单文件，免 Python）

Dioxus 原生窗口，同步引擎**内嵌在同一个可执行文件里**：最终交付单个可执行文件，拷到任何机器双击即用，**目标机器无需安装 Python**。

```bash
python scripts/build_desktop.py   # 一条命令 → dist/md-sync-ui（内嵌后端，免 Python）
./dist/md-sync-ui                 # 运行——内嵌后端自动在 :8580 启动
```

app 与后端通过**本机 Unix socket** 通信（无 HTTP、不监听/访问任何网络端口）。启动时 app 依次：① 若 IPC socket 已有后端在跑，直接复用；② 否则找同目录（或仓库 `dist/`）的打包后端，或把内嵌副本解压到缓存目录后执行 `md-sync ipc`；③ 都没有才回退 `python -m md_sync.web.ipc`（仅开发环境）。输出是真正的单文件 app：把 `md-sync-ui` 拷走即用。

#### 方式 B：Web 看板

无需配置文件的浏览器界面，网页里一键完成：上传源文件、设输出目录 / 格式 / 语言、开关排版规范、实时查看同步日志、下载输出。

```bash
md-sync start                  # 打开 http://127.0.0.1:8580
python -m uvicorn md_sync.web.app:app --host 127.0.0.1 --port 8580   # 等价命令，无需入口点
```

#### 方式 C：命令行

```bash
md-sync init                  # 在当前目录生成默认 md-sync.yaml
md-sync sync -c md-sync.yaml          # 一次性同步
md-sync status -c md-sync.yaml        # 查看配置解析与输出状态
md-sync dry-run -c md-sync.yaml       # 预览将发生的变化（不写文件）
md-sync template list                 # 列出可用模板
md-sync plugin list                   # 列出已安装插件
```

### Typora 主题选用

md-sync 会自动发现本机 Typora 主题并以 `typora-<主题名>` 形式选用
（如 `typora-bloom-mist`、`typora-night`、`typora-claude-like`）。**需本机已安装 Typora**；主题目录为：

- **Windows**：`%APPDATA%\Typora\themes`
- **macOS**：`~/Library/Application Support/abnerworks.Typora/themes`
- **Linux**：`~/.config/Typora/themes`

访问 <https://github.com/zagwi/typora-themes-util> 可一键安装 Typora 官方推荐的社区主题。
未检测到上述目录时，Typora 主题不会出现在「渲染风格」下拉框中（界面会提示「未检测到本机已安装 Typora」）。

### 公文插件（gongwen）：标准公文导出

只需按模板写一份 Markdown，即可一键导出**符合 GB/T 9704-2012《党政机关公文格式》**的公文 `docx` 与 `pdf`——红头、版式、落款、页码全部自动排版：

```yaml
source: 通知.md
schema: gongwen                     # 启用公文插件
outputs:
  - format: docx                    # 标准公文 docx
    lang: zh
    path: out/通知.docx
  - format: html
    lang: zh
    style: gongwen                  # 公文渲染风格
    pdf: true
    pdf_path: out/通知.pdf          # 标准公文 pdf
```

按模板填空即可（发文机关标志 / 发文字号 / 主送机关 / 标题 / 正文 / 附件 / 署名 / 成文日期 / 附注 / 版记）：

```bash
md-sync plugin template gongwen -o 通知.md    # 生成公文源稿模板
```

**字体**：Windows 一般自带国标字体；Linux 缺失时 GUI 会提示一键下载免费 Fandol 字体集。

### 配置文件（md-sync.yaml）

```yaml
project: my-project                 # 项目名（内部标识）
source: README.md                   # 源 Markdown 路径
schema: resume                      # 解析模式（resume 等，按需安装）

output_root: ./output               # 输出根目录（可省略）

outputs:
  - format: html                    # html / md / docx / epub
    lang: zh                        # zh / en
    pdf: true                       # 是否导出 PDF（需 Chromium）
    pdf_path: output/zh.pdf
    style: bwx                      # 渲染风格名
    page_size: A4                   # A4 / A3 / A5 / Letter / Legal
    page_margin: ""                 # 留空用标准边距；可显式如 "15mm" 或 "5mm 8mm"
  - format: html
    lang: en
    pdf: true
    style: bwx

watch:
  enabled: true
  debounce: 1.5                     # 源文件防抖秒数

translation:
  strategy: mapping                 # 缓存优先（缺译时才回退）
  mapping_file: .translations.json  # 译文缓存文件

typography:                          # 文档排版规范（默认全部开启）
  enabled: true                      # 总开关；关闭则输出与源完全一致
  cjk_latin_space: true              # 中英文之间加空格（支持ChatGPT → 支持 ChatGPT）
  cjk_digit_space: true              # 中文与数字之间加空格（花100元 → 花 100 元）
  number_unit_space: true            # 数字与单位之间加空格（20Gbps → 20 Gbps）
  fullwidth_punct_no_space: true     # 全角标点旁不加空格（iPhone ，好用 → iPhone，好用）
  en_no_space_before_punct: true     # 标点前不加空格（Hello ,world → Hello,world）
  en_space_after_punct: true         # 标点后加空格（Hello,world → Hello, world）
  en_collapse_spaces: true           # 合并连续空格（Hello   world → Hello world）
```

要点：

- `source` 指向你的源 Markdown；`outputs` 每项是一个「格式 × 语言」组合，`html` 可指定 `style` 并可同时导出 `pdf`。
- 翻译走**译文缓存**：已译内容直接复用，不重复调翻译、不重复出文件；缺失片段才回退。
- `typography` 排版规范仅作用于**生成的输出**（md / html / pdf），**绝不修改你的源文件**；代码块、行内代码与网址链接不受影响。GUI 用「📐 文档标准配置」按钮调整（内存生效）；命令行直接编辑此段配置。

### 常见问题

- **「打开」选完后输入框清空**：预期行为——选文件即加载，无需手填路径。
- **翻译后为什么还有 HTML？** 翻译换文字（语言），HTML 是格式（渲染），两者是独立的输出，可分别勾选。
- **Qt GUI 的同步日志在哪？** 在「同步日志」面板中。
- **GUI 里改了「文档标准配置」要重启吗？** 不需要——保存后若正在监听会立即用新规则重跑；「规范化源文档」也始终用当前配置。

---

## 开发者指南

面向想为其扩展、打包、或把 md-sync 集成进自己工程的开发者。

### 目录结构

```
md-sync/
├── md_sync/                # 核心包
│   ├── cli.py              # 命令行入口（md-sync sync / start / ipc …）
│   ├── config.py           # ProjectConfig 解析（md-sync.yaml）
│   ├── watcher.py          # 文件监听（watchdog + debounce）
│   ├── core/               # Document 数据模型 + pipeline 同步主流程编排
│   ├── renderers/          # md / html 渲染器
│   ├── translate/          # 翻译管理（mapping 缓存 + AI 回退）
│   ├── exporters/          # PDF 导出（Chromium）/ pandoc 导出（docx/epub）
│   ├── template/           # 模板管理（含资源路径解析）
│   ├── web/                # Web 看板（FastAPI 后端 + static/index.html 前端）
│   └── plugin/             # 插件引擎（接口 / 注册表 / 加载器 / 钩子），不含插件实例
├── plugins/                # 内置插件（typora / resume / generic-markdown / gongwen），各自携带模板
├── docs/example-plugin/    # 插件开发示例（resume-pack：源模板 + 解析器 + 渲染风格）
├── scripts/build_web.py    # 打包 Web 版（→ dist/md-sync-web）
├── scripts/build_desktop.py # 打包桌面版（→ dist/md-sync-ui）
├── tests/                  # 测试脚本
└── pyproject.toml
```

### 内部机制：同步主流程

```
源文件 .md
   │
   ├─[1 解析]   按 config.schema 选用解析器（插件解析器优先，缺省 MdParser）→ Document 模型
   ├─[2 翻译]   对每个目标语言去 .translations.json 查译文，命中复用，未命中回退 AI
   ├─[3 排版]   typography 规范化（按目标语言启用对应子规则，仅作用于输出文本）
   ├─[4 渲染]   模板系统渲染（Jinja2 + 插件自定义过滤器）
   ├─[5 导出]   md / html 直接写盘；pdf 由 Chromium 打印 html；docx/epub 经 pandoc
   └─ 保存译文缓存
```

关键点：

- **译文缓存**（`.translations.json`）：翻译只写缓存文件，渲染时才取译文——因此翻译**不直接生成输出文件**，后续同步极少再调 AI。
- **资源发现**：打包后模板 / 插件 / Web 静态资源通过 `md_sync.template.manager._find_install_dir()` 从 bundle 内解析，用户机器无需另行安装。
- **docx 导出可扩展**：任何插件可注册自己的 docx 导出器（如 gongwen 从内部数据直接产出红头 docx，完全无须 pandoc）；未提供时自动回退基础 pandoc 路径。

### 制作插件

md-sync 的插件分两类目录：**`md_sync/plugin/`** 是插件**引擎**（接口 / 注册表 / 加载器 / 钩子），
**`plugins/`** 与 `~/.md-sync/plugins/`** 才是插件**实例**（具体模板、解析器）。
你只需按约定放一个目录，无需改引擎代码即可扩展渲染风格、解析器、文档钩子。

完整可运行示例见 **`docs/example-plugin/`**（`resume-pack`：源模板 + 解析器 + 过滤器的四件套教学包）。

#### 插件目录结构

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

#### plugin.yaml 清单字段

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

templates:                  # 本插件提供的渲染风格名（须与 templates/<name>/ 目录一一对应）
  - example-style

hooks:                      # 监听的流水线钩子
  - after_parse
  - after_render

dependencies: []
```

> ⚠️ **`templates:` 里写的风格名必须真实存在于 `templates/<name>/` 目录**，否则该风格不会出现在 `md-sync template list` 中（也不会报错，只是不生效）。

#### 三种常见插件类型

| 类型 | `type` | 用途 | 关键文件 |
|------|--------|------|----------|
| 渲染风格 | `render` | 只提供新 HTML/CSS 样式 | `templates/<style>/` |
| 自定义解析器 | `parser` | 让 md-sync 读懂特殊格式源稿 | `parser.py` |
| 完整插件包 | `pack` | 源模板 + 解析器 + 风格一体 | 上述全部 |

#### 写解析器（pack/parser 类型）

继承 `md_sync.plugin.interface.ParserPlugin`，实现 `_parse` 把源文本转成 `Document` 模型：

```python
from md_sync.plugin.interface import ParserPlugin, PluginManifest, PLUGIN_TYPE_PACK
from md_sync.core.document import Document

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

`detect()` 命中后，配置里写 `schema: my-resume` 即可指定使用它。

#### 写渲染风格插件（render/pack 类型）

在 `templates/<style-name>/` 下放 Jinja2 模板与 CSS。`Document` 模型的字段（`doc.name`、`doc.sections`、`doc.items` 等）即为模板可用变量。需要自定义过滤器时，在 `filters.py` 暴露一个 `filters` 字典，`DirectoryPlugin` 自动发现并注册：

```python
def highlight_metric(text: str) -> str:
    return text  # 实际实现略

filters = {"highlight_metric": highlight_metric}
```

模板里即可使用 `{{ value | highlight_metric }}`。

#### 安装与调试

```bash
# 从本地目录安装（复制到 ~/.md-sync/plugins/<name>/）
md-sync plugin install ./docs/example-plugin/resume-pack/

md-sync plugin list                              # 查看已安装插件与声明的风格
md-sync plugin show resume-pack                  # 查看某个插件的清单详情
md-sync plugin template resume-pack -o my-resume.md   # 从插件生成源模板，照着写稿
md-sync plugin remove resume-pack                # 卸载
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

### 打包

#### 1. Python 包（wheel）

```bash
pip install build
python -m build
# 输出在 dist/md_sync-*.whl，可 pip install 分发
```

CLI 入口：`md-sync`（见 `pyproject.toml` 的 `[project.scripts]`）。

#### 2. 单文件可执行程序（推荐，免 Python）

Web 与桌面两个版本共用同一套 Dioxus 前端，一条命令全部打包：

```bash
pip install pyinstaller            # 或 pip install -e ".[build]"

python scripts/build_all.py        # 一键 → dist/md-sync-web + dist/md-sync-ui
python scripts/build_all.py --web       # 只打 Web 版
python scripts/build_all.py --desktop   # 只打桌面版
python scripts/build_all.py --force     # 强制重新打包后端（跳过指纹缓存）
```

**输出位置（均在本项目 `dist/` 目录下）：**

| 版本 | 输出路径 | 说明 |
|------|----------|------|
| Web   | `dist/md-sync-web`    | 独立 web 服务器，`./dist/md-sync-web` 运行 → 浏览器打开 http://127.0.0.1:8580 |
| 桌面   | `dist/md-sync-ui`     | Dioxus 原生窗口，直接运行，内嵌后端自动启动（Unix socket，无网络端口） |

> 后端指纹缓存（`dist/.backend-*.fp`）自动感知源码 / 依赖 / 环境变化：只有后端输入变了才重新打
> （如新装 `python-docx` 后会自动重打）。`dist/`、`build/`、`*.spec` 均已写入 `.gitignore`。
> `dist/md-sync` 是桌面版内嵌用的后端中间物，由脚本自动处理，无需手动管理。

- 打包脚本自动把 `md_sync/templates`、`md_sync/plugins`、`md_sync/web/static` 等资源打进 bundle，
  运行时通过 `md_sync.template.manager._find_install_dir()` 解析，**无需用户机器安装任何东西**。
- **构建 Python 版本建议 3.12**（PyInstaller 官方稳定支持）。在 3.14 上需排除 `mypy`
  （脚本已默认 `--exclude-module mypy`）。

#### 3. 跨平台自动构建（GitHub Actions）

`.github/workflows/build.yml` 在 `ubuntu-latest` / `windows-latest` / `macos-latest`
三个 runner 上分别运行 `python scripts/build_web.py`，将三个平台的 Web 版可执行文件作为 Release artifact 上传。

```bash
# 打 tag 触发自动构建并发布 Release
git tag v1.0.0 && git push origin v1.0.0
# 到 GitHub Releases 页面下载：md-sync-web-linux / md-sync-web-windows.exe / md-sync-web-macos
```

若只想临时取三平台二进制（不发 Release），在 Actions 对应 run 的 Artifacts 里下载即可，
artifact 命名为 `md-sync-web-ubuntu-latest` / `md-sync-web-windows-latest` / `md-sync-web-macos-latest`。

---

## 许可证

MIT。