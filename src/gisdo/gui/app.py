"""GISdo 主窗口与 GUI 入口。

侧边栏导航 + 堆叠视图 + 底部日志面板。所有 engine 操作经 worker 线程池执行，
绝不阻塞 UI。
"""

from __future__ import annotations

import sys

from PySide6 import QtCore, QtGui, QtWidgets

from gisdo import __version__
from gisdo.gui.state import AppState
from gisdo.gui.views.chat import ChatView
from gisdo.gui.views.extract import ExtractView
from gisdo.gui.views.inspect import InspectView
from gisdo.gui.views.project import ProjectView
from gisdo.gui.views.render import RenderView
from gisdo.gui.views.runtime import RuntimeView
from gisdo.gui.views.settings import SettingsView
from gisdo.gui.widgets import LogConsole


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.state = AppState()
        self.log = LogConsole()
        self._current_worker = None
        self.setWindowTitle(f"GISdo · GeoScene 工作台 v{__version__}")
        self.resize(1280, 820)
        self._build()
        self.state.restore_runtimes()

    def _build(self) -> None:
        central = QtWidgets.QWidget()
        outer = QtWidgets.QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)

        # 侧边栏
        self.nav = QtWidgets.QListWidget()
        self.nav.setFixedWidth(170)
        self.nav.setSpacing(2)
        self.nav.currentRowChanged.connect(self._on_nav)
        outer.addWidget(self.nav)

        # 视图栈
        self.stack = QtWidgets.QStackedWidget()
        outer.addWidget(self.stack, 1)

        self._views = []
        self._add_view("运行时", RuntimeView(self.state, self.log))
        self._add_view("项目", ProjectView(self.state, self.log))
        self._add_view("Agent", ChatView(self.state, self.log))
        self._add_view("检查", InspectView(self.state, self.log))
        self._add_view("提取", ExtractView(self.state, self.log))
        self._add_view("出图", RenderView(self.state, self.log))
        self._add_view("设置", SettingsView(self.state, self.log))
        self.nav.setCurrentRow(2)  # 默认打开 Agent 页

        self.setCentralWidget(central)

        # 日志面板
        self.log_dock = QtWidgets.QDockWidget("日志", self)
        self.log_dock.setWidget(self.log)
        self.log_dock.setFeatures(QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable |
                                  QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)

        # 状态栏
        self.status = QtWidgets.QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("就绪。先在“运行时”页发现运行时，再到“Agent”页用自然语言下任务。")

        # 菜单
        self._build_menu()

    def _add_view(self, name: str, widget: QtWidgets.QWidget) -> None:
        self.nav.addItem(name)
        self.stack.addWidget(widget)
        self._views.append(widget)

    def _on_nav(self, row: int) -> None:
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)

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

        help_menu = menubar.addMenu("帮助(&H)")
        about = QtGui.QAction("关于", self)
        about.triggered.connect(self._about)
        help_menu.addAction(about)

    def _cancel_current(self) -> None:
        worker = getattr(self, "_current_worker", None)
        if worker is not None and hasattr(worker, "request_cancel"):
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
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
