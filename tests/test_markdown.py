"""markdown 渲染器测试（纯函数，无 GUI）。"""

from gisdo.gui.markdown import render_markdown


def test_plain_text_wrapped_in_paragraph():
    assert render_markdown("你好") == "<p>你好</p>"


def test_bold_and_italic():
    out = render_markdown("**粗体** 和 *斜体*")
    assert "<b>粗体</b>" in out
    assert "<i>斜体</i>" in out


def test_inline_code():
    out = render_markdown("用 `analysis.Clip` 处理")
    assert "<code>analysis.Clip</code>" in out


def test_heading_levels():
    assert "<h4>标题</h4>" in render_markdown("## 标题")
    assert "<h4>标题</h4>" in render_markdown("### 标题")
    assert "<h4>标题</h4>" in render_markdown("# 标题")


def test_unordered_list():
    out = render_markdown("- 甲\n- 乙")
    assert "<ul>" in out and "<li>甲</li>" in out and "<li>乙</li>" in out


def test_ordered_list():
    out = render_markdown("1. 第一步\n2. 第二步")
    assert "<ol>" in out and "<li>第一步</li>" in out


def test_code_fence():
    src = "```\nimport arcpy\nprint(1)\n```"
    out = render_markdown(src)
    assert "<pre>" in out
    assert "import arcpy" in out
    assert "<p>" not in out.replace("<pre>", "").replace("</pre>", "")


def test_blockquote():
    out = render_markdown("> 注意：断裂源会被拒绝")
    assert "<blockquote>" in out
    assert "断裂源会被拒绝" in out


def test_horizontal_rule():
    assert "<hr/>" in render_markdown("---")


def test_link_safe_url():
    out = render_markdown("[文档](https://example.com/a?x=1&y=2)")
    assert 'href="https://example.com/a?x=1&amp;y=2"' in out
    assert 'target="_blank"' in out


def test_link_javascript_ignored():
    out = render_markdown("[x](javascript:alert(1))")
    assert "<a" not in out
    assert "javascript" in out  # 按纯文本保留


def test_xss_escaped():
    out = render_markdown("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_xss_attr_escaped():
    out = render_markdown('[x](https://a.com/"onclick="x)')
    assert " onclick=" not in out  # 引号已转义，无法注入新属性


def test_paragraph_split_on_blank_line():
    out = render_markdown("第一段\n\n第二段")
    assert out.count("<p>") == 2


def test_newline_inside_paragraph_becomes_br():
    out = render_markdown("第一行\n第二行")
    assert "<br/>" in out


def test_empty_text():
    assert render_markdown("") == ""
    assert render_markdown("   \n\n  ") == ""


def test_qtextedit_renders_fragment_offscreen():
    """离屏验证：只读 QTextEdit 上 insertHtml 渲染渲染器输出（与 chat.py 同路径）。"""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest = __import__("pytest")
    pytest.importorskip("PySide6")
    from PySide6 import QtGui, QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    assert app is not None  # 持引用，防 QApplication 被 GC
    view = QtWidgets.QTextEdit()
    view.setReadOnly(True)
    view.append("<p>你</p>")
    frag = "<p><b>🤖</b></p>" + render_markdown("**结论**：完成\n\n- 步骤一\n- 步骤二")
    cursor = view.textCursor()
    cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
    cursor.insertHtml(frag)
    view.setTextCursor(cursor)
    text = view.toPlainText()
    assert "步骤一" in text
    assert "结论" in text
    assert "**" not in text  # 星号已被渲染成富文本
    assert "<b>" not in text  # 标签不进纯文本
