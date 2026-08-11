"""运行时发现与探测视图。"""

from __future__ import annotations

from PySide6 import QtWidgets

from gisdo.engine import runtime as runtime_mod
from gisdo.engine.runtime import Runtime
from gisdo.gui import theme
from gisdo.gui.widgets import PageHeader
from gisdo.gui.workers import start_worker


class RuntimeView(QtWidgets.QWidget):
    def __init__(self, state, log):
        super().__init__()
        self.state = state
        self.log = log
        self._modern_paths: list[str] = []
        self._legacy_paths: list[str] = []
        self._auto_discovered = False
        self._current_worker = None
        self._build()

    def _build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(PageHeader(
            "运行时", "发现本机 GeoScene/ArcGIS Pro 与 ArcMap Python 环境，选定后供各功能页使用"))

        # Pro 运行时
        modern_group = QtWidgets.QGroupBox("GeoScene / ArcGIS Pro 运行时（用于 APRX / GDB / 提取 / 打包）")
        modern_layout = QtWidgets.QVBoxLayout(modern_group)
        self.modern_group = QtWidgets.QButtonGroup(self)
        self.modern_group.setExclusive(True)
        self.modern_radios: list[QtWidgets.QRadioButton] = []
        self.modern_container = QtWidgets.QVBoxLayout()
        self.modern_container.setSpacing(4)
        modern_layout.addLayout(self.modern_container)
        probe_row = QtWidgets.QHBoxLayout()
        self.probe_btn = QtWidgets.QPushButton("探测选中运行时")
        self.probe_btn.setProperty("kind", "primary")
        self.probe_btn.clicked.connect(self._on_probe)
        self.probe_btn.setEnabled(False)
        probe_row.addWidget(self.probe_btn)
        probe_row.addWidget(QtWidgets.QWidget(), 1)
        modern_layout.addLayout(probe_row)
        self.probe_summary = QtWidgets.QTextEdit()
        self.probe_summary.setReadOnly(True)
        self.probe_summary.setMaximumHeight(170)
        self.probe_summary.setPlaceholderText("探测结果：产品、扩展、arcpy 版本、工具箱等")
        modern_layout.addWidget(self.probe_summary)
        layout.addWidget(modern_group)

        # ArcMap 运行时
        legacy_group = QtWidgets.QGroupBox("ArcMap 遗留运行时（Python 2.7，用于 MXD / 旧数据集 / 线桥）")
        legacy_layout = QtWidgets.QVBoxLayout(legacy_group)
        self.legacy_group = QtWidgets.QButtonGroup(self)
        self.legacy_group.setExclusive(True)
        self.legacy_radios: list[QtWidgets.QRadioButton] = []
        self.legacy_container = QtWidgets.QVBoxLayout()
        self.legacy_container.setSpacing(4)
        legacy_layout.addLayout(self.legacy_container)
        layout.addWidget(legacy_group)

        # 底部操作行
        bottom = QtWidgets.QHBoxLayout()
        self.discover_btn = QtWidgets.QPushButton("重新发现运行时")
        self.discover_btn.clicked.connect(self._on_discover)
        bottom.addWidget(self.discover_btn)
        self.status = QtWidgets.QLabel("尚未发现")
        self.status.setStyleSheet(f"color: {theme.TEXT_DIM};")
        bottom.addWidget(self.status, 1)
        layout.addLayout(bottom)

        layout.addStretch(1)

        self.modern_group.idToggled.connect(self._on_modern_selected)
        self.legacy_group.idToggled.connect(self._on_legacy_selected)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # 首次显示时自动发现一次（若有保存路径但尚未探测，也借这次发现确认可用）
        if not self._auto_discovered:
            self._auto_discovered = True
            self._on_discover()

    def _clear_layout(self, layout: QtWidgets.QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _on_discover(self) -> None:
        self.discover_btn.setEnabled(False)
        self.status.setText("正在发现…")
        self._current_worker = start_worker(
            runtime_mod.list_runtimes,
            on_finished=self._on_discover_done,
            on_error=self._on_discover_error,
            on_log=self.log.append_log,
        )

    def _on_discover_done(self, discovery) -> None:
        self._current_worker = None
        self.discover_btn.setEnabled(True)
        self._modern_paths = list(discovery.modern_candidates)
        self._legacy_paths = list(discovery.legacy_arcmap_candidates)
        self._populate_modern()
        self._populate_legacy()
        total = len(self._modern_paths) + len(self._legacy_paths)
        self.status.setText(f"发现 {len(self._modern_paths)} 个 Pro / {len(self._legacy_paths)} 个 ArcMap 运行时")
        if not total and discovery.error:
            self.status.setText(f"未发现运行时：{discovery.error}")

    def _on_discover_error(self, msg: str) -> None:
        self._current_worker = None
        self.discover_btn.setEnabled(True)
        self.status.setText(f"发现失败：{msg}")

    def _populate_modern(self) -> None:
        self._clear_layout(self.modern_container)
        self.modern_radios.clear()
        for path in self._modern_paths:
            radio = QtWidgets.QRadioButton(path)
            self.modern_group.addButton(radio, len(self.modern_radios))
            self.modern_container.addWidget(radio)
            self.modern_radios.append(radio)
        if not self._modern_paths:
            empty = QtWidgets.QLabel("未发现 Pro 运行时，可在「设置」页手动指定 python.exe")
            empty.setStyleSheet(f"color: {theme.TEXT_DIM};")
            self.modern_container.addWidget(empty)
        # 优先选中设置中保存的；否则自动选第一个。
        saved = self.state.settings.modern_python
        for index, path in enumerate(self._modern_paths):
            if path == saved:
                self.modern_radios[index].setChecked(True)
        if self.modern_group.checkedId() < 0 and self.modern_radios:
            self.modern_radios[0].setChecked(True)
        self.probe_btn.setEnabled(self.modern_group.checkedId() >= 0)

    def _populate_legacy(self) -> None:
        self._clear_layout(self.legacy_container)
        self.legacy_radios.clear()
        for path in self._legacy_paths:
            radio = QtWidgets.QRadioButton(path)
            self.legacy_group.addButton(radio, len(self.legacy_radios))
            self.legacy_container.addWidget(radio)
            self.legacy_radios.append(radio)
        if not self._legacy_paths:
            empty = QtWidgets.QLabel("未发现 ArcMap 运行时（仅处理 MXD/旧数据集时需要）")
            empty.setStyleSheet(f"color: {theme.TEXT_DIM};")
            self.legacy_container.addWidget(empty)
        saved = self.state.settings.arcmap_python
        for index, path in enumerate(self._legacy_paths):
            if path == saved:
                self.legacy_radios[index].setChecked(True)
        if self.legacy_group.checkedId() < 0 and self.legacy_radios:
            self.legacy_radios[0].setChecked(True)

    def _on_modern_selected(self, button_id: int, checked: bool) -> None:
        if not checked:
            return
        path = self._modern_paths[button_id] if 0 <= button_id < len(self._modern_paths) else ""
        self.state.set_modern(Runtime(python=path, family="待探测", source="discover"))
        self.probe_btn.setEnabled(True)

    def _on_legacy_selected(self, button_id: int, checked: bool) -> None:
        if not checked:
            return
        path = self._legacy_paths[button_id] if 0 <= button_id < len(self._legacy_paths) else ""
        self.state.set_arcmap(Runtime(python=path, family="ArcMap", is_py2=True, source="discover"))

    def _on_probe(self) -> None:
        if self.state.modern is None:
            return
        python = self.state.modern.python
        self.probe_btn.setEnabled(False)
        self.probe_summary.setText(f"正在探测 {python} …")
        self._current_worker = start_worker(
            runtime_mod.probe,
            python,
            on_finished=self._on_probe_done,
            on_error=self._on_probe_error,
            on_log=self.log.append_log,
        )

    def _on_probe_done(self, probe: dict) -> None:
        self._current_worker = None
        self.probe_btn.setEnabled(True)
        if self.state.modern is not None:
            self.state.modern.family = probe.get("runtime_family", "Pro")
            self.state.modern.probe = probe
        ext = probe.get("extensions", {})
        avail = [k for k, v in ext.items() if v == "Available"]
        pkgs = probe.get("python_packages", {})
        text = (
            f"运行时：{probe.get('runtime_family', '?')}\n"
            f"解释器：{probe.get('runtime_python', '?')}\n"
            f"产品：{probe.get('product', '?')}\n"
            f"工具数：{probe.get('tool_count', '?')}\n"
            f"扩展（可用）：{', '.join(avail) or '无'}\n"
            f"arcpy：{pkgs.get('arcpy')}\n"
            f"工具箱：{', '.join((probe.get('toolboxes') or [])[:8])}"
        )
        self.probe_summary.setText(text)

    def _on_probe_error(self, msg: str) -> None:
        self._current_worker = None
        self.probe_btn.setEnabled(True)
        self.probe_summary.setText(f"探测失败：{msg}")
