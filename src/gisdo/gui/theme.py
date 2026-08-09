"""亮色主题：色板常量 + 全局 QSS。

``apply_theme(app)`` 在 ``main()`` 里 QApplication 创建后调用一次。
按钮变体通过动态 property 区分：``kind="primary" | "danger" | "ghost"``，
默认按钮为白底描边样式。
"""

from __future__ import annotations

from PySide6 import QtGui, QtWidgets

# --- 色板 ---
BG = "#F4F6F8"            # 窗口背景
CARD = "#FFFFFF"          # 卡片/输入控件背景
BORDER = "#DFE3E8"        # 常规边框
BORDER_STRONG = "#C9D0D8"  # 悬停/强边框
TEXT = "#24292F"          # 正文
TEXT_DIM = "#6E7781"      # 次要文字
ACCENT = "#1C6FD1"        # 强调蓝
ACCENT_HOVER = "#1A63BB"
ACCENT_PRESSED = "#1755A3"
ACCENT_SOFT = "#E8F1FC"   # 浅蓝底（选中/气泡）
SUCCESS = "#1E8E3E"
WARNING = "#B26A00"
WARNING_BG = "#FFF4E0"
DANGER = "#C5221F"
DANGER_HOVER = "#B01D1B"
DANGER_BG = "#FDECEA"
LOG_BG = "#1F2430"        # 日志控制台深色底
LOG_TEXT = "#D6DAE2"

