"""分类线出图视图：渲染 PNG/PDF + 像素校验 + 预览。"""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtWidgets

from gisdo.engine import ops
from gisdo.engine.alignment import Alignment
from gisdo.engine.runner import ScriptResult
from gisdo.engine.versioning import versioned_file
from gisdo.gui.widgets import JsonTreeView, PngPreview
from gisdo.gui.workers import start_worker


class RenderView(QtWidgets.QWidget):
    def __init__(self, state, log):
        super().__init__()
        self.state = state
        self.log = log
        self._build()

    def _build(self) -> None:
        outer = QtWidgets.QHBoxLayout(self)

        # 左：参数表单
        form_widget = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(form_widget)
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        self.input_edit = QtWidgets.QLineEdit()
        self.input_edit.setPlaceholderText("export_legacy_lines 产出的 JSON")
        input_browse = QtWidgets.QPushButton("浏览…")
        input_browse.clicked.connect(self._browse_input)
        input_row = QtWidgets.QHBoxLayout()
        input_row.addWidget(self.input_edit, 1)
        input_row.addWidget(input_browse)
        form.addRow("输入 JSON：", input_row)

        self.breaks_edit = QtWidgets.QLineEdit("0,20,40,60,80,100")
        self.breaks_edit.setPlaceholderText("逗号分隔，如 0,20,40,60,80,100")
        form.addRow("分类断点 *：", self.breaks_edit)

        self.colors_edit = QtWidgets.QLineEdit()
        self.colors_edit.setPlaceholderText("留空用默认 5 色；如 #ffffb2,#fecc5c,#fd8d3c,#f03b20,#bd0026")
        form.addRow("颜色：", self.colors_edit)

        self.labels_edit = QtWidgets.QLineEdit()
        self.labels_edit.setPlaceholderText("留空自动；竖线 | 分隔")
        form.addRow("图例标签：", self.labels_edit)

        self.title_edit = QtWidgets.QLineEdit()
        form.addRow("标题：", self.title_edit)
        self.legend_edit = QtWidgets.QLineEdit()
        form.addRow("图例标题：", self.legend_edit)

        self.scale_bar_edit = QtWidgets.QLineEdit()
        self.scale_bar_edit.setPlaceholderText("坐标单位长度，如 2000；留空不加比例尺")
        form.addRow("比例尺长度：", self.scale_bar_edit)
        self.scale_label_edit = QtWidgets.QLineEdit()
        form.addRow("比例尺文本：", self.scale_label_edit)

        self.output_png_edit = QtWidgets.QLineEdit()
        self.output_png_edit.setPlaceholderText("留空自动生成 版本化名_v1_日期.png")
        png_browse = QtWidgets.QPushButton("浏览…")
        png_browse.clicked.connect(self._browse_png)
        png_row = QtWidgets.QHBoxLayout()
        png_row.addWidget(self.output_png_edit, 1)
        png_row.addWidget(png_browse)
        form.addRow("输出 PNG：", png_row)

        self.pdf_check = QtWidgets.QCheckBox("同时输出 PDF（同名 .pdf）")
        form.addRow("", self.pdf_check)

        # 尺寸
        size_row = QtWidgets.QHBoxLayout()
        self.width_spin = QtWidgets.QDoubleSpinBox()
        self.width_spin.setRange(2, 40)
        self.width_spin.setValue(10.0)
        self.height_spin = QtWidgets.QDoubleSpinBox()
        self.height_spin.setRange(2, 40)
        self.height_spin.setValue(6.5)
        self.dpi_spin = QtWidgets.QSpinBox()
        self.dpi_spin.setRange(72, 1200)
        self.dpi_spin.setValue(300)
        self.line_width_spin = QtWidgets.QDoubleSpinBox()
        self.line_width_spin.setRange(0.1, 10)
        self.line_width_spin.setSingleStep(0.1)
        self.line_width_spin.setValue(1.1)
        size_row.addWidget(QtWidgets.QLabel("宽")); size_row.addWidget(self.width_spin)
        size_row.addWidget(QtWidgets.QLabel("高")); size_row.addWidget(self.height_spin)
        size_row.addWidget(QtWidgets.QLabel("DPI")); size_row.addWidget(self.dpi_spin)
        size_row.addWidget(QtWidgets.QLabel("线宽")); size_row.addWidget(self.line_width_spin)
        size_row.addWidget(QtWidgets.QWidget(), 1)
        form.addRow("尺寸：", size_row)

        self.axis_km_check = QtWidgets.QCheckBox("坐标轴用 km")
        self.no_north_check = QtWidgets.QCheckBox("无指北针")
        self.no_grid_check = QtWidgets.QCheckBox("无网格")
        opt_row = QtWidgets.QHBoxLayout()
        opt_row.addWidget(self.axis_km_check)
        opt_row.addWidget(self.no_north_check)
        opt_row.addWidget(self.no_grid_check)
        opt_row.addWidget(QtWidgets.QWidget(), 1)
        form.addRow("选项：", opt_row)

        self.confirm_check = QtWidgets.QCheckBox("我已确认输出路径为新文件（不会覆盖既有文件）")
        self.run_btn = QtWidgets.QPushButton("渲染并校验")
        self.run_btn.clicked.connect(self._on_run)
        action_row = QtWidgets.QHBoxLayout()
        action_row.addWidget(self.confirm_check)
        action_row.addWidget(self.run_btn, 1)
        form.addRow("", action_row)

        outer.addWidget(form_widget, 1)

        # 右：预览 + 校验结果
        right = QtWidgets.QVBoxLayout()
        right.addWidget(QtWidgets.QLabel("PNG 预览："))
        self.preview = PngPreview()
        right.addWidget(self.preview, 1)
        right.addWidget(QtWidgets.QLabel("像素校验："))
        self.verify_view = JsonTreeView()
        self.verify_view.setMaximumHeight(180)
        right.addWidget(self.verify_view)
        outer.addLayout(right, 1)

    def _browse_input(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择分类线 JSON", "", "JSON (*.json)")
        if path:
            self.input_edit.setText(path)

    def _browse_png(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "选择输出 PNG", "", "PNG (*.png)")
        if path:
            self.output_png_edit.setText(path)

    def _default_png(self) -> str:
        explicit = self.output_png_edit.text().strip()
        if explicit:
            return explicit
        parent = self.state.settings.output_root or str(Path.cwd())
        return str(versioned_file(parent, "classified_map", "png"))

    def _build_options(self) -> ops.RenderOptions:
        breaks_text = self.breaks_edit.text().strip()
        if not breaks_text:
            raise ValueError("请填写分类断点。")
        breaks = [float(x) for x in breaks_text.split(",") if x.strip()]
        colors_text = self.colors_edit.text().strip()
        labels_text = self.labels_edit.text().strip()
        scale_bar = None
        sb_text = self.scale_bar_edit.text().strip()
        if sb_text:
            scale_bar = float(sb_text)
        out_png = self._default_png()
        out_pdf = None
        if self.pdf_check.isChecked():
            out_pdf = str(Path(out_png).with_suffix(".pdf"))
        return ops.RenderOptions(
            breaks=breaks,
            colors=[c.strip() for c in colors_text.split(",")] if colors_text else None,
            labels=[l.strip() for l in labels_text.split("|")] if labels_text else None,
            title=self.title_edit.text().strip(),
            legend_title=self.legend_edit.text().strip(),
            scale_bar=scale_bar,
            scale_label=self.scale_label_edit.text().strip() or None,
            output_pdf=out_pdf,
            report=str(Path(out_png).with_suffix(".validation.json")),
            axis_km=self.axis_km_check.isChecked(),
            no_north_arrow=self.no_north_check.isChecked(),
            no_grid=self.no_grid_check.isChecked(),
            line_width=self.line_width_spin.value(),
            width=self.width_spin.value(),
            height=self.height_spin.value(),
            dpi=self.dpi_spin.value(),
        ), out_png

    def _on_run(self) -> None:
        try:
            options, out_png = self._build_options()
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "参数错误", str(exc))
            return
        if not self.input_edit.text().strip():
            QtWidgets.QMessageBox.warning(self, "缺少输入", "请选择输入 JSON。")
            return
        if not self.confirm_check.isChecked():
            QtWidgets.QMessageBox.warning(self, "未确认", "请勾选确认后再渲染。")
            return
        alignment = Alignment(
            authoritative_project=self.input_edit.text().strip(),
            output_location=out_png,
            will_create=[out_png] + ([options.output_pdf] if options.output_pdf else []),
            output_format_and_size=f"PNG {options.width}x{options.height}in @ {options.dpi}dpi",
            classification_field_and_rules=f"breaks={options.breaks}",
        )
        alignment.confirm()
        self.run_btn.setEnabled(False)
        self._current_png = out_png
        start_worker(
            ops.render_classified,
            self.input_edit.text().strip(), out_png, options,
            alignment=alignment,
            on_finished=self._on_render_done,
            on_error=self._on_error,
            on_log=self.log.append_log,
        )

    def _on_render_done(self, result: ScriptResult) -> None:
        self.run_btn.setEnabled(True)
        if result.json is None:
            self.verify_view.show_raw_text(result.stderr or result.stdout or f"退出码 {result.returncode}")
            return
        png_path = result.json.get("output_png", getattr(self, "_current_png", ""))
        self.preview.set_png(png_path)
        # 紧接着像素校验
        start_worker(
            ops.verify_png, png_path,
            on_finished=self._on_verify_done,
            on_error=self._on_error,
            on_log=self.log.append_log,
        )

    def _on_verify_done(self, result: ScriptResult) -> None:
        if result.json is not None:
            self.verify_view.show_json(result.json, label="verify_png")
            passed = bool(result.json.get("passed"))
            msg = "像素校验通过" if passed else "像素校验未通过（疑似空白或近空白导出）"
            (QtWidgets.QMessageBox.information if passed else QtWidgets.QMessageBox.warning)(
                self, "校验结果", msg
            )
        else:
            self.verify_view.show_raw_text(result.stderr or result.stdout)

    def _on_error(self, msg: str) -> None:
        self.run_btn.setEnabled(True)
        self.verify_view.show_raw_text(f"错误：{msg}")
        QtWidgets.QMessageBox.critical(self, "渲染失败", msg)
