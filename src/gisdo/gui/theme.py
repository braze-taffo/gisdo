"""GISdo 的全局视觉主题。

主题保持明亮、专业的工作区，同时用深色侧边栏稳定导航层级。按钮变体
通过动态 property 区分：``kind="primary" | "danger" | "ghost"``。
"""

from __future__ import annotations

from PySide6 import QtGui, QtWidgets

# --- 基础色板 ---
BG = "#F3F6FA"
SURFACE_ALT = "#F8FAFC"
CARD = "#FFFFFF"
BORDER = "#E2E8F0"
BORDER_STRONG = "#CBD5E1"
TEXT = "#172033"
TEXT_DIM = "#64748B"

# --- 品牌与状态色 ---
ACCENT = "#2563EB"
ACCENT_HOVER = "#1D4ED8"
ACCENT_PRESSED = "#1E40AF"
ACCENT_SOFT = "#EAF2FF"
ACCENT_FAINT = "#F5F8FF"
SUCCESS = "#059669"
SUCCESS_BG = "#ECFDF5"
WARNING = "#D97706"
WARNING_BG = "#FFF7E8"
DANGER = "#DC2626"
DANGER_HOVER = "#B91C1C"
DANGER_BG = "#FEF2F2"

# --- 导航与日志 ---
SIDEBAR = "#111827"
SIDEBAR_ALT = "#1C2536"
SIDEBAR_HOVER = "#1E293B"
SIDEBAR_TEXT = "#F8FAFC"
SIDEBAR_DIM = "#94A3B8"
LOG_BG = "#0B1220"
LOG_TEXT = "#D7E0EE"

