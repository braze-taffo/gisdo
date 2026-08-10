"""项目数据层测试：GisProject / ProjectStore 序列化与 CRUD。"""

from gisdo import project as project_mod
from gisdo.project import (
    DEFAULT_CONVERSATION_TITLE,
    Conversation,
    GisProject,
    ProjectStore,
    conversation_history_path,
    history_path,
)


def _isolate(tmp_path, monkeypatch):
    """让 projects.json / history 指向临时目录，避免污染真实 ~/.gisdo。"""
    monkeypatch.setattr(project_mod, "PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(project_mod, "PROJECTS_DIR", tmp_path / "projects")


# --------------------------------------------------------------------------- #
# GisProject
# --------------------------------------------------------------------------- #


def test_gis_project_from_dict_ignores_unknown_and_fills_defaults():
    p = GisProject.from_dict({"id": "x", "name": "n", "bogus": 1})
    assert p.id == "x"
    assert p.name == "n"
    assert p.project_dir == ""
    assert p.map_output_dir == ""
    assert p.created_at == ""


def test_gis_project_new_generates_id_and_timestamp():
    a = GisProject.new("proj")
    b = GisProject.new("proj")
    assert a.id != b.id
    assert a.created_at  # 非空 ISO 时间戳
    assert a.map_output_dir == ""


def test_conversation_new_generates_metadata():
    conversation = Conversation.new("  数据   检查  ")
    assert conversation.id
    assert conversation.title == "数据 检查"
    assert conversation.created_at
    assert conversation.updated_at


def test_gis_project_from_dict_loads_conversations():
    project = GisProject.from_dict({
        "id": "p1",
        "name": "项目",
        "current_conversation_id": "c1",
        "conversations": [{"id": "c1", "title": "会话一", "unknown": True}],
    })
    assert project.current_conversation_id == "c1"
    assert len(project.conversations) == 1
    assert project.conversations[0].title == "会话一"


# --------------------------------------------------------------------------- #
# ProjectStore
# --------------------------------------------------------------------------- #


def test_store_roundtrip(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    store = ProjectStore()
    p = store.create("a", project_dir="E:/p", map_output_dir="E:/map")
    store.set_current(p.id)

    loaded = ProjectStore.load()
    assert len(loaded.projects) == 1
    assert loaded.current_project_id == p.id
    assert loaded.current().name == "a"
    assert loaded.current().map_output_dir == "E:/map"


def test_store_bad_json_returns_empty(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    project_mod.PROJECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    project_mod.PROJECTS_FILE.write_text("{broken", encoding="utf-8")
    store = ProjectStore.load()
    assert store.projects == []
    assert store.current_project_id is None


def test_store_missing_file_returns_empty(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert ProjectStore.load().projects == []


def test_store_update_by_id(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    store = ProjectStore()
    p = store.create("a")
    p.map_output_dir = "E:/new"
    store.update(p)
    assert store.get(p.id).map_output_dir == "E:/new"


def test_store_delete_current_clears_current(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    store = ProjectStore()
    p = store.create("a")
    store.set_current(p.id)
    removed = store.delete(p.id)
    assert removed is not None
    assert store.get(p.id) is None
    assert store.current_project_id is None


def test_store_get_by_name():
    store = ProjectStore()
    p = store.create("uniq")
    assert store.get_by_name("uniq").id == p.id
    assert store.get_by_name("missing") is None


def test_history_path_under_projects_dir(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    hp = history_path("abc123")
    assert str(tmp_path / "projects" / "abc123" / "history.json") == str(hp)


def test_conversation_history_path_under_project(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    path = conversation_history_path("p1", "c1")
    assert path == tmp_path / "projects" / "p1" / "conversations" / "c1.json"


def test_ensure_conversation_for_new_project(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    store = ProjectStore()
    project = store.create("p1")
    conversation = store.ensure_current_conversation(project.id)
    assert conversation.title == DEFAULT_CONVERSATION_TITLE
    assert store.current_conversation(project.id).id == conversation.id


def test_legacy_history_is_copied_into_first_conversation(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    store = ProjectStore()
    project = store.create("p1")
    legacy = history_path(project.id)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        '{"version":1,"messages":[{"role":"user","content":"检查道路数据"}]}',
        encoding="utf-8",
    )

    conversation = store.ensure_current_conversation(project.id)
    migrated = conversation_history_path(project.id, conversation.id)
    assert conversation.title == "检查道路数据"
    assert migrated.read_text(encoding="utf-8") == legacy.read_text(encoding="utf-8")
    assert legacy.exists()  # 原文件保留作升级备份


def test_conversation_crud_and_current_selection(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    store = ProjectStore()
    project = store.create("p1")
    first = store.ensure_current_conversation(project.id)
    second = store.create_conversation(project.id, "第二条")
    assert store.current_conversation(project.id).id == second.id

    renamed = store.rename_conversation(project.id, second.id, " 新名称 ")
    assert renamed.title == "新名称"
    store.set_current_conversation(project.id, first.id)
    assert store.current_conversation(project.id).id == first.id

    history = conversation_history_path(project.id, first.id)
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text("{}", encoding="utf-8")
    removed = store.delete_conversation(project.id, first.id)
    assert removed.id == first.id
    assert not history.exists()
    assert store.current_conversation(project.id).id == second.id


def test_store_from_dict_ignores_unknown_fields():
    store = ProjectStore.from_dict({"version": 99, "projects": [{"id": "x", "name": "n"}],
                                    "unknown": True})
    assert len(store.projects) == 1
    assert store.projects[0].name == "n"


def test_store_duplicate_name_allowed():
    store = ProjectStore()
    store.create("dup")
    store.create("dup")
    assert len(store.projects) == 2
