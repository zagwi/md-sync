# Typora 主题风格 Markdown 文档模板

选择 `typora` 插件后，你的 Markdown 文档将使用 **Typora CSS 主题** 渲染输出。

## 快速开始

1. **选择 Typora 插件** — 在「插件管理」卡片中选择 `typora`
2. **选择 Typora 主题** — 在「渲染风格」下拉框中选择你已安装的 Typora 主题（如 github、claude-like、newsprint 等）
3. **编写文档** — 使用标准 Markdown 语法
4. **配置输出** — 选择 HTML / Markdown / PDF 格式
5. **启动同步** — 实时预览 Typora 风格的文档

## 文档结构

```markdown
# 文档标题

## 第一章

- 要点一
- 要点二
- 要点三

普通段落文字。

## 第二章

### 子章节

段落内容。

- 项目
- 列表

## 第三章

更多内容...
```

## 可用的 Typora 主题

md-sync 会自动发现**本机已安装 Typora** 的主题目录下的 `.css` 文件并加入「渲染风格」下拉框。不同系统的主题目录：

- **Windows**：`%APPDATA%\Typora\themes`
- **macOS**：`~/Library/Application Support/abnerworks.Typora/themes`
- **Linux**：`~/.config/Typora/themes`

> 未检测到上述目录（即本机未安装 Typora）时，不会有任何 Typora 主题出现在下拉框中。

安装新主题：将主题 `.css` 文件放入对应系统的 Typora 主题目录，重启 md-sync 即可使用。