_QSS = f"""
/* ---------- 全局 ---------- */
QWidget {{
    background: transparent;
    color: {TEXT};
    font-family: "Microsoft YaHei UI", "Segoe UI Variable", "Segoe UI", sans-serif;
    font-size: 13px;
}}
QMainWindow, QDialog, QMessageBox {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QScrollArea {{ background: transparent; border: none; }}
QToolTip {{
    background: {SIDEBAR};
    color: {SIDEBAR_TEXT};
    border: 1px solid #293548;
    border-radius: 6px;
    padding: 6px 9px;
}}

/* ---------- 卡片容器 ---------- */
QGroupBox {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
    margin-top: 17px;
    padding: 16px 14px 14px 14px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    top: 1px;
    padding: 0 6px;
    color: {TEXT};
    background: {CARD};
}}
QFrame#card, QFrame#onboardingCard {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QFrame#chatToolbar {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 11px;
}}
QFrame#composer {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: 12px;
}}
QFrame#composer:hover {{ border-color: #AFC4E5; }}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 7px 14px;
    min-height: 20px;
    color: {TEXT};
}}
QPushButton:hover {{
    background: {SURFACE_ALT};
    border-color: #94A3B8;
}}
QPushButton:pressed {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton:disabled {{
    color: #94A3B8;
    border-color: {BORDER};
    background: {SURFACE_ALT};
}}
QPushButton[kind="primary"] {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: {CARD};
    font-weight: 600;
}}
QPushButton[kind="primary"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton[kind="primary"]:pressed {{ background: {ACCENT_PRESSED}; border-color: {ACCENT_PRESSED}; }}
QPushButton[kind="primary"]:disabled {{ background: #A8B5C7; border-color: #A8B5C7; color: {CARD}; }}
QPushButton[kind="danger"] {{ background: {CARD}; border-color: #F0B4B4; color: {DANGER}; }}
QPushButton[kind="danger"]:hover {{ background: {DANGER_BG}; border-color: {DANGER}; }}
QPushButton[kind="danger"]:pressed {{ background: #FEE2E2; }}
QPushButton[kind="danger"]:disabled {{ color: #94A3B8; border-color: {BORDER}; background: {SURFACE_ALT}; }}
QPushButton[kind="ghost"] {{ background: transparent; border-color: transparent; color: {ACCENT}; }}
QPushButton[kind="ghost"]:hover {{ background: {ACCENT_SOFT}; border-color: transparent; }}
QPushButton[kind="ghost"]:pressed {{ background: #DCEAFF; }}
QToolButton {{
    background: transparent;
    border: none;
    border-radius: 7px;
    padding: 6px;
}}
QToolButton:hover {{ background: {ACCENT_SOFT}; }}
QToolButton:pressed {{ background: #DCEAFF; }}

/* ---------- 输入控件 ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 7px 10px;
    selection-background-color: {ACCENT};
    selection-color: {CARD};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled,
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: {SURFACE_ALT};
    color: #94A3B8;
    border-color: {BORDER};
}}
QLineEdit[readOnly="true"] {{ background: {SURFACE_ALT}; color: {TEXT_DIM}; }}
QPlainTextEdit#chatInput {{
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 8px 10px;
}}
QComboBox {{ padding-right: 28px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_DIM};
    margin-right: 9px;
}}
QComboBox QAbstractItemView {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 4px;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
    outline: none;
}}

/* ---------- 列表 / 树 ---------- */
QListWidget, QTreeWidget {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 4px;
    outline: none;
    alternate-background-color: {SURFACE_ALT};
}}
QListWidget::item {{ padding: 8px 10px; border-radius: 7px; }}
QTreeWidget::item {{ padding: 6px 8px; border-radius: 6px; }}
QListWidget::item:selected, QTreeWidget::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
QListWidget::item:hover, QTreeWidget::item:hover {{ background: {SURFACE_ALT}; }}
QHeaderView::section {{
    background: {SURFACE_ALT};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 7px 9px;
    color: {TEXT_DIM};
    font-weight: 600;
}}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #C4CDDA; border-radius: 4px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: #94A3B8; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #C4CDDA; border-radius: 4px; min-width: 32px; }}
QScrollBar::handle:horizontal:hover {{ background: #94A3B8; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ---------- 复选 / 单选 ---------- */
QCheckBox, QRadioButton {{ spacing: 7px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {BORDER_STRONG};
    background: {CARD};
}}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QRadioButton::indicator:checked {{ border: 5px solid {ACCENT}; background: {CARD}; }}

/* ---------- 菜单 / 状态栏 ---------- */
QMenuBar {{ background: {CARD}; border-bottom: 1px solid {BORDER}; padding: 2px 6px; }}
QMenuBar::item {{ padding: 5px 9px; border-radius: 5px; }}
QMenuBar::item:selected {{ background: {ACCENT_SOFT}; }}
QMenu {{ background: {CARD}; border: 1px solid {BORDER}; padding: 5px; }}
QMenu::item {{ padding: 7px 24px 7px 10px; border-radius: 5px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 7px; }}
QStatusBar {{ background: {CARD}; border-top: 1px solid {BORDER}; color: {TEXT_DIM}; min-height: 26px; }}
QStatusBar::item {{ border: none; }}
QLabel#statusSummary {{ color: {TEXT_DIM}; font-size: 12px; padding-right: 8px; }}

/* ---------- Dock ---------- */
QDockWidget {{ color: {TEXT_DIM}; font-weight: 600; titlebar-close-icon: none; titlebar-normal-icon: none; }}
QDockWidget::title {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-bottom: none;
    padding: 7px 11px;
    text-align: left;
}}
QWidget#logToolbar {{ background: {CARD}; border-bottom: 1px solid {BORDER}; }}

/* ---------- 主窗口与导航 ---------- */
QWidget#sidebar {{ background: {SIDEBAR}; border-right: 1px solid #1D293B; }}
QLabel#brandMark {{
    background: {ACCENT};
    color: {CARD};
    border-radius: 9px;
    font-size: 19px;
    font-weight: 800;
}}
QLabel#appTitle {{ font-size: 18px; font-weight: 700; color: {SIDEBAR_TEXT}; }}
QLabel#appVersion {{ font-size: 11px; color: {SIDEBAR_DIM}; }}
QLabel#navSection {{ color: #718096; font-size: 10px; font-weight: 700; padding: 10px 10px 3px 10px; }}
QToolButton#navBtn {{
    background: transparent;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 8px;
    padding: 9px 11px 9px 9px;
    text-align: left;
    color: {SIDEBAR_DIM};
    font-size: 13px;
}}
QToolButton#navBtn:hover {{ background: {SIDEBAR_HOVER}; color: {SIDEBAR_TEXT}; }}
QToolButton#navBtn:checked {{
    background: #24324A;
    border-left-color: #60A5FA;
    color: {SIDEBAR_TEXT};
    font-weight: 600;
}}
QFrame#sidebarStatusCard {{ background: {SIDEBAR_ALT}; border: 1px solid #2A374B; border-radius: 11px; }}
QLabel#sidebarStatusTitle {{ color: {SIDEBAR_DIM}; font-size: 10px; font-weight: 700; }}

/* ---------- 页面级组件 ---------- */
QLabel#pageTitle {{ font-size: 23px; font-weight: 750; color: {TEXT}; }}
QLabel#pageSubtitle {{ color: {TEXT_DIM}; font-size: 13px; }}
QFrame#bannerInfo {{ background: {ACCENT_FAINT}; border: 1px solid #C8DAF8; border-radius: 9px; }}
QFrame#bannerWarn {{ background: {WARNING_BG}; border: 1px solid #F3D29B; border-radius: 9px; }}
QLabel#chip {{
    background: {ACCENT_SOFT};
    color: {ACCENT};
    border: 1px solid #C8DAF8;
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 12px;
    font-weight: 600;
}}
QLabel#fieldLabel {{ color: {TEXT_DIM}; font-size: 12px; font-weight: 600; }}
QLabel#onboardingEyebrow {{ color: {ACCENT}; font-size: 11px; font-weight: 700; }}
QLabel#onboardingTitle {{ color: {TEXT}; font-size: 20px; font-weight: 750; }}
QLabel#onboardingHint {{ color: {TEXT_DIM}; }}
QPlainTextEdit#logConsole {{
    background: {LOG_BG};
    color: {LOG_TEXT};
    border: none;
    selection-background-color: #33466A;
}}
QTextEdit#chatView {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 10px;
}}
QScrollArea#previewArea {{ background: {SURFACE_ALT}; border: 1px dashed {BORDER_STRONG}; border-radius: 9px; }}
QLabel#emptyPreview {{ color: {TEXT_DIM}; }}
QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:hover {{ background: #B9C5D5; }}
"""


def apply_theme(app: QtWidgets.QApplication) -> None:
    """应用 Fusion 风格、默认字体与全局 QSS。"""
    app.setStyle("Fusion")
    font = QtGui.QFont("Microsoft YaHei UI")
    font.setStyleHint(QtGui.QFont.StyleHint.SansSerif)
    font.setPointSizeF(9.5)
    app.setFont(font)
    app.setStyleSheet(_QSS)


__all__ = [
    "ACCENT",
    "ACCENT_FAINT",
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
    "SIDEBAR",
    "SIDEBAR_ALT",
    "SIDEBAR_DIM",
    "SIDEBAR_HOVER",
    "SIDEBAR_TEXT",
    "SUCCESS",
    "SUCCESS_BG",
    "SURFACE_ALT",
    "TEXT",
    "TEXT_DIM",
    "WARNING",
    "WARNING_BG",
    "apply_theme",
]
