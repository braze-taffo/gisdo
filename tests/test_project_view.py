"""项目视图冒烟测试（pytest-qt）：新建/激活/删除项目。"""

from gisdo import project as project_mod
from gisdo.gui.state import AppState
from gisdo.gui.views.project import ProjectView


def _isolate(tmp_path, monkeypatch):
    """让 projects.json 指向临时目录，避免污染真实 ~/.gisdo。"""
    monkeypatch.setattr(project_mod, "PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(project_mod, "PROJECTS_DIR", tmp_path / "projects")


def test_project_view_new_and_current(qtbot, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state = AppState()
    view = ProjectView(state, None)
    qtbot.addWidget(view)
    view.show()

    assert view.list_widget.count() == 0
    assert "当前项目：无" in view.current_label.text()

    # 新建
    view.name_edit.setText("测试项目")
    view.project_dir_edit.setText("E:/proj")
    view.map_output_edit.setText("E:/map")
    view._on_save()

    assert view.list_widget.count() == 1
    assert state.current_project is not None
    assert state.current_project.name == "测试项目"
    assert "当前项目：测试项目" in view.current_label.text()


def test_project_view_edit_fields(qtbot, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state = AppState()
    view = ProjectView(state, None)
    qtbot.addWidget(view)

    proj = state.create_project("p1", "E:/proj", "E:/map")
    # 触发列表刷新 + 选中
    view._refresh_list()
    for i in range(view.list_widget.count()):
        if view.list_widget.item(i).data(256) == proj.id:
            view.list_widget.setCurrentRow(i)
            break
    assert view.name_edit.text() == "p1"
    assert view.project_dir_edit.text() == "E:/proj"
    assert view.map_output_edit.text() == "E:/map"


def test_project_view_delete_current_clears(qtbot, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state = AppState()
    view = ProjectView(state, None)
    qtbot.addWidget(view)

    proj = state.create_project("p1")
    state.set_current_project(proj)
    assert state.current_project is not None

    # 直接调底层删除（绕过 QMessageBox）
    state.delete_project(proj.id)
    assert state.current_project is None
    assert view.list_widget.count() == 0
    assert "当前项目：无" in view.current_label.text()


def test_appstate_signals_emit(qtbot, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state = AppState()
    seen = []
    state.current_project_changed.connect(lambda p: seen.append(p))
    proj = state.create_project("sig")
    state.set_current_project(proj)
    state.set_current_project(None)
    # 至少触发过 2 次（设当前 + 清当前）
    assert len(seen) >= 2
