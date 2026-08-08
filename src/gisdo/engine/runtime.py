"""运行时发现与探测。

封装 ``discover_geoscene.py``：用应用自身 Python 列出候选解释器，
再用候选解释器（自带 arcpy）执行 ``--inside`` 探测产品、扩展、工具箱与 Python 包。
应用 Python 不需要也不应该安装 arcpy。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

from gisdo.engine.runner import run_script

APP_PYTHON = sys.executable


@dataclass
class Runtime:
    """一个可用的 Python 运行时。"""

    python: str
    family: str = ""  # "GeoScene Pro" | "ArcGIS Pro" | "ArcMap" | "unknown"
    is_py2: bool = False
    source: str = ""  # "env" | "glob" | "explicit"
    probe: dict[str, Any] | None = field(default=None, repr=False)

    @property
    def label(self) -> str:
        if self.family and self.python:
            return f"{self.family} — {self.python}"
        return self.python


@dataclass
class Discovery:
    """一次发现的结果。"""

    modern_candidates: list[str] = field(default_factory=list)
    legacy_arcmap_candidates: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def has_any(self) -> bool:
        return bool(self.modern_candidates or self.legacy_arcmap_candidates)

    def modern_runtimes(self) -> list[Runtime]:
        return [Runtime(python=p, family="待探测", source="discover") for p in self.modern_candidates]

    def legacy_runtimes(self) -> list[Runtime]:
        return [Runtime(python=p, family="ArcMap", is_py2=True, source="discover") for p in self.legacy_arcmap_candidates]


def list_runtimes(
    *,
    on_stdout=None,
    on_stderr=None,
    env: dict[str, str] | None = None,
):
    """列出本机候选解释器（不启动 arcpy）。"""
    result = run_script(
        APP_PYTHON,
        "discover_geoscene.py",
        ["--list-only"],
        on_stdout=on_stdout,
        on_stderr=on_stderr,
        env=env,
    )
    data = result.json or {}
    return Discovery(
        modern_candidates=list(data.get("modern_candidates", [])),
        legacy_arcmap_candidates=list(data.get("legacy_arcmap_candidates", [])),
        error=None if result.ok else (result.json_error or result.stderr.strip() or "未发现候选运行时"),
    )


def probe(
    modern_python: str,
    *,
    on_stdout=None,
    on_stderr=None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """用指定 Pro 解释器探测 arcpy 产品、扩展、工具箱与 Python 包。"""
    result = run_script(
        modern_python,
        "discover_geoscene.py",
        ["--inside"],
        on_stdout=on_stdout,
        on_stderr=on_stderr,
        env=env,
    )
    if result.json:
        return result.json
    raise RuntimeError(
        f"探测运行时失败：{modern_python}\n{result.stderr.strip() or result.stdout.strip()}"
    )


def discover_first(
    *,
    on_stdout=None,
    on_stderr=None,
    env: dict[str, str] | None = None,
) -> tuple[Runtime | None, dict[str, Any] | None]:
    """让 ``discover_geoscene.py`` 自行选第一个可用 Pro 运行时并探测。

    返回 ``(runtime, probe_dict)``；无可用运行时时返回 ``(None, error_dict)``。
    """
    result = run_script(
        APP_PYTHON,
        "discover_geoscene.py",
        [],
        on_stdout=on_stdout,
        on_stderr=on_stderr,
        env=env,
    )
    data = result.json or {}
    if "runtime_family" in data and "runtime_python" in data:
        runtime = Runtime(
            python=data["runtime_python"],
            family=data.get("runtime_family", ""),
            source="discover",
        )
        runtime.probe = data
        return runtime, data
    # 失败：data 形如 {"error": ..., "legacy_arcmap_candidates": [...], "failures": [...]}
    return None, data
