"""可复用小部件：页头、提示条、日志控制台、JSON 树查看器、PNG 预览。"""

from __future__ import annotations

from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from gisdo.gui import theme
from gisdo.gui.icons import get_icon


class PageHeader(QtWidgets.QWidget):
    """统一页头：大标题 + 灰色说明行，右侧可挂操作控件。"""

    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 4)
        text_col = QtWidgets.QVBoxLayout()
        text_col.setSpacing(2)
        title_label = QtWidgets.QLabel(title)
        title_label.setObjectName("pageTitle")
        text_col.addWidget(title_label)
        self.subtitle_label = QtWidgets.QLabel(subtitle)
        self.subtitle_label.setObjectName("pageSubtitle")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setVisible(bool(subtitle))
        text_col.addWidget(self.subtitle_label)
        layout.addLayout(text_col, 1)
        self.extra = QtWidgets.QHBoxLayout()
        self.extra.setSpacing(8)
        layout.addLayout(self.extra)

    def add_widget(self, widget: QtWidgets.QWidget) -> None:
        self.extra.addWidget(widget)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)
        self.subtitle_label.setVisible(bool(text))


class Banner(QtWidgets.QFrame):
    """内联提示条：kind = "info"（蓝）| "warning"（黄）。"""

    def __init__(self, text: str = "", kind: str = "info", parent=None):
        super().__init__(parent)
        self.setObjectName("bannerWarn" if kind == "warning" else "bannerInfo")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        color = theme.WARNING if kind == "warning" else theme.ACCENT
        icon_label = QtWidgets.QLabel()
        icon_label.setPixmap(get_icon("warning", color).pixmap(16, 16))
        icon_label.setFixedSize(16, 16)
        layout.addWidget(icon_label)
        self.label = QtWidgets.QLabel(text)
        self.label.setWordWrap(True)
        layout.addWidget(self.label, 1)
        self.setVisible(bool(text))

    def set_text(self, text: str) -> None:
        self.label.setText(text)
        self.setVisible(bool(text))


class LogConsole(QtWidgets.QPlainTextEdit):
    """只读、等宽、深色底的日志控制台。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("logConsole")
        self.setReadOnly(True)
        self.setMaximumBlockCount(10000)
        font = QtGui.QFont("Consolas")
        font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        font.setPointSizeF(9.0)
        self.setFont(font)
        self._auto_scroll = True

    def set_auto_scroll(self, enabled: bool) -> None:
        self._auto_scroll = enabled

    def append_log(self, text: str) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        cursor.insertText(text + "\n")
        if self._auto_scroll:
            self.setTextCursor(cursor)
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


class JsonTreeView(QtWidgets.QTreeWidget):
    """把任意 JSON 渲染成可折叠树。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["键", "值"])
        self.setAlternatingRowColors(True)
        self.setColumnWidth(0, 280)

    def show_json(self, data: Any, label: str = "") -> None:
        self.clear()
        root = QtWidgets.QTreeWidgetItem(self, [label or "root", ""])
        self._populate(root, data)
        if label:
            root.setExpanded(True)
        self.expandToDepth(0)

    def _populate(self, parent_item, data: Any) -> None:
        if isinstance(data, dict):
            for key in sorted(data.keys(), key=str):
                value = data[key]
                if isinstance(value, (dict, list)) and value:
                    child = QtWidgets.QTreeWidgetItem(parent_item, [str(key), ""])
                    self._populate(child, value)
                else:
                    QtWidgets.QTreeWidgetItem(parent_item, [str(key), self._format(value)])
        elif isinstance(data, list):
            parent_item.setText(1, f"[{len(data)} 项]")
            for index, value in enumerate(data):
                if isinstance(value, (dict, list)) and value:
                    child = QtWidgets.QTreeWidgetItem(parent_item, [str(index), ""])
                    self._populate(child, value)
                else:
                    QtWidgets.QTreeWidgetItem(parent_item, [str(index), self._format(value)])
        else:
            parent_item.setText(1, self._format(data))

    @staticmethod
    def _format(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, float):
            return f"{value:g}"
        return str(value)

    def show_raw_text(self, text: str) -> None:
        """把原始文本作为单个节点展示。"""
        self.clear()
        QtWidgets.QTreeWidgetItem(self, ["output", text])


class PngPreview(QtWidgets.QScrollArea):
    """PNG 预览，自动缩放以适应。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._label = QtWidgets.QLabel("（无预览）")
        self._label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet(f"color: {theme.TEXT_DIM};")
        self.setWidget(self._label)

    def set_png(self, path: str) -> None:
        pixmap = QtGui.QPixmap(path)
        if pixmap.isNull():
            self._label.setText(f"无法加载图片：{path}")
            return
        self._label.setStyleSheet("")
        self._label.setPixmap(pixmap)
        self._label.setText("")


__all__ = ["Banner", "JsonTreeView", "LogConsole", "PageHeader", "PngPreview"]
