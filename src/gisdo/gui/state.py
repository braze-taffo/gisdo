"""应用共享状态与设置持久化。

``AppState`` 是一个 QObject，持有当前选定的运行时、APRX 清单、输出根目录等，
并通过信号通知视图变化。设置持久化到 ``~/.gisdo/settings.json``。

``Settings`` 数据类已抽到 :mod:`gisdo.config`（无 GUI 依赖，CLI 共用），此处 re-export。
"""

from __future__ import annotations

import shutil

from PySide6 import QtCore

from gisdo.config import SETTINGS_DIR, SETTINGS_FILE, Settings
from gisdo.project import (
    DEFAULT_CONVERSATION_TITLE,
    Conversation,
    GisProject,
    ProjectStore,
)

__all__ = ["SETTINGS_DIR", "SETTINGS_FILE", "AppState", "Settings"]


class AppState(QtCore.QObject):
    """全局应用状态。"""

    modern_runtime_changed = QtCore.Signal(object)  # Runtime | None
    arcmap_runtime_changed = QtCore.Signal(object)
    inventory_changed = QtCore.Signal(object)  # dict | None
    settings_changed = QtCore.Signal(object)  # Settings
    projects_changed = QtCore.Signal(object)  # list[GisProject]
    current_project_changed = QtCore.Signal(object)  # GisProject | None
    conversations_changed = QtCore.Signal(object)  # list[Conversation]
    current_conversation_changed = QtCore.Signal(object)  # Conversation | None

    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings.load()
        self._modern = None  # Runtime
        self._arcmap = None
        self._inventory: dict | None = None
        self._last_aprx = ""
        self._store = ProjectStore.load()

    # --- runtimes ---
    @property
    def modern(self):
        return self._modern

    def set_modern(self, runtime) -> None:
        self._modern = runtime
        if runtime is not None:
            self.settings.modern_python = runtime.python
            self.settings.save()
        self.modern_runtime_changed.emit(runtime)

    @property
    def arcmap(self):
        return self._arcmap

    def set_arcmap(self, runtime) -> None:
        self._arcmap = runtime
        if runtime is not None:
            self.settings.arcmap_python = runtime.python
            self.settings.save()
        self.arcmap_runtime_changed.emit(runtime)

    def restore_runtimes(self) -> None:
        """启动时从设置恢复运行时（仅填充路径，不探测）。"""
        from gisdo.engine.runtime import Runtime

        if self.settings.modern_python:
            self._modern = Runtime(python=self.settings.modern_python, family="explicit", source="settings")
            self.modern_runtime_changed.emit(self._modern)
        if self.settings.arcmap_python:
            self._arcmap = Runtime(python=self.settings.arcmap_python, family="ArcMap", is_py2=True, source="settings")
            self.arcmap_runtime_changed.emit(self._arcmap)

    # --- inventory ---
    @property
    def inventory(self):
        return self._inventory

    def set_inventory(self, inventory: dict | None, project: str = "") -> None:
        self._inventory = inventory
        self._last_aprx = project
        self.inventory_changed.emit(inventory)

    @property
    def last_aprx(self) -> str:
        return self._last_aprx

    # --- projects ---
    @property
    def projects(self) -> list[GisProject]:
        return list(self._store.projects)

    @property
    def current_project(self) -> GisProject | None:
        return self._store.current()

    def create_project(self, name: str, project_dir: str = "", map_output_dir: str = "") -> GisProject:
        project = self._store.create(name, project_dir, map_output_dir)
        self.projects_changed.emit(self.projects)
        return project

    def update_project(self, project: GisProject) -> None:
        self._store.update(project)
        self.projects_changed.emit(self.projects)
        if self._store.current_project_id == project.id:
            self.current_project_changed.emit(self._store.current())

    def delete_project(self, project_id: str) -> GisProject | None:
        removed = self._store.delete(project_id)
        if removed is None:
            return None
        from gisdo.project import PROJECTS_DIR

        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
        self.projects_changed.emit(self.projects)
        self.current_project_changed.emit(self._store.current())
        self.conversations_changed.emit(self.conversations)
        self.current_conversation_changed.emit(self.current_conversation)
        return removed

    def set_current_project(self, project: GisProject | None) -> None:
        self._store.set_current(project.id if project is not None else None)
        if project is not None:
            self._store.ensure_current_conversation(project.id)
        self.current_project_changed.emit(self._store.current())
        self.conversations_changed.emit(self.conversations)
        self.current_conversation_changed.emit(self.current_conversation)

    # --- conversations ---
    @property
    def conversations(self) -> list[Conversation]:
        project = self.current_project
        return self._store.conversations_for(project.id) if project is not None else []

    @property
    def current_conversation(self) -> Conversation | None:
        project = self.current_project
        return self._store.current_conversation(project.id) if project is not None else None

    def ensure_current_conversation(self) -> Conversation | None:
        project = self.current_project
        if project is None:
            return None
        before = self._store.current_conversation(project.id)
        conversation = self._store.ensure_current_conversation(project.id)
        if before is None:
            self.conversations_changed.emit(self.conversations)
            self.current_conversation_changed.emit(conversation)
        return conversation

    def create_conversation(self, title: str = DEFAULT_CONVERSATION_TITLE) -> Conversation | None:
        project = self.current_project
        if project is None:
            return None
        conversation = self._store.create_conversation(project.id, title)
        self.conversations_changed.emit(self.conversations)
        self.current_conversation_changed.emit(conversation)
        return conversation

    def rename_conversation(self, conversation_id: str, title: str) -> Conversation | None:
        project = self.current_project
        if project is None:
            return None
        conversation = self._store.rename_conversation(project.id, conversation_id, title)
        if conversation is not None:
            self.conversations_changed.emit(self.conversations)
        return conversation

    def touch_conversation(self, conversation_id: str) -> Conversation | None:
        project = self.current_project
        if project is None:
            return None
        conversation = self._store.touch_conversation(project.id, conversation_id)
        if conversation is not None:
            self.conversations_changed.emit(self.conversations)
        return conversation

    def set_current_conversation(self, conversation_id: str) -> Conversation | None:
        project = self.current_project
        if project is None:
            return None
        conversation = self._store.set_current_conversation(project.id, conversation_id)
        if conversation is not None:
            self.current_conversation_changed.emit(conversation)
        return conversation

    def delete_conversation(self, conversation_id: str) -> Conversation | None:
        project = self.current_project
        if project is None:
            return None
        removed = self._store.delete_conversation(project.id, conversation_id)
        if removed is None:
            return None
        if not self._store.conversations_for(project.id):
            self._store.create_conversation(project.id)
        self.conversations_changed.emit(self.conversations)
        self.current_conversation_changed.emit(self.current_conversation)
        return removed

    # --- settings ---
    def update_settings(self, **kwargs) -> None:
        for key, value in kwargs.items():
            if hasattr(self.settings, key):
                setattr(self.settings, key, value)
        self.settings.save()
        self.settings_changed.emit(self.settings)
