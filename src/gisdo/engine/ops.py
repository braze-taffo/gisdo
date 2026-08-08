"""面向上层的高层操作。

每个操作选用正确的运行时与脚本，派发执行并返回 :class:`ScriptResult`。
写操作（提取/打包/校验/导出/出图）必须传入已确认的 :class:`Alignment`，
并在派发前用 :mod:`gisdo.engine.safety` 校验输出路径与不变量。
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from gisdo.engine import runtime as runtime_mod
from gisdo.engine.alignment import Alignment
from gisdo.engine.runner import RunCancelled, ScriptResult, run_script
from gisdo.engine.safety import (
    SafetyError,
    assert_absent,
    assert_no_broken_sources,
)

StreamCallback = Callable[[str], None] | None

# 通用地理处理白名单（tool-routing.md 前几族）：仅这些工具箱的官方工具可经
# run_geoprocessing 执行。脚本端 geoprocessing.py 再校验一次（defense-in-depth）。
GP_ALLOWED_TOOLBOXES = ("management", "analysis", "conversion")


# 工具清单缓存：全量扫描一次 arcpy 要几十秒（许可 checkout + 510 个工具 GetParameterInfo），
# 注入用 TTL 缓存避免每个会话都重扫；list_gis_tools 工具本身保持实时（权威来源）。
GIS_TOOLS_CACHE_TTL_S = 24 * 3600
_gis_tools_cache_lock = threading.Lock()


def gis_tools_cache_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local"))
    return root / "gisdo" / "gis_tools_cache.json"


def read_gis_tools_cache(runtime_python: str, *, now: float | None = None) -> dict | None:
    """读缓存：运行时路径匹配且未过期才返回工具清单数据（不含包装字段）。"""
    with _gis_tools_cache_lock:
        path = gis_tools_cache_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if data.get("runtime") != runtime_python:
            return None
        if (now if now is not None else time.time()) - data.get("generated_at", 0) > GIS_TOOLS_CACHE_TTL_S:
            return None
        body = data.get("data")
        return body if isinstance(body, dict) and body.get("toolboxes") else None


def write_gis_tools_cache(runtime_python: str, data: dict, *, now: float | None = None) -> None:
    """写缓存；失败静默（注入失败不应阻断会话）。"""
    with _gis_tools_cache_lock:
        path = gis_tools_cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "runtime": runtime_python,
                "generated_at": now if now is not None else time.time(),
                "data": data,
            }
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def gp_toolbox(tool: str) -> str:
    """解析工具名的 toolbox 前缀；格式不符抛 ValueError。"""
    if not tool or "." not in tool:
        raise ValueError("工具名必须是 toolbox.tool 全名，如 analysis.Clip、management.Project。")
    return tool.split(".", 1)[0]


# --------------------------------------------------------------------------- #
# 类型化结果
# --------------------------------------------------------------------------- #


@dataclass
class AprxReport:
    """APRX 检查的类型化视图，原始 JSON 仍保留在 ``raw``。"""

    raw: dict
    project: str = ""
    maps_count: int = 0
    layouts_count: int = 0
    layers_count: int = 0
    tables_count: int = 0
    broken_count: int = 0
    broken: list[dict] = field(default_factory=list)
    data_sources: list[str] = field(default_factory=list)

    @classmethod
    def from_result(cls, result: ScriptResult) -> AprxReport:
        data = result.json or {}
        return cls(
            raw=data,
            project=data.get("project", ""),
            maps_count=int(data.get("maps_count", 0) or 0),
            layouts_count=int(data.get("layouts_count", 0) or 0),
            layers_count=int(data.get("layers_count", 0) or 0),
            tables_count=int(data.get("tables_count", 0) or 0),
            broken_count=int(data.get("broken_count", 0) or 0),
            broken=list(data.get("broken", []) or []),
            data_sources=list(data.get("data_sources", []) or []),
        )


@dataclass
class RenderOptions:
    """``render_classified_lines.py`` 的渲染参数。"""

    breaks: list[float]
    colors: list[str] | None = None
    labels: list[str] | None = None
    title: str = ""
    legend_title: str = ""
    scale_bar: float | None = None
    scale_label: str | None = None
    output_pdf: str | None = None
    report: str | None = None
    axis_km: bool = False
    no_north_arrow: bool = False
    no_grid: bool = False
    line_width: float = 1.1
    width: float = 10.0
    height: float = 6.5
    dpi: int = 300

    def to_args(self, input_json: str, output_png: str) -> list[str]:
        args = [
            input_json,
            output_png,
            "--breaks", ",".join(f"{b:g}" for b in self.breaks),
            "--line-width", str(self.line_width),
            "--width", str(self.width),
            "--height", str(self.height),
            "--dpi", str(self.dpi),
        ]
        if self.colors:
            args += ["--colors", ",".join(self.colors)]
        if self.labels:
            args += ["--labels", "|".join(self.labels)]
        if self.title:
            args += ["--title", self.title]
        if self.legend_title:
            args += ["--legend-title", self.legend_title]
        if self.scale_bar is not None:
            args += ["--scale-bar", str(self.scale_bar)]
            if self.scale_label:
                args += ["--scale-label", self.scale_label]
        if self.output_pdf:
            args += ["--output-pdf", self.output_pdf]
        if self.report:
            args += ["--report", self.report]
        if self.axis_km:
            args.append("--axis-km")
        if self.no_north_arrow:
            args.append("--no-north-arrow")
        if self.no_grid:
            args.append("--no-grid")
        return args


# --------------------------------------------------------------------------- #
# 只读检查
# --------------------------------------------------------------------------- #


def inspect_aprx(modern_runtime, project: str, *, on_output: StreamCallback = None,
                 on_stderr: StreamCallback = None, cancel: threading.Event | None = None) -> ScriptResult:
    """只读清点 APRX：地图/布局/图层/表/数据源/断裂源。"""
    return run_script(
        modern_runtime.python,
        "inspect_aprx.py",
        [project],
        is_py2=modern_runtime.is_py2,
        on_stdout=on_output,
        on_stderr=on_stderr,
        cancel=cancel,
    )


def inspect_gdb(modern_runtime, workspace: str, *, skip_counts: bool = False,
                on_output: StreamCallback = None, on_stderr: StreamCallback = None,
                cancel: threading.Event | None = None) -> ScriptResult:
    """只读清点文件 GDB / 工作空间。"""
    args = [workspace]
    if skip_counts:
        args.append("--skip-counts")
    return run_script(
        modern_runtime.python,
        "inspect_gdb.py",
        args,
        is_py2=modern_runtime.is_py2,
        on_stdout=on_output,
        on_stderr=on_stderr,
        cancel=cancel,
    )


def inspect_mxd(arcmap_runtime, mxd_path: str, *, on_output: StreamCallback = None,
                on_stderr: StreamCallback = None, cancel: threading.Event | None = None) -> ScriptResult:
    """只读清点遗留 ArcMap MXD。必须用 ArcMap Python 2.7。"""
    return run_script(
        arcmap_runtime.python,
        "inspect_mxd_legacy.py",
        [mxd_path],
        is_py2=True,
        on_stdout=on_output,
        on_stderr=on_stderr,
        cancel=cancel,
    )


def inspect_legacy_dataset(arcmap_runtime, dataset: str, *, on_output: StreamCallback = None,
                           on_stderr: StreamCallback = None,
                           cancel: threading.Event | None = None) -> ScriptResult:
    """只读诊断 ArcMap 数据集：字段/CRS/extent/锁/cursor 探针。"""
    return run_script(
        arcmap_runtime.python,
        "inspect_legacy_dataset.py",
        [dataset],
        is_py2=True,
        on_stdout=on_output,
        on_stderr=on_stderr,
        cancel=cancel,
    )


def verify_png(png_path: str, *, on_output: StreamCallback = None,
               on_stderr: StreamCallback = None, cancel: threading.Event | None = None) -> ScriptResult:
    """像素校验 PNG：拒绝空白或近空白导出。用应用 Python（无需 arcpy）。"""
    return run_script(
        runtime_mod.APP_PYTHON,
        "verify_png.py",
        [png_path],
        on_stdout=on_output,
        on_stderr=on_stderr,
        cancel=cancel,
    )


# --------------------------------------------------------------------------- #
# 写操作（需对齐确认）
# --------------------------------------------------------------------------- #


def extract_data(modern_runtime, project: str, output_dir: str, *,
                 alignment: Alignment, skip_hashes: bool = False,
                 on_output: StreamCallback = None, on_stderr: StreamCallback = None,
                 cancel: threading.Event | None = None) -> ScriptResult:
    """把 APRX 引用的本地数据复制到新版本化目录并校验。"""
    alignment.require_confirmed()
    resolved = assert_absent(output_dir)
    args = [project, str(resolved)]
    if skip_hashes:
        args.append("--skip-hashes")
    return run_script(
        modern_runtime.python,
        "extract_project_data.py",
        args,
        is_py2=modern_runtime.is_py2,
        on_stdout=on_output,
        on_stderr=on_stderr,
        cancel=cancel,
    )


def package_project(modern_runtime, project: str, output_ppkx: str, *,
                    alignment: Alignment, inventory: dict | None = None,
                    allow_broken: bool = False, summary: str = "Portable GeoScene project package",
                    tags: str = "GeoScene;GIS;ArcPy",
                    on_output: StreamCallback = None, on_stderr: StreamCallback = None,
                    cancel: threading.Event | None = None) -> ScriptResult:
    """用官方 PackageProject 创建新 PPKX。断裂源默认拒绝，除非显式放行。"""
    alignment.require_confirmed()
    if inventory is not None:
        assert_no_broken_sources(inventory, allow=allow_broken)
    resolved = assert_absent(output_ppkx)
    args = [project, str(resolved), "--summary", summary, "--tags", tags]
    if allow_broken:
        args.append("--allow-broken")
    return run_script(
        modern_runtime.python,
        "package_project.py",
        args,
        is_py2=modern_runtime.is_py2,
        on_stdout=on_output,
        on_stderr=on_stderr,
        cancel=cancel,
    )


def validate_package(modern_runtime, package: str, output_dir: str, *,
                     alignment: Alignment, source_aprx: str | None = None,
                     on_output: StreamCallback = None, on_stderr: StreamCallback = None,
                     cancel: threading.Event | None = None) -> ScriptResult:
    """用官方 ExtractPackage 校验 PPKX，并重开提取出的 APRX。"""
    alignment.require_confirmed()
    resolved = assert_absent(output_dir)
    args = [package, str(resolved)]
    if source_aprx:
        args += ["--source-aprx", source_aprx]
    return run_script(
        modern_runtime.python,
        "validate_package.py",
        args,
        is_py2=modern_runtime.is_py2,
        on_stdout=on_output,
        on_stderr=on_stderr,
        cancel=cancel,
    )


def export_legacy_lines(arcmap_runtime, dataset: str, value_field: str, output_json: str, *,
                        alignment: Alignment, skip_invalid: bool = False,
                        on_output: StreamCallback = None, on_stderr: StreamCallback = None,
                        cancel: threading.Event | None = None) -> ScriptResult:
    """把 ArcMap 可读的折线几何与一个数值字段导出为可移植 JSON 桥。"""
    alignment.require_confirmed()
    resolved = assert_absent(output_json)
    args = [dataset, value_field, str(resolved)]
    if skip_invalid:
        args.append("--skip-invalid")
    return run_script(
        arcmap_runtime.python,
        "export_legacy_lines.py",
        args,
        is_py2=True,
        on_stdout=on_output,
        on_stderr=on_stderr,
        cancel=cancel,
    )


def render_classified(input_json: str, output_png: str, options: RenderOptions, *,
                      alignment: Alignment, on_output: StreamCallback = None,
                      on_stderr: StreamCallback = None,
                      cancel: threading.Event | None = None) -> ScriptResult:
    """把分类线 JSON 渲染为新 PNG（与可选 PDF）。用应用 Python + Matplotlib。"""
    alignment.require_confirmed()
    resolved_png = assert_absent(output_png)
    if options.output_pdf:
        assert_absent(options.output_pdf)
    if options.report:
        assert_absent(options.report)
    args = options.to_args(input_json, str(resolved_png))
    return run_script(
        runtime_mod.APP_PYTHON,
        "render_classified_lines.py",
        args,
        on_stdout=on_output,
        on_stderr=on_stderr,
        cancel=cancel,
    )


def run_geoprocessing(modern_runtime, tool: str, params: dict, output: str, *,
                      alignment: Alignment, output_param: str | None = None,
                      check_field: str | None = None,
                      on_output: StreamCallback = None, on_stderr: StreamCallback = None,
                      cancel: threading.Event | None = None) -> ScriptResult:
    """运行白名单内的官方 ArcPy 地理处理工具（管理/叠加/转换），自动校验输出。

    输出路径必须不存在（``assert_absent`` + 脚本端 ``arcpy.Exists`` 双层拒绝覆盖）；
    脚本内 ``overwriteOutput=False``。参数按官方工具签名传递，结果做 CRS/要素数/extent 校验。
    """
    alignment.require_confirmed()
    toolbox = gp_toolbox(tool)
    if toolbox not in GP_ALLOWED_TOOLBOXES:
        raise SafetyError(
            f"工具箱 {toolbox!r} 不在白名单（允许：{'、'.join(GP_ALLOWED_TOOLBOXES)}），拒绝执行。"
        )
    resolved = assert_absent(output)
    args = ["--tool", tool, "--params", json.dumps(params, ensure_ascii=False),
            "--output", str(resolved)]
    if output_param:
        args += ["--output-param", output_param]
    if check_field:
        args += ["--check-field", check_field]
    return run_script(
        modern_runtime.python,
        "geoprocessing.py",
        args,
        is_py2=modern_runtime.is_py2,
        on_stdout=on_output,
        on_stderr=on_stderr,
        cancel=cancel,
    )


def list_gis_tools(modern_runtime, *, toolboxes: tuple[str, ...] = GP_ALLOWED_TOOLBOXES,
                   tool: str | None = None,
                   on_output: StreamCallback = None, on_stderr: StreamCallback = None,
                   cancel: threading.Event | None = None) -> ScriptResult:
    """只读：列出白名单工具箱的已装官方工具与参数名（供模型直接使用，不猜参数名）。

    默认列出 management/analysis/conversion 全部已装工具；传 ``tool``（toolbox.tool
    全名）时聚焦单个工具的完整参数表。
    """
    args = ["--toolboxes", ",".join(toolboxes)]
    if tool:
        args += ["--tool", tool]
    return run_script(
        modern_runtime.python,
        "list_gis_tools.py",
        args,
        is_py2=modern_runtime.is_py2,
        on_stdout=on_output,
        on_stderr=on_stderr,
        cancel=cancel,
    )


# 显式导出
__all__ = [
    "GP_ALLOWED_TOOLBOXES",
    "AprxReport",
    "RenderOptions",
    "RunCancelled",
    "ScriptResult",
    "export_legacy_lines",
    "extract_data",
    "gp_toolbox",
    "inspect_aprx",
    "inspect_gdb",
    "inspect_legacy_dataset",
    "inspect_mxd",
    "list_gis_tools",
    "package_project",
    "render_classified",
    "run_geoprocessing",
    "validate_package",
    "verify_png",
]
