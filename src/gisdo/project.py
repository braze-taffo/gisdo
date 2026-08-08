"""项目注册表与对话历史路径（纯数据，无 GUI 依赖）。

用户把 GISdo 当 coding harness 用：新建项目、设置项目文件夹与地图输出文件夹，
每个项目一个对话历史。注册表存 ``~/.gisdo/projects.json``（小、常改），
对话历史独立存 ``~/.gisdo/projects/<id>/history.json``（大、随对话增长）。

风格对齐 :mod:`gisdo.config`：dataclass + ``to_dict``/``from_dict``（按已知字段过滤，
新增字段用默认值即兼容旧文件）+ JSON 持久化。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from gisdo.config import SETTINGS_DIR

PROJECTS_FILE = SETTINGS_DIR / "projects.json"
PROJECTS_DIR = SETTINGS_DIR / "projects"
HISTORY_VERSION = 1


def history_path(project_id: str) -> Path:
    """某项目对话历史文件路径。"""
    return PROJECTS_DIR / project_id / "history.json"


@dataclass
class GisProject:
    """一个 GIS 项目。``project_dir`` 是源数据/产物参考根，``map_output_dir`` 是地图输出文件夹。"""

    id: str
    name: str
    project_dir: str = ""
    map_output_dir: str = ""
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> GisProject:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

    @staticmethod
    def new(name: str, project_dir: str = "", map_output_dir: str = "") -> GisProject:
        return GisProject(
            id=uuid4().hex,
            name=name,
            project_dir=project_dir,
            map_output_dir=map_output_dir,
            created_at=datetime.now(timezone.utc).isoformat(),
        )


@dataclass
class ProjectStore:
    """项目注册表：项目列表 + 当前激活项目 id。"""

    projects: list[GisProject] = field(default_factory=list)
    current_project_id: str | None = None

    # --- 序列化 ---
    def to_dict(self) -> dict:
        return {
            "version": 1,
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

    # --- 当前激活 ---
    def current(self) -> GisProject | None:
        if self.current_project_id is None:
            return None
        return self.get(self.current_project_id)

    def set_current(self, project_id: str | None) -> None:
        self.current_project_id = project_id
        self.save()


__all__ = ["PROJECTS_DIR", "PROJECTS_FILE", "GisProject", "ProjectStore", "history_path"]
