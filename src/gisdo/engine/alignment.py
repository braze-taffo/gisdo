"""对齐门禁。

把 SKILL.md 的"对齐确认块"固化为代码。任何写操作（提取、打包、出图、导出）
必须先构造 :class:`Alignment` 草稿、由用户确认，再调用 :meth:`Alignment.confirm`。
确认前 ``require_confirmed`` 会抛错，阻断写流程。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gisdo.engine.safety import SafetyError

# SKILL.md 中对齐确认块的 11 个字段，顺序固定。
BLOCK_FIELDS = [
    "权威工程",
    "数据主目录",
    "外部依赖",
    "现有成果",
    "拟读取内容",
    "拟新增内容",
    "不会修改的内容",
    "输出位置",
    "分级字段与规则",
    "输出格式与尺寸",
    "GeoScene桌面授权",
]


@dataclass
class Alignment:
    """一次任务的对齐信息。``confirmed`` 必须在写操作前置为 True。"""

    authoritative_project: str = ""
    data_root: str = ""
    external_dependencies: list[str] = field(default_factory=list)
    existing_outputs: list[str] = field(default_factory=list)
    will_read: list[str] = field(default_factory=list)
    will_create: list[str] = field(default_factory=list)
    will_not_modify: list[str] = field(default_factory=list)
    output_location: str = ""
    classification_field_and_rules: str = ""
    output_format_and_size: str = ""
    desktop_authorized: bool = False
    confirmed: bool = False
    notes: str = ""

    def as_block(self) -> str:
        """渲染为 SKILL.md 规定的对齐确认块文本。"""
        lines = []
        for field_name in BLOCK_FIELDS:
            attr = _field_attr(field_name)
            value = getattr(self, attr)
            if isinstance(value, list):
                value = "、".join(value) if value else "（无）"
            elif isinstance(value, bool):
                value = "已授权" if value else "未授权"
            lines.append(f"{field_name}：{value}")
        return "\n".join(lines)

    def confirm(self) -> None:
        self.confirmed = True

    def require_confirmed(self) -> None:
        if not self.confirmed:
            raise SafetyError("写操作前必须先确认对齐块。请检查对齐信息并点击“确认”。")


def _field_attr(field_name: str) -> str:
    return {
        "权威工程": "authoritative_project",
        "数据主目录": "data_root",
        "外部依赖": "external_dependencies",
        "现有成果": "existing_outputs",
        "拟读取内容": "will_read",
        "拟新增内容": "will_create",
        "不会修改的内容": "will_not_modify",
        "输出位置": "output_location",
        "分级字段与规则": "classification_field_and_rules",
        "输出格式与尺寸": "output_format_and_size",
        "GeoScene桌面授权": "desktop_authorized",
    }[field_name]


def build_draft(
    *,
    project: str,
    inventory: dict[str, Any] | None = None,
    output_location: str = "",
    task_description: str = "",
    will_create: list[str] | None = None,
    classification_field: str = "",
    output_format: str = "",
    desktop_authorized: bool = False,
) -> Alignment:
    """从 APRX 检查结果构造对齐草稿。用户随后可编辑并确认。"""
    inventory = inventory or {}
    sources = inventory.get("data_sources", []) or []
    broken = inventory.get("broken", []) or []

    external = [s for s in sources if _is_external(s, project)]
    internal = [s for s in sources if not _is_external(s, project)]

    will_read = [project]
    if internal:
        will_read.append(f"{len(internal)} 个工程内数据源")

    will_not_modify = [project, "所有原始 GDB / shapefile / 栅格数据"]

    return Alignment(
        authoritative_project=project,
        data_root=str(Path(project).resolve().parent) if project else "",
        external_dependencies=external or ["（无）"],
        existing_outputs=[f"{len(broken)} 个断裂源" if broken else "（无已知断裂源）"],
        will_read=will_read,
        will_create=will_create or [],
        will_not_modify=will_not_modify,
        output_location=output_location,
        classification_field_and_rules=classification_field or "（待指定）",
        output_format_and_size=output_format or "（待指定）",
        desktop_authorized=desktop_authorized,
        notes=task_description,
    )


def _is_external(source: str, project: str) -> bool:
    """粗略判断数据源是否在工程根之外。"""
    if not source or not project:
        return True
    try:
        source_path = Path(source).resolve()
        project_root = Path(project).resolve().parent
        return project_root not in source_path.parents and source_path != project_root
    except (OSError, ValueError):
        return True
