# md-sync

把一份 Markdown 源文件，**自动同步**成多种格式（HTML / PDF 等等）和多种语言（中文 / 英文）的输出，
并通过文件监听（watcher）在源文件变动时自动重新生成。适合文档工作者的效率提升神器。

- 一份源 → 多份产物：`html/zh`、`html/en`、`pdf/en`、`pdf/en`、`md/zh`、`md/en`、 …
- 源文件改了 → 自动重新同步（带防抖 / debounce）
- 翻译走 **缓存优先**：已有译文直接复用，缺失才回退到 AI
- 自带 Web 风格UI

---

## 为什么需要 md-sync？

AI 工具确实能翻译文档、也能转换格式——但那解决的是"一次性"需求。
真正繁琐的是**频繁修改源文件**的场景：你每改一个词，就要手动走完
**修改 → 翻译 → 转换格式** 这一整条链路，而且往往要同时维护多个语言、多种格式的产物。
人工反复操作，不仅费时，更容易漏翻、漏转、版本对不齐。

**md-sync 就是为这个场景而生：你只管改源文件，其余交给工具。**

- **所改即所得**：基于文件监听，源文件一保存，所有语言 / 格式的产物在秒级内自动重新生成。
- **一次配置，长期受益**：翻译缓存 + 多输出配置只需设定一次，后续每次修改零额外操作。
- **不易出错**：译文与格式由工具统一处理，避免人工复制粘贴导致的漏翻、漏转、错版。

把"重复的体力活"交给 md-sync，你专注内容本身。

---

## 功能概览

| 能力 | 说明 |
|------|------|
| **多格式输出** | `html` `PDF` `md` （支持插件扩展docx等格式）|
| **多语言输出** | `zh` / `en`，翻译基于 `.translations.json` 缓存，缺失回退 AI（provider `auto`） |
| **文件监听** | 基于 `watchdog` 监听源文件，`debounce` 默认 1.5s，改动即同步 |
| **模板 / 主题** | `bwx`、`modern` 等样式，支持插件扩展（`md-sync template` / `md-sync plugin`） |
| **翻译缓存** | `strategy: mapping` + `mapping_file`，译文只更新缓存字典、不直接出文件，渲染时再取用 |
| **Web 仪表盘** | 浏览器里配置源文件、查看解析信息、启动/停止监听、手动同步、查看同步事件与历史项目 |
| **桌面 GUI（Electron）** | 复用同一套 Web 仪表盘与 FastAPI 后端，零 UI 重写 |

### 两个独立维度（重要）

同步行为由两个**互不相干**的维度组合：

- **语言 `lang`**：`zh` ↔ `en` —— 由翻译模块处理（只换文字）
- **格式 `format`**：`md` ↔ `html` —— 由渲染器处理（只换呈现形式，套用模板/主题）

例：一份简历源文件，可同时输出 `html/zh`（同语言 + 转 HTML 带模板）、
`md/en`（翻译 + 不转换）、`html/en`（翻译 + 转 HTML + PDF）等组合。

---

## 目录结构

```
md_sync/
├── md_sync/
│   ├── cli.py              # 命令行入口（md-sync start / sync / status …）
│   ├── config.py           # ProjectConfig 解析（md-sync.yaml）
│   ├── watcher.py          # 文件监听（watchdog + debounce）
│   ├── core/pipeline.py    # 同步主流程编排
│   ├── renderers/          # md / html 渲染器
│   ├── translate/          # 翻译管理 + AI 回退
│   ├── exporters/          # PDF 导出（Chromium）
│   ├── template/           # 模板管理
│   ├── plugin/             # 插件系统
│   └── web/app.py          # FastAPI 后端 + 仪表盘
├── electron/               # 桌面壳（Electron + main.js）
│   ├── main.js
│   ├── package.json
│   └── start.sh
├── projects/              # 示例 / 项目配置（md-sync.yaml）
├── templates/ themes/     # 内置模板与主题
├── start_server.py         # 直接以 Web 模式启动（加载 projects/resume 配置）
└── pyproject.toml
```

---

## 安装

要求 **Python ≥ 3.10**，并装有 `pip`。

```bash
cd md_sync
pip install -e .
```

依赖：`pyyaml`、`jinja2`、`watchdog`、`fastapi`、`uvicorn[standard]`、`httpx`。

---

## 使用方式

### 方式 A：桌面 GUI（推荐，Electron）

Web 仪表盘原样复用，Electron 只负责拉起 Python 后端并打开窗口。
后端进程由 Electron 自动管理，关闭窗口即退出。

```bash
cd md_sync/electron
npm install        # 仅首次，安装 electron（若未全局可用）
electron .         # 或：bash start.sh
```

> 如果系统已自带 `/usr/bin/electron` 时，直接 `electron .` 即可，无需 `npm install`。
> 若 8580 端口已有后端在跑，Electron 会直接复用，不会重复启动。

### 方式 B：Web 仪表盘（浏览器访问）

```bash
# 1) 直接以示例项目启动（加载 projects/resume/md-sync.yaml）
python start_server.py
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

仓库自带跨平台构建脚本 `build_app.py`，在当前系统上产出免 Python 环境的可执行文件：

```bash
pip install pyinstaller            # 或 pip install -e ".[build]"
python build_app.py                # 产出 dist/md-sync (Linux/macOS) 或 dist/md-sync.exe (Windows)
python build_app.py --clean        # 清缓存后重新构建
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
三个 runner 上分别运行 `build_app.py`，将三个平台的可执行文件作为 Release artifact 上传。

```bash
# 打 tag 触发自动构建并发布 Release
git tag v1.0.0 && git push origin v1.0.0
# 到 GitHub Releases 页面下载：md-sync-linux / md-sync-windows.exe / md-sync-macos
```

若只想临时取三平台二进制（不发 Release），在 Actions 对应 run 的 Artifacts 里下载即可，
artifact 命名为 `md-sync-ubuntu-latest` / `md-sync-windows-latest` / `md-sync-macos-latest`。

### 4. 桌面应用（Electron，可选）

把上述可执行文件交给 Electron 壳调用（替代 `spawn('python', ...)`）：

```bash
# 先产出后端可执行文件
python build_app.py
# 修改 electron/main.js 中 startBackend() 的 spawn 命令为 ./dist/md-sync
cd electron && npm install && npx electron-packager . md-sync-desktop
```

> 当前 `electron/main.js` 直接调用系统 `python`，适合开发/本机使用；
> 若要分发免 Python 环境的安装包，按上述步骤把后端打包进 Electron 即可。

---

## 常见问题

- **「打开」选完文件后输入框清空**：这是预期行为——选文件即加载，无需手填路径。
- **翻译后为什么还有 HTML？** 翻译只换文字，HTML 由渲染器（转换）生成；两者独立，详见「两个独立维度」。
- **后端日志在哪？** Web 模式下打到启动终端；Electron 模式下在 Electron 启动的那个终端。
- **端口被占用？** 8580 已被占用时，Electron 会直接复用现有后端，不会重复拉起。

---

## 许可证

MIT。
