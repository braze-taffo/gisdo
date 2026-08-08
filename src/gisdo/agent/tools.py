"""Agent 工具注册表：把 engine 的 ops 包装成 OpenAI function-calling 工具。

每个工具 = OpenAI schema + Python handler。handler 调对应 ``ops.*``，
写操作用循环已确认的 :class:`Alignment`（经 ``ctx.confirmed_alignment``），
失败时返回 :class:`FailureRecord` 摘要供 LLM 自恢复。安全规则在这里与 ops 双层强制。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gisdo.agent.prompt import format_tool_focus, format_tool_inventory
from gisdo.engine import ops
from gisdo.engine import preflight as preflight_mod
from gisdo.engine import runtime as runtime_mod
from gisdo.engine.alignment import Alignment, build_draft
from gisdo.engine.failure import FailureRecord
from gisdo.engine.runner import RunCancelled, ScriptResult
from gisdo.engine.versioning import versioned_file, versioned_path

# --------------------------------------------------------------------------- #
# 上下文与异常
# --------------------------------------------------------------------------- #


class ToolError(Exception):
    """工具可预期的失败（如未选定运行时、参数缺失），转成结果字符串喂回 LLM。"""


@dataclass
class ToolContext:
    """工具执行上下文，由 Agent 循环填充。"""

    modern_runtime: Any = None  # Runtime | None
    arcmap_runtime: Any = None
    cancel: Any = None  # threading.Event
    confirmed_alignment: Alignment | None = None
    on_log: Callable[[str], None] = lambda _s: None
    on_ask_user: Callable[[str, list[str]], str | None] = lambda _q, _o: None
    project: Any = None  # GisProject | None

    def modern(self):
        if self.modern_runtime is None:
            raise ToolError("未选定 GeoScene/Pro 运行时。请先调用 discover_runtimes 并在设置里选定。")
        return self.modern_runtime

    def arcmap(self):
        if self.arcmap_runtime is None:
            raise ToolError("未选定 ArcMap Python 2.7 运行时。请先在设置里指定 ArcMap 解释器。")
        return self.arcmap_runtime

    def stream(self):
        return self.on_log if self.on_log else None

    def project_dir(self) -> str:
        """当前项目文件夹；未设项目或未配置则抛 ToolError。"""
        if self.project is None or not getattr(self.project, "project_dir", ""):
            raise ToolError("未设置项目文件夹。请先在「项目」页配置当前项目。")
        return self.project.project_dir

    def map_output_dir(self) -> str:
        """当前地图输出文件夹；未设项目或未配置则抛 ToolError。"""
        if self.project is None or not getattr(self.project, "map_output_dir", ""):
            raise ToolError("未设置地图输出文件夹。请先在「项目」页配置当前项目。")
        return self.project.map_output_dir


# --------------------------------------------------------------------------- #
# 结果格式化
# --------------------------------------------------------------------------- #

_MAX_RESULT = 4000


def _truncate(text: str) -> str:
    if len(text) <= _MAX_RESULT:
        return text
    half = _MAX_RESULT // 2
    return text[:half] + f"\n…[已截断，共 {len(text)} 字符]…\n" + text[-half:]


def _format_result(result: ScriptResult, output_path: str | None = None) -> str:
    if result.ok:
        if result.json is not None:
            return _truncate(json.dumps(result.json, ensure_ascii=False))
        return _truncate(result.stdout or "(无输出)")
    rec = FailureRecord.from_result(result, output_path=output_path)
    return _truncate(rec.format_report())


def _require(args: dict, key: str):
    if key not in args or args[key] in (None, ""):
        raise ToolError(f"缺少必填参数：{key}")
    return args[key]


# --------------------------------------------------------------------------- #
# 工具定义
# --------------------------------------------------------------------------- #


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    is_write: bool = False
    handler: Callable[[dict, ToolContext], str] = field(default=lambda _a, _c: "")
    prepare_alignment: Callable[[dict, ToolContext], Alignment | None] | None = None
    normalize_args: Callable[[dict, ToolContext], None] | None = None

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def call(self, args: dict, ctx: ToolContext) -> str:
        try:
            return self.handler(args, ctx)
        except ToolError as exc:
            return f"工具 {self.name} 无法执行：{exc}"
        except RunCancelled:
            return f"工具 {self.name} 已被取消。"
        except Exception as exc:  # noqa: BLE001 - 兜底，避免单工具崩溃拖垮循环
            return f"工具 {self.name} 异常：{type(exc).__name__}: {exc}"


# --- 只读工具 --------------------------------------------------------------- #


def _h_discover_runtimes(_args, ctx):
    discovery = runtime_mod.list_runtimes()
    data = {
        "modern_candidates": discovery.modern_candidates,
        "legacy_arcmap_candidates": discovery.legacy_arcmap_candidates,
        "has_modern": bool(discovery.modern_candidates),
        "has_arcmap": bool(discovery.legacy_arcmap_candidates),
    }
    return _truncate(json.dumps(data, ensure_ascii=False))


def _h_probe_runtime(args, ctx):
    python = args.get("python") or (ctx.modern_runtime.python if ctx.modern_runtime else None)
    if not python:
        raise ToolError("未提供 python 路径，也未选定现代运行时。")
    probe = runtime_mod.probe(python)
    return _truncate(json.dumps(probe, ensure_ascii=False))


def _h_list_gis_tools(args, ctx):
    modern = ctx.modern()
    tool = args.get("tool")
    if tool:
        # 聚焦单工具：实时查询完整参数表（权威），无需缓存。
        result = ops.list_gis_tools(modern, tool=tool)
        return _truncate(format_tool_focus((result.json or {}).get("focus") or {}))
    toolboxes = args.get("toolboxes")
    if isinstance(toolboxes, list) and toolboxes:
        clean = [str(t) for t in toolboxes if str(t).strip()]
        return _h_gis_tools_listing(modern, clean)
    return _h_gis_tools_listing(modern, None)


_SNAPSHOT_NOTE = (
    "（以下为 24h 内缓存快照，通常够用；如需某工具的最新完整参数表，"
    "用 tool 传 toolbox.tool 实时聚焦查询。）\n"
)


def _h_gis_tools_listing(modern, toolboxes: list[str] | None) -> str:
    """默认/限定工具箱模式：优先缓存快照（快）；无缓存才实时全量扫描并回写缓存。"""
    cached = ops.read_gis_tools_cache(modern.python)
    if cached is not None:
        if toolboxes is None:
            return _truncate(_SNAPSHOT_NOTE + format_tool_inventory(cached))
        filtered = {"toolboxes": {tb: cached["toolboxes"][tb] for tb in toolboxes
                                  if tb in (cached.get("toolboxes") or {})}}
        return _truncate(_SNAPSHOT_NOTE + format_tool_inventory(filtered))
    kwargs = {} if toolboxes is None else {"toolboxes": tuple(toolboxes)}
    result = ops.list_gis_tools(modern, **kwargs)
    data = result.json
    if not data or not data.get("toolboxes"):
        return _format_result(result)
    if toolboxes is None:  # 全量实时扫描，回写缓存供后续注入/查询
        ops.write_gis_tools_cache(modern.python, data)
    return _truncate(format_tool_inventory(data))


def build_tool_inventory(modern_runtime, *, on_log: Callable[[str], None] | None = None) -> str | None:
    """拉取白名单工具箱的已装工具清单并格式化为提示词片段；失败/无运行时时返回 None。

    优先读本地 TTL 缓存（全量扫描一次 arcpy 需几十秒），缓存缺失/过期才重新扫描。
    """
    if modern_runtime is None:
        return None
    cached = ops.read_gis_tools_cache(modern_runtime.python)
    if cached is not None:
        return format_tool_inventory(cached)
    try:
        result = ops.list_gis_tools(modern_runtime, on_output=on_log)
    except Exception:  # noqa: BLE001 - 注入失败不应阻断会话
        return None
    data = result.json
    if not data or not data.get("toolboxes"):
        return None
    ops.write_gis_tools_cache(modern_runtime.python, data)
    return format_tool_inventory(data)


def _h_inspect_aprx(args, ctx):
    project = _require(args, "project")
    result = ops.inspect_aprx(ctx.modern(), project, on_output=ctx.stream())
    return _format_result(result)


def _h_inspect_gdb(args, ctx):
    workspace = _require(args, "workspace")
    result = ops.inspect_gdb(ctx.modern(), workspace, skip_counts=bool(args.get("skip_counts")),
                             on_output=ctx.stream())
    return _format_result(result)


def _h_inspect_mxd(args, ctx):
    mxd = _require(args, "mxd")
    result = ops.inspect_mxd(ctx.arcmap(), mxd, on_output=ctx.stream())
    return _format_result(result)


def _h_inspect_legacy_dataset(args, ctx):
    dataset = _require(args, "dataset")
    result = ops.inspect_legacy_dataset(ctx.arcmap(), dataset, on_output=ctx.stream())
    return _format_result(result)


def _h_preflight(args, ctx):
    output = _require(args, "output")
    project = args.get("project")
    inventory = None
    gdb_roots: list[str] = []
    if project:
        inv = ops.inspect_aprx(ctx.modern(), project)
        if inv.json:
            inventory = inv.json
            for src in inventory.get("data_sources", []) or []:
                low = str(src).lower().replace("\\", "/")
                idx = low.find(".gdb")
                if idx >= 0:
                    root = str(src)[: idx + 4]
                    if root not in gdb_roots:
                        gdb_roots.append(root)
    report = preflight_mod.preflight(
        runtime=ctx.modern(), output_path=output, inventory=inventory,
        gdb_roots=gdb_roots, allow_broken=bool(args.get("allow_broken")),
    )
    return _truncate(report.format())


def _h_verify_png(args, ctx):
    png = _require(args, "png")
    result = ops.verify_png(png, on_output=ctx.stream())
    return _format_result(result, output_path=png)


def _h_list_dir(args, ctx):
    path = _require(args, "path")
    p = Path(path)
    if not p.is_dir():
        raise ToolError(f"不是目录：{path}")
    entries = []
    for child in sorted(p.iterdir()):
        try:
            st = child.stat()
            kind = "D" if child.is_dir() else "F"
            entries.append(f"{kind} {st.st_size:>12} {child.name}")
        except OSError:
            entries.append(f"? {child.name}")
    return _truncate("\n".join(entries) or "(空目录)")


def _h_read_file(args, ctx):
    path = _require(args, "path")
    max_chars = int(args.get("max_chars", 8000))
    p = Path(path)
    if not p.is_file():
        raise ToolError(f"不是文件：{path}")
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ToolError(f"读取失败：{exc}") from exc
    return _truncate(text[:max_chars]) + (f"\n…[共 {len(text)} 字符]" if len(text) > max_chars else "")


def _h_ask_user(args, ctx):
    question = _require(args, "question")
    options = args.get("options")
    if options is None:
        options = []
    if not isinstance(options, list):
        raise ToolError("options 必须是字符串数组（可选）。")
    clean = [str(o) for o in options[:6] if str(o).strip()]
    answer = ctx.on_ask_user(question, clean)
    if answer is None:
        return "用户未回答（可能取消了对话框或当前无交互式前端）。请按最佳判断继续，或改用方案 B 并如实说明。"
    return f"用户回答：{answer}"


# --- 写工具 ----------------------------------------------------------------- #


def _al_draft(args, ctx, *, project, output, will_create, classification="", output_format=""):
    return build_draft(
        project=project,
        output_location=output,
        will_create=will_create,
        classification_field=classification,
        output_format=output_format,
        desktop_authorized=False,
    )


def _h_extract_data(args, ctx):
    project = _require(args, "project")
    out = _require(args, "output_dir")
    if ctx.confirmed_alignment is None:
        raise ToolError("写操作缺少已确认的对齐块。")
    result = ops.extract_data(
        ctx.modern(), project, out, alignment=ctx.confirmed_alignment,
        skip_hashes=bool(args.get("skip_hashes")), on_output=ctx.stream(),
    )
    return _format_result(result, output_path=out)


def _al_extract(args, ctx):
    return _al_draft(args, ctx, project=args.get("project", ""), output=args.get("output_dir", ""),
                     will_create=["extraction_manifest.json", "workspaces/", "files/"])


def _h_package_project(args, ctx):
    project = _require(args, "project")
    out = _require(args, "output_ppkx")
    if ctx.confirmed_alignment is None:
        raise ToolError("写操作缺少已确认的对齐块。")
    result = ops.package_project(
        ctx.modern(), project, out, alignment=ctx.confirmed_alignment,
        allow_broken=bool(args.get("allow_broken")),
        summary=args.get("summary", "Portable GeoScene project package"),
        tags=args.get("tags", "GeoScene;GIS;ArcPy"),
        on_output=ctx.stream(),
    )
    return _format_result(result, output_path=out)


def _al_package(args, ctx):
    out = args.get("output_ppkx", "")
    return _al_draft(args, ctx, project=args.get("project", ""), output=out,
                     will_create=[Path(out).name] if out else [])


def _h_validate_package(args, ctx):
    package = _require(args, "package")
    out = _require(args, "output_dir")
    if ctx.confirmed_alignment is None:
        raise ToolError("写操作缺少已确认的对齐块。")
    result = ops.validate_package(
        ctx.modern(), package, out, alignment=ctx.confirmed_alignment,
        source_aprx=args.get("source_aprx"), on_output=ctx.stream(),
    )
    return _format_result(result, output_path=out)


def _al_validate(args, ctx):
    return _al_draft(args, ctx, project=args.get("package", ""), output=args.get("output_dir", ""),
                     will_create=["validation_report.json", "提取出的 APRX"])


def _h_export_legacy_lines(args, ctx):
    dataset = _require(args, "dataset")
    value_field = _require(args, "value_field")
    out = _require(args, "output_json")
    if ctx.confirmed_alignment is None:
        raise ToolError("写操作缺少已确认的对齐块。")
    result = ops.export_legacy_lines(
        ctx.arcmap(), dataset, value_field, out, alignment=ctx.confirmed_alignment,
        skip_invalid=bool(args.get("skip_invalid")), on_output=ctx.stream(),
    )
    return _format_result(result, output_path=out)


def _al_export_lines(args, ctx):
    out = args.get("output_json", "")
    return _al_draft(args, ctx, project=args.get("dataset", ""), output=out,
                     will_create=[Path(out).name] if out else [])


def _h_render_classified(args, ctx):
    input_json = _require(args, "input_json")
    out_png = _require(args, "output_png")
    breaks = _require(args, "breaks")
    if not isinstance(breaks, list) or not breaks:
        raise ToolError("breaks 必须是非空数值数组。")
    if ctx.confirmed_alignment is None:
        raise ToolError("写操作缺少已确认的对齐块。")
    options = ops.RenderOptions(
        breaks=[float(b) for b in breaks],
        colors=args.get("colors"),
        labels=args.get("labels"),
        title=args.get("title", ""),
        legend_title=args.get("legend_title", ""),
        width=float(args.get("width", 10.0)),
        height=float(args.get("height", 6.5)),
        dpi=int(args.get("dpi", 300)),
        line_width=float(args.get("line_width", 1.1)),
    )
    result = ops.render_classified(
        input_json, out_png, options, alignment=ctx.confirmed_alignment, on_output=ctx.stream(),
    )
    return _format_result(result, output_path=out_png)


def _al_render(args, ctx):
    out_png = args.get("output_png", "")
    breaks = args.get("breaks", [])
    return _al_draft(args, ctx, project=args.get("input_json", ""), output=out_png,
                     will_create=[out_png] if out_png else [],
                     classification=f"breaks={breaks}",
                     output_format=f"PNG {args.get('width',10)}x{args.get('height',6.5)}in @{args.get('dpi',300)}dpi")


def _h_run_geoprocessing(args, ctx):
    tool = _require(args, "tool")
    params = args.get("params")
    if not isinstance(params, dict):
        raise ToolError('params 必须是参数名→值的对象，如 {"in_features": "E:/a.shp", "clip_features": "E:/b.shp"}。')
    output = _require(args, "output")
    try:
        toolbox = ops.gp_toolbox(tool)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    if toolbox not in ops.GP_ALLOWED_TOOLBOXES:
        raise ToolError(
            f"工具箱 {toolbox!r} 不在白名单（允许：{'、'.join(ops.GP_ALLOWED_TOOLBOXES)}）。"
            "请用 toolbox.tool 全名，如 analysis.Clip、management.Project。"
        )
    if ctx.confirmed_alignment is None:
        raise ToolError("写操作缺少已确认的对齐块。")
    result = ops.run_geoprocessing(
        ctx.modern(), tool, params, output, alignment=ctx.confirmed_alignment,
        output_param=args.get("output_param"), check_field=args.get("check_field"),
        on_output=ctx.stream(),
    )
    return _format_result(result, output_path=output)


def _al_gp(args, ctx):
    out = args.get("output", "")
    tool = args.get("tool", "")
    params = args.get("params") or {}
    reads = [str(v) for v in params.values() if isinstance(v, str) and v] if isinstance(params, dict) else []
    return Alignment(
        authoritative_project="（无，输入数据集见拟读取内容）",
        data_root="（直接地理处理）",
        external_dependencies=reads,
        will_read=reads,
        will_create=[Path(out).name] if out else [],
        will_not_modify=["所有输入数据集与源文件"],
        output_location=out,
        classification_field_and_rules="（无分类）",
        output_format_and_size=f"geoprocessing（{tool}）",
        desktop_authorized=False,
        notes=f"geoprocessing: {tool}",
    )


# --- 默认输出落点：写操作省略输出时补到当前项目地图输出文件夹 ---


def _fill_dir(args: dict, ctx: ToolContext, param: str, base: str) -> None:
    """目录型输出：省略时补 ``versioned_path(map_output_dir, base)`` 子目录。"""
    if not args.get(param):
        args[param] = str(versioned_path(ctx.map_output_dir(), base))


def _fill_file(args: dict, ctx: ToolContext, param: str, stem: str, suffix: str) -> None:
    """文件型输出：省略时补 ``versioned_file(map_output_dir, stem, suffix)``。"""
    if not args.get(param):
        args[param] = str(versioned_file(ctx.map_output_dir(), stem, suffix))


def _norm_extract(args, ctx):
    _fill_dir(args, ctx, "output_dir", "extract")


def _norm_package(args, ctx):
    _fill_file(args, ctx, "output_ppkx", "package", ".ppkx")


def _norm_validate(args, ctx):
    _fill_dir(args, ctx, "output_dir", "validate")


def _norm_export(args, ctx):
    _fill_file(args, ctx, "output_json", "legacy_lines", ".json")


def _norm_render(args, ctx):
    _fill_file(args, ctx, "output_png", "render", ".png")


def _norm_gp(args, ctx):
    base = (args.get("output_param") or args.get("tool") or "output").split(".")[-1]
    _fill_file(args, ctx, "output", base, "")


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #

_TOOLS: list[Tool] = [
    Tool(
        name="discover_runtimes",
        description="只读：发现本机 GeoScene/Pro 与 ArcMap Python 候选运行时。无需参数。",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_h_discover_runtimes,
    ),
    Tool(
        name="probe_runtime",
        description="只读：探测指定 Python 解释器的 arcpy 版本/产品级/扩展/工具数。不填 python 则用已选定的现代运行时。",
        parameters={"type": "object", "properties": {"python": {"type": "string"}}, "required": []},
        handler=_h_probe_runtime,
    ),
    Tool(
        name="list_gis_tools",
        description="只读：列出本机白名单工具箱（management/analysis/conversion）已装官方工具与官方参数名。模型必须用这里的参数名，不要猜。不填参数返回全部（24h 内缓存快照，秒回）；传 toolboxes 限定工具箱（同走快照）；传 tool（toolbox.tool 全名，如 management.Project）实时聚焦单个工具的完整参数表（含可选参数与类型，约 20-40s）。",
        parameters={"type": "object",
                    "properties": {
                        "toolboxes": {"type": "array", "items": {"type": "string"},
                                      "description": "可选：限定工具箱别名数组，如 [\"management\", \"analysis\"]"},
                        "tool": {"type": "string",
                                 "description": "可选：聚焦单个工具完整参数表，如 \"management.Project\""},
                    },
                    "required": []},
        handler=_h_list_gis_tools,
    ),
    Tool(
        name="inspect_aprx",
        description="只读：清点 APRX 的地图/布局/图层/表/数据源/断裂源。用 GeoScene/Pro 运行时。",
        parameters={"type": "object", "properties": {"project": {"type": "string"}},
                    "required": ["project"]},
        handler=_h_inspect_aprx,
    ),
    Tool(
        name="inspect_gdb",
        description="只读：清点文件 GDB 的要素类/表/栅格/计数/几何类型/空间参考。",
        parameters={"type": "object",
                    "properties": {"workspace": {"type": "string"}, "skip_counts": {"type": "boolean"}},
                    "required": ["workspace"]},
        handler=_h_inspect_gdb,
    ),
    Tool(
        name="inspect_mxd",
        description="只读：清点遗留 ArcMap MXD。必须已选定 ArcMap 运行时。",
        parameters={"type": "object", "properties": {"mxd": {"type": "string"}}, "required": ["mxd"]},
        handler=_h_inspect_mxd,
    ),
    Tool(
        name="inspect_legacy_dataset",
        description="只读：诊断 ArcMap 数据集（字段/CRS/extent/锁/cursor 探针）。用 ArcMap 运行时。",
        parameters={"type": "object", "properties": {"dataset": {"type": "string"}},
                    "required": ["dataset"]},
        handler=_h_inspect_legacy_dataset,
    ),
    Tool(
        name="preflight",
        description="只读：写前预检（运行时可用/输出路径不存在/无活动锁/无断裂源）。给 project 会顺带读取数据源。",
        parameters={"type": "object",
                    "properties": {"project": {"type": "string"}, "output": {"type": "string"},
                                   "allow_broken": {"type": "boolean"}},
                    "required": ["output"]},
        handler=_h_preflight,
    ),
    Tool(
        name="verify_png",
        description="只读：像素校验 PNG，拒绝空白或近空白导出。出图后务必调用。",
        parameters={"type": "object", "properties": {"png": {"type": "string"}}, "required": ["png"]},
        handler=_h_verify_png,
    ),
    Tool(
        name="list_dir",
        description="只读：列出目录内容（F=文件/D=目录+大小+名）。仅用于查看与任务直接相关的输入/输出目录；不要翻无关目录。",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        handler=_h_list_dir,
    ),
    Tool(
        name="read_file",
        description="只读：读取文本文件内容（如 manifest/report JSON），默认截断到 8000 字符。",
        parameters={"type": "object",
                    "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer"}},
                    "required": ["path"]},
        handler=_h_read_file,
    ),
    Tool(
        name="ask_user",
        description="交互：向用户提问并等待选择，返回用户回答。用户未明确指定所有必要信息、任务有歧义、或需用户在多个方案/范围/格式/坐标系间拍板时，必须先用本工具与用户对齐一轮。question 是问题，options 是 2-6 个候选答案（可省略让用户自由输入）。只有用户已明确指定所有必要信息时才可不问；用户未回答时按最佳判断继续并如实说明。",
        parameters={"type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "options": {"type": "array", "items": {"type": "string"},
                                    "description": "可选：候选答案，最多 6 个"},
                    },
                    "required": ["question"]},
        handler=_h_ask_user,
    ),
    # 写操作
    Tool(
        name="extract_data",
        description="写：把 APRX 引用的本地数据复制到新版本化目录并校验（文件数/字节/SHA-256）。output_dir 可省略，默认落到当前项目地图输出文件夹。需先对齐确认。",
        parameters={"type": "object",
                    "properties": {"project": {"type": "string"},
                                   "output_dir": {"type": "string",
                                                  "description": "输出目录；省略则默认落到当前项目地图输出文件夹"},
                                   "skip_hashes": {"type": "boolean"}},
                    "required": ["project"]},
        is_write=True, handler=_h_extract_data, prepare_alignment=_al_extract,
        normalize_args=_norm_extract,
    ),
    Tool(
        name="package_project",
        description="写：用官方 PackageProject 打包为 PPKX。断裂源默认拒绝，除非 allow_broken=true。output_ppkx 可省略，默认落到当前项目地图输出文件夹。需先对齐确认。",
        parameters={"type": "object",
                    "properties": {"project": {"type": "string"},
                                   "output_ppkx": {"type": "string",
                                                   "description": "PPKX 路径；省略则默认落到当前项目地图输出文件夹"},
                                   "summary": {"type": "string"}, "tags": {"type": "string"},
                                   "allow_broken": {"type": "boolean"}},
                    "required": ["project"]},
        is_write=True, handler=_h_package_project, prepare_alignment=_al_package,
        normalize_args=_norm_package,
    ),
    Tool(
        name="validate_package",
        description="写：用官方 ExtractPackage 校验 PPKX 并重开提取出的 APRX。output_dir 可省略，默认落到当前项目地图输出文件夹。需先对齐确认。",
        parameters={"type": "object",
                    "properties": {"package": {"type": "string"},
                                   "output_dir": {"type": "string",
                                                  "description": "输出目录；省略则默认落到当前项目地图输出文件夹"},
                                   "source_aprx": {"type": "string"}},
                    "required": ["package"]},
        is_write=True, handler=_h_validate_package, prepare_alignment=_al_validate,
        normalize_args=_norm_validate,
    ),
    Tool(
        name="export_legacy_lines",
        description="写：把 ArcMap 可读的折线几何与一个数值字段导出为可移植 JSON 桥。output_json 可省略，默认落到当前项目地图输出文件夹。用 ArcMap 运行时。需先对齐确认。",
        parameters={"type": "object",
                    "properties": {"dataset": {"type": "string"}, "value_field": {"type": "string"},
                                   "output_json": {"type": "string",
                                                   "description": "JSON 路径；省略则默认落到当前项目地图输出文件夹"},
                                   "skip_invalid": {"type": "boolean"}},
                    "required": ["dataset", "value_field"]},
        is_write=True, handler=_h_export_legacy_lines, prepare_alignment=_al_export_lines,
        normalize_args=_norm_export,
    ),
    Tool(
        name="render_classified",
        description="写：把分类线 JSON 渲染为新 PNG（Matplotlib，无需 arcpy）。breaks 必填。output_png 可省略，默认落到当前项目地图输出文件夹。出图后请再调 verify_png。需先对齐确认。",
        parameters={"type": "object",
                    "properties": {"input_json": {"type": "string"},
                                   "output_png": {"type": "string",
                                                  "description": "PNG 路径；省略则默认落到当前项目地图输出文件夹"},
                                   "breaks": {"type": "array", "items": {"type": "number"}},
                                   "colors": {"type": "array", "items": {"type": "string"}},
                                   "labels": {"type": "array", "items": {"type": "string"}},
                                   "title": {"type": "string"}, "legend_title": {"type": "string"},
                                   "width": {"type": "number"}, "height": {"type": "number"},
                                   "dpi": {"type": "integer"}, "line_width": {"type": "number"}},
                    "required": ["input_json", "breaks"]},
        is_write=True, handler=_h_render_classified, prepare_alignment=_al_render,
        normalize_args=_norm_render,
    ),
    Tool(
        name="run_geoprocessing",
        description="写：运行白名单内官方 ArcPy 地理处理工具（工具箱 management/analysis/conversion，如 analysis.Clip 裁剪、management.Project 投影转换、analysis.Buffer、analysis.Intersect、management.RepairGeometry、management.CalculateField）。tool 用 toolbox.tool 全名；params 按官方参数名给值（输入路径须已存在）；output 是主输出路径，可省略，默认落到当前项目地图输出文件夹（脚本会拒绝覆盖）；可选 output_param 指定主输出参数名，check_field 对输出该字段做 null/极值校验。自动校验输出存在/CRS/要素数/extent。需先对齐确认。注意：输出路径的父目录会自动创建，无需先用 CreateFolder/新建目录。",
        parameters={"type": "object",
                    "properties": {
                        "tool": {"type": "string", "description": "toolbox.tool 全名，如 analysis.Clip / management.Project"},
                        "params": {"type": "object", "additionalProperties": {},
                                   "description": "官方参数名→值，如 {\"in_features\":\"E:/a.shp\",\"clip_features\":\"E:/b.shp\"}"},
                        "output": {"type": "string",
                                   "description": "主输出路径；省略则默认落到当前项目地图输出文件夹"},
                        "output_param": {"type": "string",
                                         "description": "可选，极少需要：主输出对应的参数名。脚本会按工具签名自动识别，不要猜参数名，猜错会自动回退。"},
                        "check_field": {"type": "string"},
                    },
                    "required": ["tool", "params"]},
        is_write=True, handler=_h_run_geoprocessing, prepare_alignment=_al_gp,
        normalize_args=_norm_gp,
    ),
]


class ToolRegistry:
    """工具注册表：按名查找、导出 OpenAI tools schema。"""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools = {t.name: t for t in (tools or _TOOLS)}

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def as_openai_tools(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]


def default_registry() -> ToolRegistry:
    return ToolRegistry()


__all__ = ["Tool", "ToolContext", "ToolError", "ToolRegistry", "default_registry"]
