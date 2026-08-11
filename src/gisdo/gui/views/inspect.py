"""只读检查视图：APRX / GDB / MXD / 旧数据集。"""

from __future__ import annotations

from PySide6 import QtWidgets

from gisdo.engine import ops
from gisdo.engine.runner import ScriptResult
from gisdo.gui.widgets import Banner, JsonTreeView, PageHeader
from gisdo.gui.workers import start_worker

KIND_APRX = "APRX (.aprx)"
KIND_GDB = "GDB (.gdb)"
KIND_MXD = "MXD (.mxd, 遗留)"
KIND_DATASET = "旧数据集 (shapefile / FC)"


class InspectView(QtWidgets.QWidget):
    def __init__(self, state, log):
        super().__init__()
        self.state = state
        self.log = log
        self._current_worker = None
        self._build()
        self.state.modern_runtime_changed.connect(self._refresh_enabled)
        self.state.arcmap_runtime_changed.connect(self._refresh_enabled)

    def _build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(PageHeader("检查", "只读解析工程/数据结构，不修改任何文件；APRX 结果会联动到「提取」页"))

        self.banner = Banner("", "warning")
        layout.addWidget(self.banner)

        # 输入卡
        input_group = QtWidgets.QGroupBox("检查对象")
        input_layout = QtWidgets.QVBoxLayout(input_group)
        form = QtWidgets.QFormLayout()
        form.setSpacing(8)
        self.kind_combo = QtWidgets.QComboBox()
        self.kind_combo.addItems([KIND_APRX, KIND_GDB, KIND_MXD, KIND_DATASET])
        self.kind_combo.currentTextChanged.connect(self._refresh_enabled)
        form.addRow("类型：", self.kind_combo)
        path_row = QtWidgets.QHBoxLayout()
        self.path_edit = QtWidgets.QLineEdit()
        self.path_edit.setPlaceholderText("选择或输入工程/数据路径")
        self.path_edit.textChanged.connect(self._refresh_enabled)
        browse = QtWidgets.QPushButton("浏览…")
        browse.clicked.connect(self._on_browse)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse)
        form.addRow("路径：", path_row)
        input_layout.addLayout(form)
        self.run_btn = QtWidgets.QPushButton("执行只读检查")
        self.run_btn.setProperty("kind", "primary")
        self.run_btn.clicked.connect(self._on_run)
        input_layout.addWidget(self.run_btn)
        layout.addWidget(input_group)

        # 结果卡
        result_group = QtWidgets.QGroupBox("检查结果")
        result_layout = QtWidgets.QVBoxLayout(result_group)
        self.result_view = JsonTreeView()
        result_layout.addWidget(self.result_view)
        layout.addWidget(result_group, 1)

        self._refresh_enabled()

    def _kind(self) -> str:
        return self.kind_combo.currentText()

    def _needs_modern(self) -> bool:
        return self._kind() in (KIND_APRX, KIND_GDB)

    def _on_browse(self) -> None:
        kind = self._kind()
        if kind == KIND_APRX:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择 APRX", "", "ArcGIS Pro 工程 (*.aprx)")
        elif kind == KIND_GDB:
            path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择文件 GDB")
        elif kind == KIND_MXD:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择 MXD", "", "ArcMap 文档 (*.mxd)")
        else:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择数据集", "", "Shapefile (*.shp);;所有文件 (*)")
        if path:
            self.path_edit.setText(path)

    def _refresh_enabled(self, *args) -> None:
        modern_ok = self.state.modern is not None
        arcmap_ok = self.state.arcmap is not None
        need_modern = self._needs_modern()
        runtime_ok = modern_ok if need_modern else arcmap_ok
        self.run_btn.setEnabled(runtime_ok and bool(self.path_edit.text().strip()))
        if runtime_ok:
            self.banner.set_text("")
        else:
            which = "GeoScene/ArcGIS Pro 运行时" if need_modern else "ArcMap 运行时"
            self.banner.set_text(f"当前类型需要{which}，请先到「运行时」页选定（Ctrl+3）。")

    def _on_run(self) -> None:
        path = self.path_edit.text().strip()
        if not path:
            return
        self.run_btn.setEnabled(False)
        kind = self._kind()
        if kind == KIND_APRX:
            rt = self.state.modern
            self._current_worker = start_worker(
                ops.inspect_aprx, rt, path,
                on_finished=lambda r: self._on_done(r, kind="aprx", project=path),
                on_error=self._on_error, on_log=self.log.append_log,
            )
        elif kind == KIND_GDB:
            rt = self.state.modern
            self._current_worker = start_worker(
                ops.inspect_gdb, rt, path,
                on_finished=lambda r: self._on_done(r, kind="gdb"),
                on_error=self._on_error, on_log=self.log.append_log,
            )
        elif kind == KIND_MXD:
            rt = self.state.arcmap
            self._current_worker = start_worker(
                ops.inspect_mxd, rt, path,
                on_finished=lambda r: self._on_done(r, kind="mxd"),
                on_error=self._on_error, on_log=self.log.append_log,
            )
        else:
            rt = self.state.arcmap
            self._current_worker = start_worker(
                ops.inspect_legacy_dataset, rt, path,
                on_finished=lambda r: self._on_done(r, kind="dataset"),
                on_error=self._on_error, on_log=self.log.append_log,
            )

    def _on_done(self, result: ScriptResult, *, kind: str, project: str = "") -> None:
        self._current_worker = None
        self.run_btn.setEnabled(True)
        if result.failed:
            self.result_view.show_raw_text(result.stderr or result.stdout or f"退出码 {result.returncode}")
            return
        data = result.json
        if data is None:
            self.result_view.show_raw_text(result.stdout)
            return
        self.result_view.show_json(data, label=kind)
        # APRX 检查结果共享给提取/打包视图
        if kind == "aprx" and project:
            self.state.set_inventory(data, project=project)

    def _on_error(self, msg: str) -> None:
        self._current_worker = None
        self.run_btn.setEnabled(True)
        self.result_view.show_raw_text(f"错误：{msg}")
