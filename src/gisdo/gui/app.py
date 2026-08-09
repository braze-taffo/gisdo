"""GISdo 主窗口与 GUI 入口。

自绘侧边栏导航（图标按钮 + 就绪状态卡）+ 堆叠视图 + 底部日志面板。
所有 engine 操作经 worker 线程池执行，绝不阻塞 UI。
"""

from __future__ import annotations

import sys

from PySide6 import QtCore, QtGui, QtWidgets

from gisdo import __version__
from gisdo.gui import theme
from gisdo.gui.icons import get_icon
from gisdo.gui.state import AppState
from gisdo.gui.views.chat import ChatView
from gisdo.gui.views.extract import ExtractView
from gisdo.gui.views.inspect import InspectView
from gisdo.gui.views.project import ProjectView
from gisdo.gui.views.render import RenderView
from gisdo.gui.views.runtime import RuntimeView
from gisdo.gui.views.settings import SettingsView
from gisdo.gui.widgets import LogConsole

# (名称, 图标, 页内说明) —— 顺序即侧边栏与 Ctrl+数字 顺序
_PAGES = [
    ("Agent 对话", "chat", "用自然语言下 GIS 任务，Agent 自主调用工具完成"),
    ("项目", "folder", "新建/编辑项目，设定 Agent 的当前工作上下文"),
    ("运行时", "cpu", "发现与选定 GeoScene/ArcGIS Pro、ArcMap Python 运行时"),
    ("检查", "search", "只读检查 APRX / GDB / MXD / 旧数据集"),
    ("提取", "package", "对齐门禁下的数据提取与清单校验"),
    ("出图", "image", "分类线渲染 PNG/PDF + 像素校验"),
    ("设置", "gear", "运行时路径、输出根目录、LLM 端点配置"),
]


class _NavButton(QtWidgets.QToolButton):
    """侧边栏导航按钮：图标 + 文字，checkable。"""

    def __init__(self, text: str, icon_name: str, parent=None):
        super().__init__(parent)
        self.setObjectName("navBtn")
        self.setText(f"  {text}")
        self.setIcon(get_icon(icon_name, theme.TEXT))
        self.setIconSize(QtCore.QSize(18, 18))
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Fixed)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._icon_name = icon_name

    def _refresh_icon(self, checked: bool) -> None:
        color = theme.ACCENT if checked else theme.TEXT
        self.setIcon(get_icon(self._icon_name, color))

    def setChecked(self, checked: bool) -> None:
        super().setChecked(checked)
        self._refresh_icon(checked)


