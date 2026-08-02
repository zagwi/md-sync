"""gongwen 插件自带的 Jinja2 过滤器。

``gongwen_chrome`` 在 markdown-it 渲染出的通用 HTML 之上补充公文语义类，
使 CSS 能按 GB/T 9704-2012 精确排版：

* 主送机关（标题后第一个以全角冒号结尾的段落）→ ``gongwen-zhusong``，顶格；
* 落款（末尾成文日期段 + 其上一段署名）→ ``gongwen-sign`` / ``gongwen-sign-date``，
  分别右空二字 / 右空四字；
* 版头（标题之前的段落，可选）→ ``gongwen-org``（发文机关标志，红头）与
  ``gongwen-no``（发文字号，下接红色分隔线）。

过滤器同时作用于 raw 布局（源语言整篇渲染）与 structured 布局（翻译输出）。
"""

from __future__ import annotations

import re

_P_BLOCK = re.compile(r"<p\b[^>]*>.*?</p>", re.DOTALL)
_SIGN_DATE_RE = re.compile(r"^(?:\d{4}年\d{1,2}月\d{1,2}日|（这里是成文日期[^）]*）)$")
_OPEN_TAG_RE = re.compile(r"(<p\b)([^>]*)(>)", re.DOTALL)


def _inject_class(open_tag: str, cls: str) -> str:
    """Return the opening tag with *cls* added to its class attribute."""
    m = _OPEN_TAG_RE.match(open_tag)
    if not m:
        return open_tag
    pre, attrs, gt = m.group(1), m.group(2), m.group(3)

    def _has_class(a: str) -> bool:
        return re.search(r"\bclass\s*=", a) is not None

    if _has_class(attrs):
        attrs = re.sub(
            r'class\s*=\s*"([^"]*)"',
            lambda mm: f'class="{mm.group(1).strip()} {cls}"',
            attrs,
            count=1,
        )
        attrs = re.sub(
            r"class\s*=\s*'([^']*)'",
            lambda mm: f"class='{mm.group(1).strip()} {cls}'",
            attrs,
            count=1,
        )
    else:
        attrs += f' class="{cls}"'
    return f"{pre}{attrs}{gt}"


def _tag_block(block: str, cls: str) -> str:
    """Add *cls* to the first opening <p> tag of a paragraph block."""
    return re.sub(_OPEN_TAG_RE, lambda m: _inject_class(m.group(0), cls), block, count=1)


def _text_of(block: str) -> str:
    return re.sub(r"<[^>]+>", "", block).strip()


def gongwen_chrome(html: str) -> str:
    """Annotate rendered 公文 HTML with GB/T 9704-2012 layout classes."""
    s = str(html)
    ps = list(_P_BLOCK.finditer(s))
    if not ps:
        return s

    h1_pos = s.find("<h1")
    edits: list[tuple[int, int, str]] = []

    # ── 落款：末尾成文日期段 + 其上一段署名 ──
    date_idx = next(
        (i for i in range(len(ps) - 1, -1, -1) if _SIGN_DATE_RE.match(_text_of(ps[i].group(0)))),
        None,
    )
    if date_idx is not None:
        d = ps[date_idx]
        edits.append((d.start(), d.end(), _tag_block(d.group(0), "gongwen-sign-date")))
        if date_idx - 1 >= 0:
            sig = ps[date_idx - 1]
            edits.append((sig.start(), sig.end(), _tag_block(sig.group(0), "gongwen-sign")))

    # ── 主送机关：标题后第一个以全角/半角冒号结尾的段落，顶格 ──
    if h1_pos != -1:
        zhusong = next((m for m in ps if m.start() > h1_pos), None)
        if zhusong is not None:
            text = _text_of(zhusong.group(0))
            if text.endswith("：") or text.endswith(":"):
                edits.append(
                    (
                        zhusong.start(),
                        zhusong.end(),
                        _tag_block(zhusong.group(0), "gongwen-zhusong"),
                    )
                )

        # ── 版头（可选）：标题之前的段落 → 红头 + 发文字号 ──
        head_ps = [m for m in ps if m.end() < h1_pos]
        if head_ps:
            first = head_ps[0]
            edits.append((first.start(), first.end(), _tag_block(first.group(0), "gongwen-org")))
            if len(head_ps) > 1:
                last = head_ps[-1]
                edits.append((last.start(), last.end(), _tag_block(last.group(0), "gongwen-no")))

    # 从后往前替换，避免索引偏移。
    for start, end, new in sorted(edits, key=lambda e: e[0], reverse=True):
        s = s[:start] + new + s[end:]
    return s


filters = {"gongwen_chrome": gongwen_chrome}
