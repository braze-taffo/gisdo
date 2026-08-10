"""项目注册表与项目内多会话历史（纯数据，无 GUI 依赖）。

用户把 GISdo 当 coding harness 用：新建项目、设置项目文件夹与地图输出文件夹，
每个项目可有多条独立对话。注册表存 ``~/.gisdo/projects.json``（小、常改），
对话历史存 ``~/.gisdo/projects/<project-id>/conversations/<conversation-id>.json``。
旧版单一 ``history.json`` 会在首次打开项目时复制到默认会话，原文件保留作备份。

风格对齐 :mod:`gisdo.config`：dataclass + ``to_dict``/``from_dict``（按已知字段过滤，
新增字段用默认值即兼容旧文件）+ JSON 持久化。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from gisdo.config import SETTINGS_DIR

PROJECTS_FILE = SETTINGS_DIR / "projects.json"
PROJECTS_DIR = SETTINGS_DIR / "projects"
HISTORY_VERSION = 1
DEFAULT_CONVERSATION_TITLE = "新对话"
LEGACY_CONVERSATION_TITLE = "原对话"
MAX_CONVERSATION_TITLE_LENGTH = 40


def history_path(project_id: str) -> Path:
    """某项目旧版单一对话历史文件路径（仅用于兼容迁移）。"""
    return PROJECTS_DIR / project_id / "history.json"


def conversation_history_path(project_id: str, conversation_id: str) -> Path:
    """某项目内一条会话的历史文件路径。"""
    return PROJECTS_DIR / project_id / "conversations" / f"{conversation_id}.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_conversation_title(title: str) -> str:
    compact = " ".join(str(title).split())
    return compact[:MAX_CONVERSATION_TITLE_LENGTH] or DEFAULT_CONVERSATION_TITLE


def _legacy_history_title(path: Path) -> str:
    """尽量用旧历史首条用户消息命名；无法读取时使用“原对话”。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return LEGACY_CONVERSATION_TITLE
    for message in data.get("messages", []) or []:
        if message.get("role") == "user" and message.get("content"):
            return _clean_conversation_title(str(message["content"]))
    return LEGACY_CONVERSATION_TITLE


@dataclass
class Conversation:
    """项目内一条独立对话的轻量元数据；消息正文单独存文件。"""

    id: str
    title: str = DEFAULT_CONVERSATION_TITLE
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Conversation:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

    @staticmethod
    def new(title: str = DEFAULT_CONVERSATION_TITLE) -> Conversation:
        now = _utc_now()
        return Conversation(
            id=uuid4().hex,
            title=_clean_conversation_title(title),
            created_at=now,
            updated_at=now,
        )


@dataclass
class GisProject:
    """一个 GIS 项目。``project_dir`` 是源数据/产物参考根，``map_output_dir`` 是地图输出文件夹。"""

    id: str
    name: str
    project_dir: str = ""
    map_output_dir: str = ""
    created_at: str = ""
    conversations: list[Conversation] = field(default_factory=list)
    current_conversation_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> GisProject:
        known = set(cls.__dataclass_fields__)
        values = {k: v for k, v in data.items() if k in known and k != "conversations"}
        values["conversations"] = [
            Conversation.from_dict(item)
            for item in (data.get("conversations", []) or [])
            if isinstance(item, dict)
        ]
        return cls(**values)

    @staticmethod
    def new(name: str, project_dir: str = "", map_output_dir: str = "") -> GisProject:
        return GisProject(
            id=uuid4().hex,
            name=name,
            project_dir=project_dir,
            map_output_dir=map_output_dir,
            created_at=_utc_now(),
        )