class _StatusRow(QtWidgets.QLabel):
    """就绪状态卡中的一行：状态点 + 标签，可点击跳转（QLabel 以支持富文本状态点）。"""

    clicked = QtCore.Signal()

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self._label = label
        self.set_ready(None, "")

    def set_ready(self, ready: bool | None, detail: str) -> None:
        color = theme.TEXT_DIM if ready is None else (theme.SUCCESS if ready else theme.WARNING)
        text = f"{self._label}：{detail}" if detail else f"{self._label}：—"
        self.setText(f'<span style="color:{color}">●</span> '
                     f'<span style="color:{theme.TEXT_DIM}">{text}</span>')

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.state = AppState()
        self.log = LogConsole()
        self._current_worker = None
        self.setWindowTitle(f"GISdo · GeoScene 工作台 v{__version__}")
        self.resize(1320, 860)
        self._build()
        self.state.restore_runtimes()
        self._refresh_readiness()

    # ---------- 布局 ----------
    def _build(self) -> None:
        central = QtWidgets.QWidget()
        outer = QtWidgets.QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_sidebar())

        # 视图栈
        self.stack = QtWidgets.QStackedWidget()
        outer.addWidget(self.stack, 1)

        self._views = []
        view_classes = [ChatView, ProjectView, RuntimeView, InspectView,
                        ExtractView, RenderView, SettingsView]
        for cls in view_classes:
            self._add_view(cls(self.state, self.log))
        self._navigate_to(0)  # 默认 Agent 页

        self.setCentralWidget(central)
        self._build_log_dock()

        self.status = QtWidgets.QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("就绪。")
        self.status_label = QtWidgets.QLabel()
        self.status.addPermanentWidget(self.status_label)

        self._build_menu()
        self._build_shortcuts()

        # 就绪状态随 AppState 信号刷新
        self.state.modern_runtime_changed.connect(lambda *_: self._refresh_readiness())
        self.state.current_project_changed.connect(lambda *_: self._refresh_readiness())
        self.state.settings_changed.connect(lambda *_: self._refresh_readiness())

    def _build_sidebar(self) -> QtWidgets.QWidget:
        sidebar = QtWidgets.QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(196)
        layout = QtWidgets.QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(4)

        # Logo 区
        title = QtWidgets.QLabel("GISdo")
        title.setObjectName("appTitle")
        version = QtWidgets.QLabel(f"GeoScene 工作台 v{__version__}")
        version.setObjectName("appVersion")
        layout.addWidget(title)
        layout.addWidget(version)
        layout.addSpacing(14)

        # 导航按钮
        self.nav_buttons: list[_NavButton] = []
        for index, (name, icon_name, tip) in enumerate(_PAGES):
            btn = _NavButton(name, icon_name)
            btn.setToolTip(f"{tip}（Ctrl+{index + 1}）")
            btn.clicked.connect(lambda _=False, i=index: self._navigate_to(i))
            layout.addWidget(btn)
            self.nav_buttons.append(btn)
        layout.addStretch(1)

        # 就绪状态卡
        card = QtWidgets.QFrame()
        card.setObjectName("card")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(10, 8, 10, 8)
        card_layout.setSpacing(2)
        head = QtWidgets.QLabel("就绪状态")
        head.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 11px; font-weight: 600;")
        card_layout.addWidget(head)
        self.status_rows: dict[str, _StatusRow] = {}
        for key, label, page in (("project", "项目", 1), ("runtime", "运行时", 2), ("llm", "LLM", 6)):
            row = _StatusRow(label)
            row.clicked.connect(lambda _=False, p=page: self._navigate_to(p))
            card_layout.addWidget(row)
            self.status_rows[key] = row
        layout.addWidget(card)
        return sidebar

    def _build_log_dock(self) -> None:
        container = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setContentsMargins(8, 3, 8, 3)
        toolbar.addStretch(1)
        clear_btn = QtWidgets.QToolButton()
        clear_btn.setIcon(get_icon("clear", theme.TEXT_DIM))
        clear_btn.setIconSize(QtCore.QSize(14, 14))
        clear_btn.setToolTip("清空日志")
        clear_btn.clicked.connect(self.log.clear)
        toolbar.addWidget(clear_btn)
        scroll_btn = QtWidgets.QToolButton()
        scroll_btn.setIcon(get_icon("scroll", theme.TEXT_DIM))
        scroll_btn.setIconSize(QtCore.QSize(14, 14))
        scroll_btn.setToolTip("自动滚动到底部")
        scroll_btn.setCheckable(True)
        scroll_btn.setChecked(True)
        scroll_btn.toggled.connect(self.log.set_auto_scroll)
        toolbar.addWidget(scroll_btn)

        bar_widget = QtWidgets.QWidget()
        bar_widget.setStyleSheet(f"background: {theme.CARD}; border-bottom: 1px solid {theme.BORDER};")
        bar_widget.setLayout(toolbar)
        bar_widget.setFixedHeight(26)
        vbox.addWidget(bar_widget)
        vbox.addWidget(self.log, 1)

        self.log_dock = QtWidgets.QDockWidget("日志", self)
        self.log_dock.setWidget(container)
        self.log_dock.setFeatures(QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable |
                                  QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)
        self.log_dock.setMinimumHeight(120)
        self.resizeDocks([self.log_dock], [150], QtCore.Qt.Orientation.Vertical)

    def _build_shortcuts(self) -> None:
        for index in range(len(_PAGES)):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda i=index: self._navigate_to(i))

    # ---------- 导航 ----------
    def _add_view(self, widget: QtWidgets.QWidget) -> None:
        self.stack.addWidget(widget)
        self._views.append(widget)
        signal = getattr(widget, "navigate_requested", None)
        if signal is not None:
            signal.connect(self._on_navigate_requested)

    def _navigate_to(self, index: int) -> None:
        if not 0 <= index < self.stack.count():
            return
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == index)

    def _on_navigate_requested(self, name: str) -> None:
        """视图请求跳转：name 为页名（如 "项目"），兼容带序号的写法。"""
        for index, (page_name, _icon, _tip) in enumerate(_PAGES):
            if name in page_name:
                self._navigate_to(index)
                return

    # ---------- 就绪状态 ----------
    def _refresh_readiness(self) -> None:
        proj = self.state.current_project
        modern = self.state.modern
        s = self.state.settings
        llm_ok = bool(s.ai_base_url and s.ai_model)

        self.status_rows["project"].set_ready(proj is not None, proj.name if proj else "未选")
        self.status_rows["runtime"].set_ready(modern is not None, "已选" if modern else "未选")
        self.status_rows["llm"].set_ready(llm_ok, "已配置" if llm_ok else "未配置")

        parts = [
            f"项目 {'✓' if proj else '✗'}",
            f"运行时 {'✓' if modern else '✗'}",
            f"LLM {'✓' if llm_ok else '✗'}",
        ]
        self.status_label.setText("　".join(parts))

    # ---------- 菜单与杂项 ----------
    def _build_menu(self) -> None:
        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件(&F)")

        cancel_action = QtGui.QAction("取消当前操作", self)
        cancel_action.setShortcut("Esc")
        cancel_action.triggered.connect(self._cancel_current)
        file_menu.addAction(cancel_action)
        file_menu.addSeparator()
        quit_action = QtGui.QAction("退出", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = menubar.addMenu("视图(&V)")
        log_action = self.log_dock.toggleViewAction()
        log_action.setText("日志面板")
        view_menu.addAction(log_action)

        help_menu = menubar.addMenu("帮助(&H)")
        about = QtGui.QAction("关于", self)
        about.triggered.connect(self._about)
        help_menu.addAction(about)

    def _cancel_current(self) -> None:
        # 优先取消当前页视图暴露的 worker，其次主窗口自身记录的
        view = self.stack.currentWidget()
        worker = getattr(view, "_current_worker", None) or self._current_worker
        cancel = getattr(view, "_on_stop", None)
        if callable(cancel) and getattr(view, "_busy", False):
            cancel()
            self.status.showMessage("已请求取消…")
        elif worker is not None and hasattr(worker, "request_cancel"):
            worker.request_cancel()
            self.status.showMessage("已请求取消…")
        else:
            self.status.showMessage("当前无可取消的操作。")

    def _about(self) -> None:
        QtWidgets.QMessageBox.about(
            self, "关于 GISdo",
            f"<h3>GISdo · GeoScene 工作台</h3>"
            f"<p>版本 {__version__}</p>"
            f"<p>安全的 GeoScene/ArcGIS 工程检查、数据提取、打包、出图与校验桌面工具。</p>"
            f"<p>AI Agent 驱动（OpenAI 格式兼容云端 API）+ 确定性引擎。"
            f"应用进程不直接 import arcpy，所有 arcpy 工作经 subprocess 派发到发现到的运行时。</p>",
        )

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self._cancel_current()
        else:
            super().keyPressEvent(event)


def main() -> int:
    # 高 DPI 已在 PySide6 默认启用。
    from gisdo.config import seed_defaults_if_missing

    seed_defaults_if_missing()  # 打包后首次运行预置模型配置（如无 settings.json）
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("GISdo")
    app.setOrganizationName("GISdo")
    theme.apply_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
