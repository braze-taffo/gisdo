"""把 Agent 的 Markdown 回复渲染为安全 HTML 片段（供 QTextEdit 显示）。

先 ``html.escape`` 再套用 Markdown 子集，杜绝 XSS；只支持常见子集：
标题 / 无序有序列表 / 引用 / 代码块 / 行内代码 / 粗体 / 斜体 / 链接 / 分隔线 / 段落。
不支持的语法按纯文本显示，不会被吞掉。
"""

from __future__ import annotations

import html
import re

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_HR_RE = re.compile(r"^\s*([-*_])\s*(?:\1\s*){2,}\s*$")
_QUOTE_RE = re.compile(r"^&gt;\s?(.*)$")  # escape 后 ">" 变 "&gt;"
_UL_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def _render_inline(text: str) -> str:
    text = _INLINE_CODE_RE.sub(r"<code>\1</code>", text)
    text = _LINK_RE.sub(
        lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener noreferrer">{m.group(1)}</a>',
        text,
    )
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)
    return text


def render_markdown(text: str) -> str:
    """把 Markdown 文本渲染为安全 HTML 片段（多个块级元素）。"""
    lines = html.escape(text).split("\n")
    parts: list[str] = []
    i, n = 0, len(lines)

    def is_block_start(line: str) -> bool:
        return bool(_FENCE_RE.match(line) or _HEADING_RE.match(line) or _HR_RE.match(line)
                    or _QUOTE_RE.match(line) or _UL_RE.match(line) or _OL_RE.match(line))

    while i < n:
        line = lines[i]

        # 代码块
        if _FENCE_RE.match(line):
            i += 1
            buf = []
            while i < n and not _FENCE_RE.match(lines[i]):
                buf.append(lines[i])
                i += 1
            i += 1  # 跳过结束围栏
            parts.append("<pre>" + "\n".join(buf) + "</pre>")
            continue

        # 标题（最多四级）
        m = _HEADING_RE.match(line)
        if m and len(m.group(1)) <= 4:
            parts.append(f"<h4>{_render_inline(m.group(2).strip())}</h4>")
            i += 1
            continue

        # 分隔线
        if _HR_RE.match(line):
            parts.append("<hr/>")
            i += 1
            continue

        # 引用块（连续行合并）
        if _QUOTE_RE.match(line):
            buf = []
            while i < n:
                mq = _QUOTE_RE.match(lines[i])
                if mq:
                    buf.append(_render_inline(mq.group(1)))
                    i += 1
                    continue
                if lines[i].strip():
                    break
                i += 1  # 引用内的空行跳过，仍算同一引用块
            parts.append("<blockquote>" + "<br/>".join(buf) + "</blockquote>")
            continue

        # 无序列表（含续行）
        if _UL_RE.match(line):
            buf = []
            while i < n:
                mu = _UL_RE.match(lines[i])
                if mu:
                    buf.append("<li>" + _render_inline(mu.group(1).strip()) + "</li>")
                    i += 1
                    continue
                if lines[i].strip() == "" or is_block_start(lines[i]):
                    break
                buf[-1] = buf[-1][:-4] + "<br/>" + _render_inline(lines[i].strip()) + "</li>"
                i += 1
            parts.append("<ul>" + "".join(buf) + "</ul>")
            continue

        # 有序列表
        if _OL_RE.match(line):
            buf = []
            while i < n:
                mo = _OL_RE.match(lines[i])
                if mo:
                    buf.append("<li>" + _render_inline(mo.group(2).strip()) + "</li>")
                    i += 1
                    continue
                if lines[i].strip() == "" or is_block_start(lines[i]):
                    break
                buf[-1] = buf[-1][:-4] + "<br/>" + _render_inline(lines[i].strip()) + "</li>"
                i += 1
            parts.append("<ol>" + "".join(buf) + "</ol>")
            continue

        # 段落（收集到空行或块开始）
        buf = []
        while i < n and lines[i].strip() and not is_block_start(lines[i]):
            buf.append(_render_inline(lines[i].strip()))
            i += 1
        if buf:
            parts.append("<p>" + "<br/>".join(buf) + "</p>")

        # 跳过空行
        while i < n and lines[i].strip() == "":
            i += 1

    return "\n".join(parts)


__all__ = ["render_markdown"]