_QSS = f"""
/* ---------- 全局 ---------- */
QWidget {{
    background: transparent;
    color: {TEXT};
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}}
QMainWindow, QDialog, QMessageBox {{
    background: {BG};
}}
QScrollArea {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
}}
QToolTip {{
    background: {TEXT};
    color: {CARD};
    border: none;
    padding: 5px 8px;
}}

/* ---------- 卡片容器 ---------- */
QGroupBox {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 14px;
    padding: 12px 12px 10px 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    top: 2px;
    padding: 0 4px;
    color: {TEXT};
    background: transparent;
}}
QFrame#card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 6px 14px;
    min-height: 18px;
}}
QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton:pressed {{ background: {ACCENT_SOFT}; }}
QPushButton:disabled {{
    color: {TEXT_DIM};
    border-color: {BORDER};
    background: {BG};
}}
QPushButton[kind="primary"] {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: {CARD};
    font-weight: 600;
}}
QPushButton[kind="primary"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton[kind="primary"]:pressed {{ background: {ACCENT_PRESSED}; border-color: {ACCENT_PRESSED}; }}
QPushButton[kind="primary"]:disabled {{ background: {BORDER_STRONG}; border-color: {BORDER_STRONG}; color: {CARD}; }}
QPushButton[kind="danger"] {{
    background: {CARD};
    border: 1px solid {DANGER};
    color: {DANGER};
}}
QPushButton[kind="danger"]:hover {{ background: {DANGER_BG}; }}
QPushButton[kind="danger"]:disabled {{ color: {TEXT_DIM}; border-color: {BORDER}; background: {BG}; }}
QPushButton[kind="ghost"] {{
    background: transparent;
    border: none;
    color: {ACCENT};
}}
QPushButton[kind="ghost"]:hover {{ background: {ACCENT_SOFT}; }}
QPushButton[kind="ghost"]:pressed {{ background: #D8E7FA; }}

/* ---------- 输入控件 ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {ACCENT};
    selection-color: {CARD};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled,
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: {BG};
    color: {TEXT_DIM};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_DIM};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
    outline: none;
}}

/* ---------- 列表 / 树 ---------- */
QListWidget, QTreeWidget {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
    outline: none;
    alternate-background-color: {BG};
}}
QListWidget::item, QTreeWidget::item {{
    padding: 4px 6px;
    border-radius: 4px;
}}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background: {ACCENT_SOFT};
    color: {TEXT};
}}
QListWidget::item:hover, QTreeWidget::item:hover {{
    background: {BG};
}}
QHeaderView::section {{
    background: {BG};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 5px 8px;
    color: {TEXT_DIM};
    font-weight: 600;
}}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_STRONG};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {TEXT_DIM}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER_STRONG};
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background: {TEXT_DIM}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ---------- 复选 / 单选 ---------- */
QCheckBox, QRadioButton {{ spacing: 6px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {BORDER_STRONG};
    background: {CARD};
}}
QCheckBox::indicator {{ border-radius: 3px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
    image: none;
}}
QCheckBox::indicator:checked {{
    /* 简单对勾：两个白矩形近似 */
    border: 1px solid {ACCENT};
}}
QRadioButton::indicator:checked {{
    border: 4px solid {ACCENT};
    background: {CARD};
}}

/* ---------- 菜单 / 状态栏 ---------- */
QMenuBar {{
    background: {CARD};
    border-bottom: 1px solid {BORDER};
}}
QMenuBar::item:selected {{ background: {ACCENT_SOFT}; border-radius: 4px; }}
QMenu {{
    background: {CARD};
    border: 1px solid {BORDER};
}}
QMenu::item:selected {{ background: {ACCENT_SOFT}; }}
QStatusBar {{
    background: {CARD};
    border-top: 1px solid {BORDER};
}}
QStatusBar::item {{ border: none; }}

/* ---------- Dock ---------- */
QDockWidget {{
    color: {TEXT_DIM};
    font-weight: 600;
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}}
QDockWidget::title {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 6px 10px;
    text-align: left;
}}
QDockWidget > QWidget {{
    border: 1px solid {BORDER};
    border-top: none;
}}

/* ---------- 主窗口专用 ---------- */
QWidget#sidebar {{
    background: {CARD};
    border-right: 1px solid {BORDER};
}}
QLabel#appTitle {{
    font-size: 18px;
    font-weight: 700;
    color: {TEXT};
}}
QLabel#appVersion {{
    font-size: 11px;
    color: {TEXT_DIM};
}}
QToolButton#navBtn {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 8px 10px;
    text-align: left;
    color: {TEXT};
    font-size: 13px;
}}
QToolButton#navBtn:hover {{ background: {BG}; }}
QToolButton#navBtn:checked {{
    background: {ACCENT_SOFT};
    color: {ACCENT};
    font-weight: 600;
}}
QLabel#pageTitle {{
    font-size: 17px;
    font-weight: 700;
    color: {TEXT};
}}
QLabel#pageSubtitle {{
    color: {TEXT_DIM};
}}
QFrame#bannerInfo {{
    background: {ACCENT_SOFT};
    border: 1px solid #B9D4F5;
    border-radius: 6px;
}}
QFrame#bannerWarn {{
    background: {WARNING_BG};
    border: 1px solid #EDD3A7;
    border-radius: 6px;
}}
QLabel#chip {{
    background: {ACCENT_SOFT};
    color: {ACCENT};
    border-radius: 9px;
    padding: 2px 10px;
    font-size: 12px;
}}
QPlainTextEdit#logConsole {{
    background: {LOG_BG};
    color: {LOG_TEXT};
    border: none;
    border-bottom-left-radius: 6px;
    border-bottom-right-radius: 6px;
    selection-background-color: #3D4B66;
}}
QTextEdit#chatView {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px;
}}
"""


def apply_theme(app: QtWidgets.QApplication) -> None:
    """应用 Fusion 风格 + 全局 QSS + 默认字体。"""
    app.setStyle("Fusion")
    font = QtGui.QFont("Microsoft YaHei UI")
    font.setStyleHint(QtGui.QFont.StyleHint.SansSerif)
    app.setFont(font)
    app.setStyleSheet(_QSS)


__all__ = [
    "ACCENT",
    "ACCENT_HOVER",
    "ACCENT_PRESSED",
    "ACCENT_SOFT",
    "BG",
    "BORDER",
    "BORDER_STRONG",
    "CARD",
    "DANGER",
    "DANGER_BG",
    "LOG_BG",
    "LOG_TEXT",
    "SUCCESS",
    "TEXT",
    "TEXT_DIM",
    "WARNING",
    "WARNING_BG",
    "apply_theme",
]
