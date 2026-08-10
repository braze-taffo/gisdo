"""Agent 页项目内多会话管理测试。"""

import json

from PySide6 import QtWidgets

from gisdo import project as project_mod
from gisdo.gui.state import AppState
from gisdo.gui.views.chat import ChatView
from gisdo.project import conversation_history_path


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(project_mod, "PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(project_mod, "PROJECTS_DIR", tmp_path / "projects")


def _view_with_project(qtbot, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state = AppState()
    project = state.create_project("测试项目")
    state.set_current_project(project)
    view = ChatView(state, None)
    qtbot.addWidget(view)
    return state, view


def test_chat_view_starts_with_one_conversation(qtbot, tmp_path, monkeypatch):
    state, view = _view_with_project(qtbot, tmp_path, monkeypatch)
    assert len(state.conversations) == 1
    assert state.current_conversation is not None
    assert view.conversation_combo.count() == 1
    assert view.conversation_combo.currentData() == state.current_conversation.id


def test_chat_view_creates_and_renames_conversation(qtbot, tmp_path, monkeypatch):
    state, view = _view_with_project(qtbot, tmp_path, monkeypatch)
    monkeypatch.setattr(
        QtWidgets.QInputDialog,
        "getText",
        lambda *_args, **_kwargs: ("道路检查", True),
    )
    view._on_new_conversation()
    assert len(state.conversations) == 2
    assert state.current_conversation.title == "道路检查"
    assert view.conversation_combo.currentText() == "道路检查"

    monkeypatch.setattr(
        QtWidgets.QInputDialog,
        "getText",
        lambda *_args, **_kwargs: ("道路检查二版", True),
    )
    view._on_rename_conversation()
    assert state.current_conversation.title == "道路检查二版"
    assert view.conversation_combo.currentText() == "道路检查二版"


def test_chat_view_switches_conversation(qtbot, tmp_path, monkeypatch):
    state, view = _view_with_project(qtbot, tmp_path, monkeypatch)
    first_id = state.current_conversation.id
    second = state.create_conversation("第二条")
    view._refresh_conversation_combo()
    assert state.current_conversation.id == second.id

    first_index = view.conversation_combo.findData(first_id)
    view.conversation_combo.setCurrentIndex(first_index)
    assert state.current_conversation.id == first_id


def test_chat_view_delete_last_conversation_creates_blank_one(qtbot, tmp_path, monkeypatch):
    state, view = _view_with_project(qtbot, tmp_path, monkeypatch)
    old_id = state.current_conversation.id
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QtWidgets.QMessageBox.StandardButton.Yes,
    )
    view._on_delete_conversation()
    assert len(state.conversations) == 1
    assert state.current_conversation.id != old_id
    assert state.current_conversation.title == "新对话"
    assert view.conversation_combo.count() == 1


def test_reset_only_clears_current_conversation(qtbot, tmp_path, monkeypatch):
    state, view = _view_with_project(qtbot, tmp_path, monkeypatch)
    project = state.current_project
    first = state.current_conversation
    first_path = conversation_history_path(project.id, first.id)
    first_path.parent.mkdir(parents=True, exist_ok=True)
    first_path.write_text(
        json.dumps({"version": 1, "messages": [{"role": "user", "content": "第一条"}]}),
        encoding="utf-8",
    )

    second = state.create_conversation("第二条")
    second_path = conversation_history_path(project.id, second.id)
    second_path.parent.mkdir(parents=True, exist_ok=True)
    second_path.write_text(
        json.dumps({"version": 1, "messages": [{"role": "user", "content": "第二条"}]}),
        encoding="utf-8",
    )
    view._reload_project_ui(save_current=False)
    view._on_reset()

    first_data = json.loads(first_path.read_text(encoding="utf-8"))
    second_data = json.loads(second_path.read_text(encoding="utf-8"))
    assert first_data["messages"][0]["content"] == "第一条"
    assert second_data["messages"] == []
