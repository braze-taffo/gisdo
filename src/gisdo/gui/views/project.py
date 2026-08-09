"""项目视图：新建/编辑/删除 GIS 项目，管理当前激活项目。

每个项目有「项目文件夹」（源数据/产物参考根）与「地图输出文件夹」（写操作默认落点）。
切换当前项目时发出 ``current_project_changed``，Agent 对话随之切换。
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from gisdo.gui import theme
from gisdo.gui.icons import get_icon
from gisdo.gui.widgets import PageHeader
from gisdo.project import GisProject


class ProjectView(QtWidgets.QWidget):
    def __init__(self, state, log):
        super().__init__()
        self.state = state
        self.log = log
        self._editing_id: str | None = None
        self._build()
        self._load()
        self._wire()

    # --- UI ---
    def _build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)
        layout.addWidget(PageHeader(
            "项目", "Agent 的任务上下文与写操作默认落在「当前项目」的地图输出文件夹"))

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(10)

        # 左：项目列表卡
        list_group = QtWidgets.QGroupBox("项目列表")
        left = QtWidgets.QVBoxLayout(list_group)
        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.currentItemChanged.connect(self._on_select)
        left.addWidget(self.list_widget, 1)
        self.current_label = QtWidgets.QLabel("当前项目：无")
        self.current_label.setStyleSheet(f"color: {theme.ACCENT}; font-weight: 600;")
        left.addWidget(self.current_label)
        body.addWidget(list_group, 2)

        # 右：详情卡
        detail_group = QtWidgets.QGroupBox("项目详情")
        right = QtWidgets.QVBoxLayout(detail_group)
        form = QtWidgets.QFormLayout()
        form.setSpacing(8)

        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText("项目名称")
        form.addRow("名称：", self.name_edit)

        self.project_dir_edit = QtWidgets.QLineEdit()
        self.project_dir_edit.setPlaceholderText("源数据/产物参考根")
        proj_browse = QtWidgets.QPushButton("浏览…")
        proj_browse.clicked.connect(lambda: self._pick_dir(self.project_dir_edit))
        proj_row = QtWidgets.QHBoxLayout()
        proj_row.addWidget(self.project_dir_edit, 1)
        proj_row.addWidget(proj_browse)
        form.addRow("项目文件夹：", proj_row)

        self.map_output_edit = QtWidgets.QLineEdit()
        self.map_output_edit.setPlaceholderText("处理出的地图/产物默认放这里")
        map_browse = QtWidgets.QPushButton("浏览…")
        map_browse.clicked.connect(lambda: self._pick_dir(self.map_output_edit))
        map_row = QtWidgets.QHBoxLayout()
        map_row.addWidget(self.map_output_edit, 1)
        map_row.addWidget(map_browse)
        form.addRow("地图输出文件夹：", map_row)

        self.created_label = QtWidgets.QLabel("")
        self.created_label.setStyleSheet(f"color: {theme.TEXT_DIM};")
        form.addRow("创建时间：", self.created_label)
        right.addLayout(form)
        right.addSpacing(6)

        # 按钮行
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(8)
        self.new_btn = QtWidgets.QPushButton(get_icon("plus", theme.TEXT), "新建")
        self.new_btn.clicked.connect(self._on_new)
        btn_row.addWidget(self.new_btn)
        self.save_btn = QtWidgets.QPushButton(get_icon("check", "#FFFFFF"), "保存")
        self.save_btn.setProperty("kind", "primary")
        self.save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self.save_btn)
        self.current_btn = QtWidgets.QPushButton("设为当前项目")
        self.current_btn.clicked.connect(self._on_set_current)
        btn_row.addWidget(self.current_btn)
        self.clear_current_btn = QtWidgets.QPushButton("清除当前")
        self.clear_current_btn.clicked.connect(self._on_clear_current)
        btn_row.addWidget(self.clear_current_btn)
        btn_row.addStretch(1)
        self.delete_btn = QtWidgets.QPushButton(get_icon("trash", theme.DANGER), "删除")
        self.delete_btn.setProperty("kind", "danger")
        self.delete_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self.delete_btn)
        right.addLayout(btn_row)
        right.addStretch(1)
        body.addWidget(detail_group, 3)

        layout.addLayout(body, 1)

    def _wire(self) -> None:
        self.state.projects_changed.connect(self._refresh_list)
        # 当前项目变化也要刷新列表里的 ✓/加粗标记，走 _refresh_list 一并处理
        self.state.current_project_changed.connect(lambda *_: self._refresh_list())

    def _pick_dir(self, edit: QtWidgets.QLineEdit) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择文件夹")
        if path:
            edit.setText(path)

    # --- 数据填充 ---
    def _load(self) -> None:
        self._refresh_list()
        self._refresh_current()

    def _refresh_list(self, _projects=None) -> None:
        current = self.list_widget.currentItem()
        selected_id = current.data(QtCore.Qt.ItemDataRole.UserRole) if current else None
        proj = self.state.current_project
        current_id = proj.id if proj else None
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for project in self.state.projects:
            is_current = project.id == current_id
            item = QtWidgets.QListWidgetItem(("✓ " if is_current else "") + project.name)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, project.id)
            if is_current:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setForeground(QtGui.QColor(theme.ACCENT))
            if project.map_output_dir:
                item.setToolTip(project.map_output_dir)
            self.list_widget.addItem(item)
        if selected_id is not None:
            for i in range(self.list_widget.count()):
                if self.list_widget.item(i).data(QtCore.Qt.ItemDataRole.UserRole) == selected_id:
                    self.list_widget.setCurrentRow(i)
                    break
        self.list_widget.blockSignals(False)
        self._refresh_current()

    def _refresh_current(self, _project=None) -> None:
        proj = self.state.current_project
        self.current_label.setText(f"当前项目：{proj.name if proj else '无'}")
        current = self.list_widget.currentItem()
        current_id = current.data(QtCore.Qt.ItemDataRole.UserRole) if current else None
        self.clear_current_btn.setEnabled(proj is not None)
        self.current_btn.setEnabled(current_id is not None and current_id != (proj.id if proj else None))

    def _on_select(self, current, _previous) -> None:
        if current is None:
            self._clear_form()
            return
        project = self.state._store.get(current.data(QtCore.Qt.ItemDataRole.UserRole))
        self._load_form(project)

    def _load_form(self, project: GisProject) -> None:
        self._editing_id = project.id
        self.name_edit.setText(project.name)
        self.project_dir_edit.setText(project.project_dir)
        self.map_output_edit.setText(project.map_output_dir)
        self.created_label.setText(project.created_at)

    def _clear_form(self) -> None:
        self._editing_id = None
        self.name_edit.clear()
        self.project_dir_edit.clear()
        self.map_output_edit.clear()
        self.created_label.clear()

    def _form_values(self) -> tuple[str, str, str]:
        return (self.name_edit.text().strip(),
                self.project_dir_edit.text().strip(),
                self.map_output_edit.text().strip())

    # --- 操作 ---
    def _on_new(self) -> None:
        self.list_widget.setCurrentItem(None)
        self._clear_form()
        # 新建时地图输出文件夹预填全局输出根目录
        if not self.map_output_edit.text():
            self.map_output_edit.setText(self.state.settings.output_root)

    def _on_save(self) -> None:
        name, project_dir, map_output = self._form_values()
        if not name:
            QtWidgets.QMessageBox.warning(self, "缺少名称", "请填写项目名称。")
            return
        if self._editing_id is not None:
            proj = self.state._store.get(self._editing_id)
            if proj is None:
                return
            proj.name = name
            proj.project_dir = project_dir
            proj.map_output_dir = map_output
            self.state.update_project(proj)
        else:
            proj = self.state.create_project(name, project_dir, map_output)
            self._editing_id = proj.id
            self.state.set_current_project(proj)
            self._select_id(proj.id)

    def _on_set_current(self) -> None:
        current = self.list_widget.currentItem()
        if current is None:
            return
        proj = self.state._store.get(current.data(QtCore.Qt.ItemDataRole.UserRole))
        if proj is not None:
            self.state.set_current_project(proj)

    def _on_clear_current(self) -> None:
        self.state.set_current_project(None)

    def _on_delete(self) -> None:
        current = self.list_widget.currentItem()
        if current is None:
            return
        proj = self.state._store.get(current.data(QtCore.Qt.ItemDataRole.UserRole))
        if proj is None:
            return
        ans = QtWidgets.QMessageBox.question(
            self, "删除项目",
            f"删除项目「{proj.name}」？\n该项目的对话历史也会一并删除。",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if ans != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.state.delete_project(proj.id)
        self._clear_form()

    def _select_id(self, project_id: str) -> None:
        for i in range(self.list_widget.count()):
            if self.list_widget.item(i).data(QtCore.Qt.ItemDataRole.UserRole) == project_id:
                self.list_widget.setCurrentRow(i)
                break


__all__ = ["ProjectView"]
