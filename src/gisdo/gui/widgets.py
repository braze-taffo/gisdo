"""可复用小部件：日志控制台、JSON 树查看器、PNG 预览。"""

from __future__ import annotations

from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets


class LogConsole(QtWidgets.QPlainTextEdit):
    """只读、等宽、带时间戳的日志控制台。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(10000)
        font = QtGui.QFont("Consolas")
        font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        self.setFont(font)
        self._orig = ""

    def append_log(self, text: str) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        cursor.insertText(text + "\n")
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
        self.setWidget(self._label)

    def set_png(self, path: str) -> None:
        pixmap = QtGui.QPixmap(path)
        if pixmap.isNull():
            self._label.setText(f"无法加载图片：{path}")
            return
        self._label.setPixmap(pixmap)
        self._label.setText("")
