"""版本化输出路径。

遵循 SKILL.md / tool-routing.md 的约定：``name_v1_YYYYMMDD``，重试自增版本号，
且路径在开工前必须不存在。脚本端也会拒绝覆盖，这里在派发前再校验一次，
形成 defense-in-depth。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

_MAX_VERSION = 999


def versioned_path(parent: Path | str, base_name: str, *, version: int = 1) -> Path:
    """返回 ``parent/base_name_v<N>_YYYYMMDD``，版本号自增直到路径不存在。"""
    parent = Path(parent)
    today = date.today().strftime("%Y%m%d")
    safe_base = base_name.rstrip("/\\")
    for current in range(version, _MAX_VERSION + 1):
        candidate = parent / f"{safe_base}_v{current}_{today}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"版本号超过上限 {_MAX_VERSION}，请清理输出目录：{parent}")


def versioned_file(parent: Path | str, stem: str, suffix: str, *, version: int = 1) -> Path:
    """返回带版本的文件路径，如 ``parent/stem_v1_YYYYMMDD.suffix``。"""
    parent = Path(parent)
    today = date.today().strftime("%Y%m%d")
    safe_stem = stem.rstrip("/\\")
    if not suffix.startswith("."):
        suffix = "." + suffix
    for current in range(version, _MAX_VERSION + 1):
        candidate = parent / f"{safe_stem}_v{current}_{today}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"版本号超过上限 {_MAX_VERSION}，请清理输出目录：{parent}")