@dataclass
class ProjectStore:
    """项目注册表：项目列表 + 当前激活项目 id。"""

    projects: list[GisProject] = field(default_factory=list)
    current_project_id: str | None = None

    # --- 序列化 ---
    def to_dict(self) -> dict:
        return {
            "version": 2,
            "current_project_id": self.current_project_id,
            "projects": [p.to_dict() for p in self.projects],
        }

    @classmethod
    def from_dict(cls, data: dict) -> ProjectStore:
        if not isinstance(data, dict):
            return cls()
        projects = [GisProject.from_dict(p) for p in data.get("projects", []) or []]
        return cls(
            projects=projects,
            current_project_id=data.get("current_project_id"),
        )

    def save(self) -> None:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        with PROJECTS_FILE.open("w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls) -> ProjectStore:
        if PROJECTS_FILE.is_file():
            try:
                return cls.from_dict(json.loads(PROJECTS_FILE.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        return cls()

    # --- CRUD ---
    def create(self, name: str, project_dir: str = "", map_output_dir: str = "") -> GisProject:
        project = GisProject.new(name, project_dir, map_output_dir)
        self.projects.append(project)
        self.save()
        return project

    def update(self, project: GisProject) -> None:
        for i, p in enumerate(self.projects):
            if p.id == project.id:
                self.projects[i] = project
                break
        self.save()

    def delete(self, project_id: str) -> GisProject | None:
        removed = self.get(project_id)
        if removed is None:
            return None
        self.projects = [p for p in self.projects if p.id != project_id]
        if self.current_project_id == project_id:
            self.current_project_id = None
        self.save()
        return removed

    def get(self, project_id: str) -> GisProject | None:
        for p in self.projects:
            if p.id == project_id:
                return p
        return None

    def get_by_name(self, name: str) -> GisProject | None:
        for p in self.projects:
            if p.name == name:
                return p
        return None

    # --- 项目内会话 ---
    def conversations_for(self, project_id: str) -> list[Conversation]:
        project = self.get(project_id)
        return list(project.conversations) if project is not None else []

    def get_conversation(self, project_id: str, conversation_id: str) -> Conversation | None:
        project = self.get(project_id)
        if project is None:
            return None
        for conversation in project.conversations:
            if conversation.id == conversation_id:
                return conversation
        return None

    def current_conversation(self, project_id: str) -> Conversation | None:
        project = self.get(project_id)
        if project is None or project.current_conversation_id is None:
            return None
        return self.get_conversation(project_id, project.current_conversation_id)

    def ensure_current_conversation(self, project_id: str) -> Conversation:
        """确保项目至少有一条当前会话，并兼容迁移旧 ``history.json``。"""
        project = self.get(project_id)
        if project is None:
            raise KeyError(f"项目不存在：{project_id}")
        current = self.current_conversation(project_id)
        if current is not None:
            return current
        if project.conversations:
            project.current_conversation_id = project.conversations[-1].id
            self.save()
            return project.conversations[-1]

        legacy = history_path(project_id)
        title = _legacy_history_title(legacy) if legacy.is_file() else DEFAULT_CONVERSATION_TITLE
        conversation = Conversation.new(title)
        project.conversations.append(conversation)
        project.current_conversation_id = conversation.id
        if legacy.is_file():
            target = conversation_history_path(project_id, conversation.id)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    shutil.copy2(legacy, target)
            except OSError:
                pass  # 旧文件原样保留；后续新消息仍会写入新会话路径
        self.save()
        return conversation

    def create_conversation(
        self,
        project_id: str,
        title: str = DEFAULT_CONVERSATION_TITLE,
    ) -> Conversation:
        project = self.get(project_id)
        if project is None:
            raise KeyError(f"项目不存在：{project_id}")
        conversation = Conversation.new(title)
        project.conversations.append(conversation)
        project.current_conversation_id = conversation.id
        self.save()
        return conversation

    def rename_conversation(
        self,
        project_id: str,
        conversation_id: str,
        title: str,
    ) -> Conversation | None:
        conversation = self.get_conversation(project_id, conversation_id)
        if conversation is None:
            return None
        conversation.title = _clean_conversation_title(title)
        conversation.updated_at = _utc_now()
        self.save()
        return conversation

    def touch_conversation(self, project_id: str, conversation_id: str) -> Conversation | None:
        conversation = self.get_conversation(project_id, conversation_id)
        if conversation is None:
            return None
        conversation.updated_at = _utc_now()
        self.save()
        return conversation

    def set_current_conversation(self, project_id: str, conversation_id: str) -> Conversation | None:
        project = self.get(project_id)
        conversation = self.get_conversation(project_id, conversation_id)
        if project is None or conversation is None:
            return None
        project.current_conversation_id = conversation.id
        self.save()
        return conversation

    def delete_conversation(self, project_id: str, conversation_id: str) -> Conversation | None:
        project = self.get(project_id)
        conversation = self.get_conversation(project_id, conversation_id)
        if project is None or conversation is None:
            return None
        project.conversations = [item for item in project.conversations if item.id != conversation_id]
        if project.current_conversation_id == conversation_id:
            project.current_conversation_id = (
                project.conversations[-1].id if project.conversations else None
            )
        try:
            conversation_history_path(project_id, conversation_id).unlink(missing_ok=True)
        except OSError:
            pass
        self.save()
        return conversation

    # --- 当前激活 ---
    def current(self) -> GisProject | None:
        if self.current_project_id is None:
            return None
        return self.get(self.current_project_id)

    def set_current(self, project_id: str | None) -> None:
        self.current_project_id = project_id
        self.save()


__all__ = [
    "DEFAULT_CONVERSATION_TITLE",
    "HISTORY_VERSION",
    "PROJECTS_DIR",
    "PROJECTS_FILE",
    "Conversation",
    "GisProject",
    "ProjectStore",
    "conversation_history_path",
    "history_path",
]
