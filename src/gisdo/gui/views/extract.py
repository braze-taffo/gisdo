"""提取工作流视图：对齐门禁 + 数据提取 + 清单展示。"""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtWidgets

from gisdo.engine import ops
from gisdo.engine.alignment import Alignment, build_draft
from gisdo.engine.runner import ScriptResult
from gisdo.engine.versioning import versioned_path
from gisdo.gui.widgets import JsonTreeView
from gisdo.gui.workers import start_worker


class ExtractView(QtWidgets.QWidget):
    def __init__(self, state, log):
        super().__init__()
        self.state = state
        self.log = log
        self.alignment: Alignment | None = None
        self._build()
        self.state.inventory_changed.connect(self._on_inventory)
        self.state.modern_runtime_changed.connect(self._refresh_enabled)

    def _build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        # 工程
        proj_row = QtWidgets.QHBoxLayout()
        self.project_edit = QtWidgets.QLineEdit()
        self.project_edit.setReadOnly(True)
        self.project_edit.setPlaceholderText("先在“检查”页检查一个 APRX 工程")
        browse = QtWidgets.QPushButton("浏览…")
        browse.clicked.connect(self._on_browse_project)
        proj_row.addWidget(QtWidgets.QLabel("权威工程："))
        proj_row.addWidget(self.project_edit, 1)
        proj_row.addWidget(browse)
        layout.addLayout(proj_row)

        # 输出目录
        out_row = QtWidgets.QHBoxLayout()
        self.output_edit = QtWidgets.QLineEdit()
        self.output_edit.setPlaceholderText("留空则自动生成 版本化目录名_v1_日期")
        out_browse = QtWidgets.QPushButton("浏览…")
        out_browse.clicked.connect(self._on_browse_output)
        out_row.addWidget(QtWidgets.QLabel("输出目录："))
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(out_browse)
        layout.addLayout(out_row)

        skip_hashes = QtWidgets.QCheckBox("跳过 SHA-256（更快，不校验内容一致性）")
        layout.addWidget(skip_hashes)
        self.skip_hashes = skip_hashes

        # 对齐块
        layout.addWidget(QtWidgets.QLabel("对齐确认块（可编辑）："))
        self.alignment_edit = QtWidgets.QPlainTextEdit()
        self.alignment_edit.setReadOnly(False)
        self.alignment_edit.setMinimumHeight(180)
        layout.addWidget(self.alignment_edit)

        gen_btn = QtWidgets.QPushButton("生成对齐块")
        gen_btn.clicked.connect(self._on_generate_alignment)
        layout.addWidget(gen_btn)

        confirm_row = QtWidgets.QHBoxLayout()
        self.confirm_check = QtWidgets.QCheckBox("我已核对上述对齐信息，确认执行写操作")
        self.confirm_check.toggled.connect(self._refresh_enabled)
        confirm_row.addWidget(self.confirm_check)
        confirm_row.addWidget(QtWidgets.QWidget(), 1)
        self.run_btn = QtWidgets.QPushButton("确认并提取")
        self.run_btn.clicked.connect(self._on_run)
        self.run_btn.setEnabled(False)
        confirm_row.addWidget(self.run_btn)
        layout.addLayout(confirm_row)

        # 结果
        layout.addWidget(QtWidgets.QLabel("提取清单："))
        self.result_view = JsonTreeView()
        layout.addWidget(self.result_view, 1)

    def _on_inventory(self, inventory) -> None:
        self.project_edit.setText(self.state.last_aprx)
        self._refresh_enabled()

    def _on_browse_project(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择 APRX", "", "ArcGIS Pro 工程 (*.aprx)")
        if path:
            self.project_edit.setText(path)

    def _on_browse_output(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择输出父目录")
        if path:
            self.output_edit.setText(path)

    def _default_output(self) -> str:
        explicit = self.output_edit.text().strip()
        if explicit:
            return explicit
        parent = self.state.settings.output_root or str(Path.cwd())
        return str(versioned_path(parent, "extract"))

    def _on_generate_alignment(self) -> None:
        project = self.project_edit.text().strip()
        if not project:
            QtWidgets.QMessageBox.warning(self, "缺少工程", "请先选择一个 APRX 工程。")
            return
        out = self._default_output()
        alignment = build_draft(
            project=project,
            inventory=self.state.inventory,
            output_location=out,
            will_create=["extraction_manifest.json", "workspaces/", "files/"],
            output_format="版本化目录 + extraction_manifest.json",
        )
        self.alignment = alignment
        self.alignment_edit.setPlainText(alignment.as_block())

    def _refresh_enabled(self, *args) -> None:
        ready = (
            self.state.modern is not None
            and bool(self.project_edit.text().strip())
            and self.confirm_check.isChecked()
            and self.alignment is not None
        )
        self.run_btn.setEnabled(ready)

    def _on_run(self) -> None:
        if self.alignment is None:
            QtWidgets.QMessageBox.warning(self, "未生成对齐块", "请先点击“生成对齐块”。")
            return
        project = self.project_edit.text().strip()
        out = self._default_output()
        # 用户可编辑对齐块文本；以文本里的字段回填（保留编辑），但 confirmed 由勾选驱动。
        self.alignment.confirm()
        self.run_btn.setEnabled(False)
        self.result_view.show_raw_text("正在提取…")
        start_worker(
            ops.extract_data,
            self.state.modern, project, out,
            alignment=self.alignment, skip_hashes=self.skip_hashes.isChecked(),
            on_finished=self._on_done,
            on_error=self._on_error,
            on_log=self.log.append_log,
        )

    def _on_done(self, result: ScriptResult) -> None:
        self.run_btn.setEnabled(self.confirm_check.isChecked())
        if result.json is not None:
            self.result_view.show_json(result.json, label="extraction_manifest")
            ok = bool(result.json.get("all_verified"))
            out_dir = result.json.get("output_dir", "")
            rc = "全部校验通过" if ok else "存在未校验项（见清单 uncopied/exact_match）"
            QtWidgets.QMessageBox.information(self, "提取完成", f"输出：{out_dir}\n{rc}")
        else:
            self.result_view.show_raw_text(result.stderr or result.stdout or f"退出码 {result.returncode}")

    def _on_error(self, msg: str) -> None:
        self.run_btn.setEnabled(self.confirm_check.isChecked())
        self.result_view.show_raw_text(f"错误：{msg}")
        QtWidgets.QMessageBox.critical(self, "提取失败", msg)
